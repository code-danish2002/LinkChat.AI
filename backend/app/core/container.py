"""Composition root: builds every adapter and service once at startup.

This is the only module that knows about concrete implementations. To add a
new source service, construct its loader here and register it with the
resolver ahead of the HTTP catch-all. `build` is synchronous and cheap;
`start` opens the database, applies the schema and launches background
work, so tests can swap in doubles before anything touches the network.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta

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
from app.providers.email.senders import build_email_sender
from app.providers.embeddings.fastembed_provider import FastEmbedProvider
from app.providers.llm.health import BreakerRegistry
from app.providers.llm.openai_compatible import OpenAICompatibleClient
from app.providers.llm.registry import ModelRegistry
from app.providers.llm.router import ModelRouter
from app.providers.llm.validation import validate_pool
from app.providers.transcription.whisper import WhisperTranscriber
from app.services.auth_service import AuthService
from app.services.chat_service import ChatService
from app.services.collection_service import CollectionService
from app.services.document_service import DocumentService
from app.services.ingestion_service import IngestionService
from app.services.maintenance import MaintenanceService
from app.services.retrieval_service import RetrievalService
from app.storage.postgres.database import Database
from app.storage.postgres.repositories import (
    PostgresCollectionRepository,
    PostgresConversationRepository,
    PostgresDocumentRepository,
    PostgresIdentityRepository,
    PostgresSourceRepository,
)
from app.storage.postgres.vector_store import PostgresVectorStore

log = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    http: httpx.AsyncClient
    db: Database | None
    registry: ModelRegistry
    router: ModelRouter
    embeddings: EmbeddingProvider
    vector_store: VectorStore
    resolver: SourceResolver
    ingestion: IngestionService
    chat: ChatService
    collections: CollectionService
    auth: AuthService
    maintenance: MaintenanceService | None = None
    transcriber: Transcriber | None = None
    renderer: BrowserRenderer | None = None
    background: set[asyncio.Task] = field(default_factory=set)

    @classmethod
    def build(cls, settings: Settings) -> Container:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        if settings.environment == "production" and not settings.auth_secret:
            log.warning("AUTH_SECRET is not set: sessions will not survive a restart")

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

        # ---- persistence ----
        db = Database(
            settings.database_url,
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
            max_idle_seconds=settings.db_pool_max_idle_seconds,
        )
        documents_repo = PostgresDocumentRepository(db)
        sources_repo = PostgresSourceRepository(db)
        conversations_repo = PostgresConversationRepository(db)
        collections_repo = PostgresCollectionRepository(db)
        identity_repo = PostgresIdentityRepository(db)

        # ---- embeddings + index ----
        embeddings = FastEmbedProvider(
            settings.embedding_model_list,
            settings.embedding_cache_dir,
            batch_size=settings.embedding_batch_size,
        )
        vector_store = PostgresVectorStore(
            db,
            embedding_model=embeddings.model_name,
            vector_candidates=settings.vector_candidates,
            keyword_candidates=settings.keyword_candidates,
            rrf_k=settings.rrf_k,
        )

        # ---- identity ----
        email = build_email_sender(
            settings.email_provider,
            http,
            api_key=settings.resend_api_key,
            sender=settings.email_from,
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            smtp_username=settings.smtp_username,
            smtp_password=settings.smtp_password,
        )
        auth = AuthService(
            identity_repo,
            email,
            secret=settings.auth_secret,
            guest_ttl=timedelta(hours=settings.guest_ttl_hours),
            session_ttl=timedelta(hours=settings.session_ttl_hours),
            code_ttl=timedelta(minutes=settings.login_code_ttl_minutes),
            code_max_attempts=settings.login_code_max_attempts,
            app_name=settings.app_name.replace(" API", ""),
            reveal_codes=settings.environment != "production",
        )
        if settings.environment == "production" and email.name == "console":
            log.warning(
                "EMAIL_PROVIDER=console in production: login codes go to the server log only. "
                "Set EMAIL_PROVIDER=resend and RESEND_API_KEY so users can sign in."
            )
        maintenance = MaintenanceService(
            identity_repo,
            guest_ttl=timedelta(hours=settings.guest_ttl_hours),
            interval=timedelta(minutes=settings.cleanup_interval_minutes),
        )

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
            documents=documents_repo,
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
        collections = CollectionService(collections_repo, sources_repo, max_sources=settings.collection_max_sources)
        chat = ChatService(
            sources=sources_repo,
            conversations=conversations_repo,
            collections=collections_repo,
            retrieval=retrieval,
            documents=documents,
            router=router,
            transcriber=transcriber,
            max_history_messages=settings.max_history_messages,
            citation_preview_chars=settings.citation_preview_chars,
            max_voice_bytes=settings.max_voice_bytes,
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
            auth=auth,
            maintenance=maintenance,
            transcriber=transcriber,
            renderer=renderer,
        )

    async def start(self) -> None:
        """Open the database, apply the schema, then kick off background work."""
        if self.db is not None:
            await self.db.open()
            await self.db.migrate(self.settings.schema_path)
        self.start_background_tasks()

    def start_background_tasks(self) -> None:
        """Kick off work that must not delay the first request."""
        if self.maintenance is not None:
            self._spawn(self.maintenance.run_forever(), "cleanup")
        if self.settings.validate_model_pool:
            self._spawn(self._verify_pool(), "verify-model-pool")

    def _spawn(self, coroutine, name: str) -> None:
        task = asyncio.create_task(coroutine, name=name)
        self.background.add(task)
        task.add_done_callback(self.background.discard)

    async def _verify_pool(self) -> None:
        try:
            availability = await validate_pool(self.registry, self.http, self.settings.model_listing_timeout_seconds)
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
        if self.db is not None:
            await self.db.close()
