"""File-backed hybrid index, one per source.

Retrieval in LinkChat is always scoped to a single source, so each source
gets its own small index: a normalised embedding matrix (.npy) plus the
chunks (.json). Queries combine cosine similarity with BM25 keyword scores
via reciprocal rank fusion, which helps on pages full of exact names,
SKUs and numbers where pure semantic search is weak.

Indexes are cached in memory after first use; writes are atomic.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from app.core.exceptions import StaleIndexError
from app.domain.models import Chunk, RetrievedChunk
from app.domain.ports import VectorStore

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


@dataclass
class _SourceIndex:
    chunks: list[Chunk]
    matrix: np.ndarray
    bm25: BM25Okapi | None


class LocalHybridVectorStore(VectorStore):
    def __init__(
        self,
        root: Path,
        *,
        embedding_model: str,
        vector_candidates: int = 12,
        keyword_candidates: int = 12,
        rrf_k: int = 60,
        cache_size: int = 32,
    ):
        self._root = root
        self._embedding_model = embedding_model
        self._vector_candidates = vector_candidates
        self._keyword_candidates = keyword_candidates
        self._rrf_k = rrf_k
        self._cache_size = cache_size
        self._cache: dict[str, _SourceIndex] = {}
        self._lock = asyncio.Lock()
        self._root.mkdir(parents=True, exist_ok=True)

    # ---- paths ----

    def _paths(self, source_id: str) -> tuple[Path, Path]:
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", source_id)
        return self._root / f"{safe}.npy", self._root / f"{safe}.json"

    # ---- VectorStore ----

    async def upsert(self, source_id: str, chunks: list[Chunk], embeddings: np.ndarray) -> None:
        if len(chunks) != embeddings.shape[0]:
            raise ValueError("chunks and embeddings length mismatch")
        matrix = np.ascontiguousarray(embeddings, dtype=np.float32)
        payload = {
            "embedding_model": self._embedding_model,
            "dimension": int(matrix.shape[1]) if matrix.size else 0,
            "chunks": [{"id": c.id, "index": c.index, "text": c.text, "metadata": c.metadata} for c in chunks],
        }
        await asyncio.to_thread(self._write, source_id, matrix, payload)
        async with self._lock:
            self._cache[source_id] = self._build_index(chunks, matrix)
            self._evict()

    async def search(
        self, source_id: str, query_vector: np.ndarray, query_text: str, top_k: int
    ) -> list[RetrievedChunk]:
        index = await self._get_index(source_id)
        if index is None or not index.chunks:
            return []
        return await asyncio.to_thread(self._hybrid_search, index, query_vector, query_text, top_k)

    async def delete(self, source_id: str) -> None:
        async with self._lock:
            self._cache.pop(source_id, None)
        for path in self._paths(source_id):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    async def exists(self, source_id: str) -> bool:
        """True only when a usable index exists for the current model.

        Returning False for a stale index is what makes ingestion rebuild it
        automatically instead of serving results that cannot be compared.
        """
        stored = await asyncio.to_thread(self._stored_model, source_id)
        return stored == self._embedding_model

    async def all_chunks(self, source_id: str) -> list[Chunk]:
        index = await self._get_index(source_id)
        if index is None:
            return []
        return sorted(index.chunks, key=lambda chunk: chunk.index)

    # ---- internals ----

    def _write(self, source_id: str, matrix: np.ndarray, payload: dict) -> None:
        npy, meta = self._paths(source_id)
        tmp_npy = npy.with_suffix(".npy.tmp")
        tmp_meta = meta.with_suffix(".json.tmp")
        with open(tmp_npy, "wb") as handle:  # np.save would append .npy to a bare path
            np.save(handle, matrix)
        tmp_meta.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_npy, npy)
        os.replace(tmp_meta, meta)

    def _stored_model(self, source_id: str) -> str | None:
        """The embedding model an index was built with, or None if there is no index."""
        npy, meta = self._paths(source_id)
        if not (npy.exists() and meta.exists()):
            return None
        try:
            return json.loads(meta.read_text(encoding="utf-8")).get("embedding_model")
        except (OSError, json.JSONDecodeError):
            return None

    def _read(self, source_id: str) -> _SourceIndex | None:
        npy, meta = self._paths(source_id)
        if not (npy.exists() and meta.exists()):
            return None
        payload = json.loads(meta.read_text(encoding="utf-8"))
        stored = payload.get("embedding_model")
        if stored != self._embedding_model:
            # Vectors from two models are not comparable, so refuse loudly
            # rather than quietly returning nothing.
            raise StaleIndexError(
                f"This source was indexed with {stored!r} but the server now uses "
                f"{self._embedding_model!r}. Use Re-read to rebuild it."
            )
        matrix = np.load(npy)
        chunks = [
            Chunk(
                id=c["id"],
                source_id=source_id,
                index=c["index"],
                text=c["text"],
                metadata=c.get("metadata") or {},
            )
            for c in payload["chunks"]
        ]
        return self._build_index(chunks, matrix)

    @staticmethod
    def _build_index(chunks: list[Chunk], matrix: np.ndarray) -> _SourceIndex:
        corpus = [tokenize(c.text) for c in chunks]
        bm25 = BM25Okapi(corpus) if corpus and any(corpus) else None
        return _SourceIndex(chunks=chunks, matrix=matrix, bm25=bm25)

    async def _get_index(self, source_id: str) -> _SourceIndex | None:
        async with self._lock:
            cached = self._cache.get(source_id)
        if cached is not None:
            return cached
        loaded = await asyncio.to_thread(self._read, source_id)
        if loaded is not None:
            async with self._lock:
                self._cache[source_id] = loaded
                self._evict()
        return loaded

    def _evict(self) -> None:
        while len(self._cache) > self._cache_size:
            self._cache.pop(next(iter(self._cache)))

    def _hybrid_search(
        self, index: _SourceIndex, query_vector: np.ndarray, query_text: str, top_k: int
    ) -> list[RetrievedChunk]:
        n = len(index.chunks)
        cosine = index.matrix @ np.asarray(query_vector, dtype=np.float32)
        vector_order = np.argsort(-cosine)[: min(self._vector_candidates, n)]

        keyword_scores = None
        keyword_order: list[int] = []
        if index.bm25 is not None:
            tokens = tokenize(query_text)
            if tokens:
                keyword_scores = np.asarray(index.bm25.get_scores(tokens), dtype=np.float32)
                keyword_order = [
                    int(i)
                    for i in np.argsort(-keyword_scores)[: min(self._keyword_candidates, n)]
                    if keyword_scores[i] > 0
                ]

        fused: dict[int, float] = {}
        for rank, idx in enumerate(vector_order):
            fused[int(idx)] = fused.get(int(idx), 0.0) + 1.0 / (self._rrf_k + rank + 1)
        for rank, idx in enumerate(keyword_order):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (self._rrf_k + rank + 1)

        ranked = sorted(fused.items(), key=lambda item: (-item[1], -float(cosine[item[0]])))[:top_k]
        return [
            RetrievedChunk(
                chunk=index.chunks[idx],
                score=round(score, 6),
                vector_score=round(float(cosine[idx]), 4),
                keyword_score=round(float(keyword_scores[idx]), 4) if keyword_scores is not None else None,
            )
            for idx, score in ranked
        ]
