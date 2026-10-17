"""Query to excerpts: embed once, then search one source or a whole collection."""

from __future__ import annotations

import asyncio

from app.domain.models import Chunk, RetrievedChunk
from app.domain.ports import EmbeddingProvider, VectorStore


class RetrievalService:
    def __init__(
        self,
        embeddings: EmbeddingProvider,
        vector_store: VectorStore,
        *,
        top_k: int = 6,
        per_source_k: int = 4,
    ):
        self._embeddings = embeddings
        self._vector_store = vector_store
        self._top_k = top_k
        self._per_source_k = per_source_k

    async def retrieve(
        self, source_id: str, query: str, top_k: int | None = None
    ) -> list[RetrievedChunk]:
        query = query.strip()
        if not query:
            return []
        vector = await self._embeddings.embed_query(query)
        return await self._vector_store.search(source_id, vector, query, top_k or self._top_k)

    async def retrieve_many(
        self,
        source_ids: list[str],
        query: str,
        *,
        top_k: int | None = None,
        per_source_k: int | None = None,
    ) -> list[RetrievedChunk]:
        """Search every source in parallel, then keep the globally best excerpts.

        Each index is scored independently, so the per-source scores are already
        comparable: both come from the same fusion over the same embedding model.
        """
        query = query.strip()
        if not query or not source_ids:
            return []
        if len(source_ids) == 1:
            return await self.retrieve(source_ids[0], query, top_k)

        vector = await self._embeddings.embed_query(query)
        limit = per_source_k or self._per_source_k
        results = await asyncio.gather(
            *(self._vector_store.search(sid, vector, query, limit) for sid in source_ids),
            return_exceptions=True,
        )
        merged: list[RetrievedChunk] = []
        for result in results:
            if isinstance(result, BaseException):
                continue
            merged.extend(result)
        merged.sort(key=lambda item: (-item.score, -(item.vector_score or 0.0)))
        return merged[: top_k or self._top_k]

    async def all_chunks(self, source_ids: list[str]) -> list[Chunk]:
        """Every chunk of every source, in source order then document order."""
        batches = await asyncio.gather(
            *(self._vector_store.all_chunks(sid) for sid in source_ids), return_exceptions=True
        )
        chunks: list[Chunk] = []
        for batch in batches:
            if isinstance(batch, BaseException):
                continue
            chunks.extend(batch)
        return chunks
