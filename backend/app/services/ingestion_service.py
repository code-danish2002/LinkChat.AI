"""Orchestrates URL -> loader -> chunks -> embeddings -> index.

Ingestion runs as a background task so the API can answer immediately with
a `pending` source that the client polls until it becomes `ready`.
"""

from __future__ import annotations

import asyncio
import logging

from app.core.exceptions import AppError, IngestionError, NotFoundError, ValidationError
from app.core.ids import new_id
from app.core.urls import normalize_url
from app.domain.models import Source, SourceStatus
from app.domain.ports import EmbeddingProvider, SourceRepository, VectorStore
from app.ingestion.chunker import Chunker
from app.ingestion.extractors import SUPPORTED_UPLOAD_EXTENSIONS
from app.ingestion.resolver import SourceResolver
from app.ingestion.upload import UploadStore, parse_upload_url, upload_url

log = logging.getLogger(__name__)


class IngestionService:
    def __init__(
        self,
        *,
        resolver: SourceResolver,
        chunker: Chunker,
        embeddings: EmbeddingProvider,
        vector_store: VectorStore,
        sources: SourceRepository,
        embedding_batch_size: int = 32,
        upload_store: UploadStore | None = None,
        max_upload_bytes: int = 40 * 1024 * 1024,
        max_concurrent: int = 4,
    ):
        self._resolver = resolver
        self._chunker = chunker
        self._embeddings = embeddings
        self._vector_store = vector_store
        self._sources = sources
        self._batch_size = embedding_batch_size
        self._uploads = upload_store
        self._max_upload_bytes = max_upload_bytes
        self._tasks: set[asyncio.Task] = set()
        self._in_flight: set[str] = set()
        # Submissions are accepted immediately and queued here. Without this a
        # burst of users pasting links starts every ingestion at once, and each
        # one holds the whole document in memory while it is embedded.
        self._slots = asyncio.Semaphore(max(1, max_concurrent))
        self._max_concurrent = max(1, max_concurrent)

    # ---- commands ----

    async def submit(self, raw_url: str) -> Source:
        url = normalize_url(raw_url)
        loader = self._resolver.resolve(url)  # raises UnsupportedSourceError early

        existing = self._sources.get_by_url(url)
        if existing is not None:
            if existing.status is SourceStatus.READY and await self._vector_store.exists(existing.id):
                return existing
            if existing.status in (SourceStatus.PENDING, SourceStatus.PROCESSING) and existing.id in self._in_flight:
                return existing
            existing.status = SourceStatus.PENDING
            existing.error = None
            self._sources.update(existing)
            self._schedule(existing.id)
            return existing

        source = Source(id=new_id("src"), url=url, kind=loader.kind, title=url)
        self._sources.add(source)
        self._schedule(source.id)
        return source

    async def submit_upload(self, filename: str, payload: bytes) -> Source:
        """Register an uploaded file and ingest it like any other source."""
        if self._uploads is None:
            raise ValidationError("File uploads are not enabled on this server.")
        if not payload:
            raise ValidationError("The uploaded file is empty.")
        if len(payload) > self._max_upload_bytes:
            limit = self._max_upload_bytes / (1024 * 1024)
            raise ValidationError(
                f"The file is {len(payload) / (1024 * 1024):.1f} MB, above the {limit:.0f} MB limit."
            )
        name = (filename or "").strip()
        extension = name[name.rfind(".") :].lower() if "." in name else ""
        if extension not in SUPPORTED_UPLOAD_EXTENSIONS:
            raise ValidationError(
                "Unsupported file type. Supported: "
                + ", ".join(SUPPORTED_UPLOAD_EXTENSIONS)
                + "."
            )

        digest, _ = await asyncio.to_thread(self._uploads.save, payload, name)
        reference = upload_url(digest, name)
        existing = self._sources.get_by_url(reference)
        if existing is not None:
            if existing.status is SourceStatus.READY and await self._vector_store.exists(existing.id):
                return existing
            if existing.id in self._in_flight:
                return existing
            existing.status = SourceStatus.PENDING
            existing.error = None
            self._sources.update(existing)
            self._schedule(existing.id)
            return existing

        loader = self._resolver.resolve(reference)
        source = Source(id=new_id("src"), url=reference, kind=loader.kind, title=name)
        self._sources.add(source)
        self._schedule(source.id)
        return source

    async def reingest(self, source_id: str) -> Source:
        source = self._require(source_id)
        if source.id in self._in_flight:
            return source
        source.status = SourceStatus.PENDING
        source.error = None
        self._sources.update(source)
        self._schedule(source.id)
        return source

    async def delete(self, source_id: str) -> None:
        source = self._require(source_id)
        await self._vector_store.delete(source_id)
        if self._uploads is not None and source.url.startswith("upload://"):
            try:
                digest, filename = parse_upload_url(source.url)
            except ValidationError:
                pass
            else:
                await asyncio.to_thread(self._uploads.delete, digest, filename)
        self._sources.delete(source_id)

    def get(self, source_id: str) -> Source:
        return self._require(source_id)

    def list(self, limit: int = 50) -> list[Source]:
        return self._sources.list(limit)

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    # ---- pipeline ----

    def _schedule(self, source_id: str) -> None:
        self._in_flight.add(source_id)
        task = asyncio.create_task(self._process(source_id), name=f"ingest:{source_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @property
    def queue_depth(self) -> int:
        """Sources accepted but not finished, including those still queued."""
        return len(self._in_flight)

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    async def _process(self, source_id: str) -> None:
        # Wait for a slot before touching the document, so queued work costs
        # nothing but a row in the database and a task object.
        async with self._slots:
            await self._process_now(source_id)

    async def _process_now(self, source_id: str) -> None:
        source = self._sources.get(source_id)
        if source is None:
            self._in_flight.discard(source_id)
            return
        source.status = SourceStatus.PROCESSING
        self._sources.update(source)
        log.info("ingesting %s (%s)", source.url, source.id)

        try:
            loader = self._resolver.resolve(source.url)
            document = await loader.load(source.url)
            chunks = self._chunker.chunk(document, source.id)
            if not chunks:
                raise IngestionError("No text could be extracted from that URL.")

            vectors = []
            for start in range(0, len(chunks), self._batch_size):
                batch = chunks[start : start + self._batch_size]
                vectors.append(await self._embeddings.embed_documents([c.text for c in batch]))
            import numpy as np

            await self._vector_store.upsert(source.id, chunks, np.vstack(vectors))

            source.kind = document.kind
            source.title = document.title
            source.metadata = {
                **document.metadata,
                "characters": len(document.text),
                "embedding_model": self._embeddings.model_name,
            }
            source.chunk_count = len(chunks)
            source.status = SourceStatus.READY
            source.error = None
            log.info("ready: %s -> %d chunks", source.id, len(chunks))
        except AppError as exc:
            source.status = SourceStatus.FAILED
            source.error = exc.message
            log.warning("ingestion failed for %s: %s", source.id, exc.message)
        except asyncio.CancelledError:
            source.status = SourceStatus.FAILED
            source.error = "Ingestion was interrupted by a server shutdown."
            self._sources.update(source)
            raise
        except Exception as exc:  # noqa: BLE001 - never leave a source stuck in "processing"
            source.status = SourceStatus.FAILED
            source.error = f"Unexpected error: {exc.__class__.__name__}: {str(exc)[:200]}"
            log.exception("ingestion crashed for %s", source.id)
        finally:
            self._in_flight.discard(source_id)
        self._sources.update(source)

    def _require(self, source_id: str) -> Source:
        source = self._sources.get(source_id)
        if source is None:
            raise NotFoundError("Source not found.")
        return source
