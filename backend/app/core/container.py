"""Composition root: builds every adapter and service once at startup.

This is the only module that knows about concrete implementations. To add a
new source service, construct its loader here and register it with the
resolver ahead of the HTTP catch-all.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

from app.core.config import Settings
from app.domain.ports import EmbeddingProvider, Transcriber, VectorStore
from app.ingestion.browser import BrowserRenderer
from app.ingestion.chunker import Chunker
from app.ingestion.http import HttpLoader
from app.ingestion.processor import PayloadProcessor
from app.ingestion.resolver import SourceResolver
from app.ingestion.upload import UploadLoader, UploadStore
from app.ingestion.youtube import YouTubeLoader
from app.providers.embeddings.fastembed_provider import FastEmbedProvider
from app.providers.llm.health import BreakerRegistry
from app.providers.llm.openai_compatible import OpenAICompatibleClient
from app.providers.llm.registry import ModelRegistry
from app.providers.llm.router import ModelRouter
from app.providers.llm.validation import validate_pool
from app.providers.transcription.whisper import WhisperTranscriber
from app.services.chat_service import ChatService
from app.services.collection_service import CollectionService
from app.services.document_service import DocumentService
from app.services.ingestion_service import IngestionService
from app.services.retrieval_service import RetrievalService
from app.storage.db.sqlite import (
    Database,
    SqliteCollectionRepository,
    SqliteConversationRepository,
    SqliteSourceRepository,
)
from app.storage.vector.local_store import LocalHybridVectorStore

log = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    http: httpx.AsyncClient
    db: Database
    registry: ModelRegistry
    router: ModelRouter
    embeddings: EmbeddingProvider
    vector_store: VectorStore
    resolver: SourceResolver
    ingestion: IngestionService
    chat: ChatService
    collections: CollectionService
    transcriber: Transcriber | None = None
    renderer: BrowserRenderer | None = None
    background: set[asyncio.Task] = field(default_factory=set)

    @classmethod
    def build(cls, settings: Settings) -> Container:
        settings.data_dir.mkdir(parents=True, exist_ok=True)

        http = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(settings.fetch_timeout_seconds, connect=10.0),
            headers={"User-Agent": settings.user_agent, "Accept-Language": "en-US,en;q=0.9"},
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=8),
        )

        # ---- model pool ----
        registry = ModelRegistry.from_yaml(settings.models_config_path)
        breakers = BreakerRegistry(
            failure_threshold=settings.breaker_failure_threshold,
            base_cooldown=settings.breaker_base_cooldown_seconds,
            max_cooldown=settings.breaker_max_cooldown_seconds,
            hard_cooldown=settings.breaker_hard_failure_cooldown_seconds,
        )
        llm_client = OpenAICompatibleClient(http, timeout_seconds=settings.llm_request_timeout_seconds)
        router = ModelRouter(
            registry,
            breakers,
            llm_client,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        )
        active = registry.active_models()
        log.info(
            "model pool: %d/%d models active (%s)",
            len(active),
            len(registry.models),
            ", ".join(sorted({p.name for _, p in active})) or "no providers configured",
        )

        # ---- embeddings + index ----
        embeddings = FastEmbedProvider(
            settings.embedding_model_list,
            settings.embedding_cache_dir,
            batch_size=settings.embedding_batch_size,
        )
        vector_store = LocalHybridVectorStore(
            settings.vector_dir,
            embedding_model=embeddings.model_name,
            vector_candidates=settings.vector_candidates,
            keyword_candidates=settings.keyword_candidates,
            rrf_k=settings.rrf_k,
        )

        # ---- persistence ----
        db = Database(settings.db_path)
        sources_repo = SqliteSourceRepository(db)
        conversations_repo = SqliteConversationRepository(db)
        collections_repo = SqliteCollectionRepository(db)

        # ---- ingestion ----
        transcriber = WhisperTranscriber(registry, http)
        if transcriber.available:
            log.info(
                "speech-to-text ready (%s)",
                ", ".join(spec.id for spec, _ in registry.active_transcription_models()),
            )
        renderer = (
            BrowserRenderer(
                timeout_seconds=settings.browser_timeout_seconds,
                wait_until=settings.browser_wait_until,
            )
            if settings.browser_fallback_enabled
            else None
        )
        processor = PayloadProcessor(
            transcriber=transcriber,
            max_chars=settings.max_document_chars,
            min_chars=settings.min_document_chars,
            max_audio_bytes=settings.max_audio_bytes,
        )
        upload_store = UploadStore(settings.upload_dir)

        # Specific services first; the HTTP loader is the catch-all.
        resolver = SourceResolver(
            [
                YouTubeLoader(http, settings.youtube_language_list),
                UploadLoader(upload_store, processor),
                HttpLoader(
                    http,
                    processor,
                    renderer=renderer,
                    render_below_chars=settings.browser_min_chars,
                ),
            ]
        )
        chunker = Chunker(settings.chunk_size, settings.chunk_overlap)
        ingestion = IngestionService(
            resolver=resolver,
            chunker=chunker,
            embeddings=embeddings,
            vector_store=vector_store,
            sources=sources_repo,
            embedding_batch_size=settings.embedding_batch_size,
            upload_store=upload_store,
            max_upload_bytes=settings.max_upload_bytes,
            max_concurrent=settings.max_concurrent_ingestions,
        )

        # ---- chat ----
        retrieval = RetrievalService(
            embeddings,
            vector_store,
            top_k=settings.retrieval_top_k,
            per_source_k=settings.collection_per_source_k,
        )
        documents = DocumentService(
            router,
            max_input_tokens=settings.mode_max_input_tokens,
            prompt_overhead_tokens=settings.mode_prompt_overhead_tokens,
            map_max_tokens=settings.mode_map_max_tokens,
            reduce_max_tokens=settings.mode_reduce_max_tokens,
            max_windows=settings.mode_max_windows,
            max_rounds=settings.mode_max_rounds,
            tokens_per_minute=settings.mode_tokens_per_minute,
            max_wait_seconds=settings.mode_max_wait_seconds,
        )
        collections = CollectionService(
            collections_repo, sources_repo, max_sources=settings.collection_max_sources
        )
        chat = ChatService(
            sources=sources_repo,
            conversations=conversations_repo,
            collections=collections_repo,
            retrieval=retrieval,
            documents=documents,
            router=router,
            max_history_messages=settings.max_history_messages,
            citation_preview_chars=settings.citation_preview_chars,
        )

        return cls(
            settings=settings,
            http=http,
            db=db,
            registry=registry,
            router=router,
            embeddings=embeddings,
            vector_store=vector_store,
            resolver=resolver,
            ingestion=ingestion,
            chat=chat,
            collections=collections,
            transcriber=transcriber,
            renderer=renderer,
        )

    def start_background_tasks(self) -> None:
        """Kick off work that must not delay the first request."""
        if not self.settings.validate_model_pool:
            return
        task = asyncio.create_task(self._verify_pool(), name="verify-model-pool")
        self.background.add(task)
        task.add_done_callback(self.background.discard)

    async def _verify_pool(self) -> None:
        try:
            availability = await validate_pool(
                self.registry, self.http, self.settings.model_listing_timeout_seconds
            )
            self.router.set_upstream_availability(availability)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - validation is advisory
            log.debug("model pool validation failed", exc_info=True)

    async def close(self) -> None:
        for task in list(self.background):
            task.cancel()
        if self.background:
            await asyncio.gather(*self.background, return_exceptions=True)
        await self.ingestion.shutdown()
        await self.http.aclose()
        self.db.close()
