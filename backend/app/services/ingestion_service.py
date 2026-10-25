"""Orchestrates URL -> loader -> chunks -> embeddings -> index.

Content is shared. A URL (or an uploaded file, by its hash) maps to one
document; each owner who adds it gets a source row pointing at that
document. The first submission starts ingestion in the background and the
API answers immediately with a `pending` source the client polls; anyone
adding the same content meanwhile simply attaches to the work in progress.
"""

from __future__ import annotations

import asyncio
import logging

import numpy as np

from app.core.exceptions import AppError, IngestionError, NotFoundError, ValidationError
from app.core.ids import new_id
from app.core.urls import normalize_url
from app.domain.models import Document, Source, SourceStatus
from app.domain.ports import DocumentRepository, EmbeddingProvider, SourceRepository, VectorStore
from app.ingestion.chunker import Chunker
from app.ingestion.extractors import SUPPORTED_UPLOAD_EXTENSIONS
from app.ingestion.resolver import SourceResolver
from app.ingestion.upload import UPLOAD_SCHEME, UploadStore, parse_upload_url, upload_url

log = logging.getLogger(__name__)


def upload_content_key(digest: str) -> str:
    return f"upload:{digest}"


class IngestionService:
    def __init__(
        self,
        *,
        resolver: SourceResolver,
        chunker: Chunker,
        embeddings: EmbeddingProvider,
        vector_store: VectorStore,
        documents: DocumentRepository,
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
        self._documents = documents
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

    async def submit(self, owner_id: str, raw_url: str) -> Source:
        url = normalize_url(raw_url)
        loader = self._resolver.resolve(url)  # raises UnsupportedSourceError early

        document = await self._documents.get_by_key(url)
        if document is None:
            document = Document(id=new_id("doc"), content_key=url, url=url, kind=loader.kind, title=url)
            await self._documents.add(document)
            self._schedule(document.id)
        else:
            await self._refresh_if_needed(document)
        return await self._attach(owner_id, document)

    async def submit_upload(self, owner_id: str, filename: str, payload: bytes) -> Source:
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
                "Unsupported file type. Supported: " + ", ".join(SUPPORTED_UPLOAD_EXTENSIONS) + "."
            )

        digest, _ = await asyncio.to_thread(self._uploads.save, payload, name)
        reference = upload_url(digest, name)
        document = await self._documents.get_by_key(upload_content_key(digest))
        if document is None:
            loader = self._resolver.resolve(reference)
            document = Document(
                id=new_id("doc"), content_key=upload_content_key(digest), url=reference, kind=loader.kind, title=name
            )
            await self._documents.add(document)
            self._schedule(document.id)
        else:
            await self._refresh_if_needed(document)
        return await self._attach(owner_id, document)

    async def reingest(self, owner_id: str, source_id: str) -> Source:
        source = await self._require(owner_id, source_id)
        document = await self._documents.get(source.document_id)
        if document is None:
            raise NotFoundError("Source not found.")
        if document.id not in self._in_flight:
            document.status = SourceStatus.PENDING
            document.error = None
            await self._documents.update(document)
            self._schedule(document.id)
        return await self._require(owner_id, source_id)

    async def delete(self, owner_id: str, source_id: str) -> None:
        """Drop the owner's handle. The document goes too once nobody holds it."""
        await self._require(owner_id, source_id)
        await self._sources.delete(owner_id, source_id)

    async def get(self, owner_id: str, source_id: str) -> Source:
        return await self._require(owner_id, source_id)

    async def list(self, owner_id: str, limit: int = 50) -> list[Source]:
        return await self._sources.list(owner_id, limit)

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    # ---- sharing ----

    async def _attach(self, owner_id: str, document: Document) -> Source:
        source = await self._sources.get_by_document(owner_id, document.id)
        if source is None:
            source = Source.for_document(new_id("src"), owner_id, document)
            await self._sources.add(source)
        return source

    async def _refresh_if_needed(self, document: Document) -> None:
        """Rebuild a document that failed, or whose index a model change made stale."""
        if document.id in self._in_flight:
            return
        if document.status is SourceStatus.READY and await self._vector_store.exists(document.id):
            return
        document.status = SourceStatus.PENDING
        document.error = None
        await self._documents.update(document)
        self._schedule(document.id)

    # ---- pipeline ----

    def _schedule(self, document_id: str) -> None:
        self._in_flight.add(document_id)
        task = asyncio.create_task(self._process(document_id), name=f"ingest:{document_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @property
    def queue_depth(self) -> int:
        """Documents accepted but not finished, including those still queued."""
        return len(self._in_flight)

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    async def _process(self, document_id: str) -> None:
        # Wait for a slot before touching the document, so queued work costs
        # nothing but a row in the database and a task object.
        async with self._slots:
            await self._process_now(document_id)

    async def _process_now(self, document_id: str) -> None:
        document = await self._documents.get(document_id)
        if document is None:
            self._in_flight.discard(document_id)
            return
        document.status = SourceStatus.PROCESSING
        await self._documents.update(document)
        log.info("ingesting %s (%s)", document.url, document.id)

        try:
            loader = self._resolver.resolve(document.url)
            loaded = await loader.load(document.url)
            chunks = self._chunker.chunk(loaded, document.id)
            if not chunks:
                raise IngestionError("No text could be extracted from that URL.")

            vectors = []
            for start in range(0, len(chunks), self._batch_size):
                batch = chunks[start : start + self._batch_size]
                vectors.append(await self._embeddings.embed_documents([c.text for c in batch]))
            await self._vector_store.upsert(document.id, chunks, np.vstack(vectors))

            document.kind = loaded.kind
            document.title = loaded.title
            document.metadata = {
                **loaded.metadata,
                "characters": len(loaded.text),
                "embedding_model": self._embeddings.model_name,
            }
            document.embedding_model = self._embeddings.model_name
            document.chunk_count = len(chunks)
            document.status = SourceStatus.READY
            document.error = None
            log.info("ready: %s -> %d chunks", document.id, len(chunks))
            await self._discard_upload(document)
        except AppError as exc:
            document.status = SourceStatus.FAILED
            document.error = exc.message
            log.warning("ingestion failed for %s: %s", document.id, exc.message)
        except asyncio.CancelledError:
            document.status = SourceStatus.FAILED
            document.error = "Ingestion was interrupted by a server shutdown."
            await self._documents.update(document)
            raise
        except Exception as exc:  # noqa: BLE001 - never leave a document stuck in "processing"
            document.status = SourceStatus.FAILED
            document.error = f"Unexpected error: {exc.__class__.__name__}: {str(exc)[:200]}"
            log.exception("ingestion crashed for %s", document.id)
        finally:
            self._in_flight.discard(document_id)
        await self._documents.update(document)

    async def _discard_upload(self, document: Document) -> None:
        """The extracted chunks are what is kept; the original file is not."""
        if self._uploads is None or not document.url.startswith(UPLOAD_SCHEME):
            return
        try:
            digest, filename = parse_upload_url(document.url)
        except ValidationError:
            return
        await asyncio.to_thread(self._uploads.delete, digest, filename)

    async def _require(self, owner_id: str, source_id: str) -> Source:
        source = await self._sources.get(owner_id, source_id)
        if source is None:
            raise NotFoundError("Source not found.")
        return source
