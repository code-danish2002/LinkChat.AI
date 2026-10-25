"""Chunks and embeddings in Postgres, searched with pgvector plus full-text.

Retrieval is scoped to one document at a time, so each query scans only
that document's rows: a cosine ranking from pgvector and a keyword ranking
from Postgres full-text search, fused with reciprocal rank fusion. That
keeps exact names, product codes and numbers findable where pure semantic
search is weak, exactly as the file-based index did before.

`embedding` has no fixed dimension because the model is chosen per
deployment. The model that built a document's chunks is recorded on the
document, and a mismatch is reported as a stale index rather than silently
compared.
"""

from __future__ import annotations

import re
from typing import Any

import numpy as np
from psycopg.types.json import Jsonb

from app.core.exceptions import StaleIndexError
from app.domain.models import Chunk, RetrievedChunk
from app.domain.ports import VectorStore
from app.storage.postgres.database import Database

_SEARCH_SQL = """
WITH by_vector AS (
    SELECT id, row_number() OVER (ORDER BY embedding <=> %(vec)s::vector) AS rank
      FROM chunks
     WHERE document_id = %(doc)s
     ORDER BY embedding <=> %(vec)s::vector
     LIMIT %(vector_candidates)s
), by_keyword AS (
    SELECT id, row_number() OVER (ORDER BY ts_rank_cd(tsv, q) DESC) AS rank, ts_rank_cd(tsv, q) AS kw
      FROM chunks, to_tsquery('simple', %(query)s) AS q
     WHERE document_id = %(doc)s AND tsv @@ q
     ORDER BY kw DESC
     LIMIT %(keyword_candidates)s
)
SELECT c.id, c.document_id, c.position, c.text, c.metadata,
       1 - (c.embedding <=> %(vec)s::vector) AS cosine,
       k.kw AS keyword,
       COALESCE(1.0 / (%(rrf_k)s + v.rank), 0) + COALESCE(1.0 / (%(rrf_k)s + k.rank), 0) AS score
  FROM chunks c
  LEFT JOIN by_vector v ON v.id = c.id
  LEFT JOIN by_keyword k ON k.id = c.id
 WHERE v.id IS NOT NULL OR k.id IS NOT NULL
 ORDER BY score DESC, cosine DESC
 LIMIT %(top_k)s
"""


_WORD_RE = re.compile(r"\w+")


def keyword_query(text: str) -> str:
    """An OR of the query's words, so a chunk matching any of them ranks.

    `plainto_tsquery` ANDs every word, which would drop a chunk that holds the
    product code but not the word "price". The 'simple' configuration keeps
    this language-neutral: no stemming, no stop words, Devanagari and Latin
    alike.
    """
    words = {w.lower() for w in _WORD_RE.findall(text) if w.strip("_")}
    return " | ".join(sorted(words)) if words else "nomatch_zz"


def vector_literal(vector: np.ndarray) -> str:
    values = np.asarray(vector, dtype=np.float32).ravel()
    return "[" + ",".join(f"{float(x):.7g}" for x in values) + "]"


class PostgresVectorStore(VectorStore):
    def __init__(
        self,
        db: Database,
        *,
        embedding_model: str,
        vector_candidates: int = 12,
        keyword_candidates: int = 12,
        rrf_k: int = 60,
    ):
        self._db = db
        self._embedding_model = embedding_model
        self._vector_candidates = vector_candidates
        self._keyword_candidates = keyword_candidates
        self._rrf_k = rrf_k

    # ---- VectorStore ----

    async def upsert(self, document_id: str, chunks: list[Chunk], embeddings: np.ndarray) -> None:
        if len(chunks) != embeddings.shape[0]:
            raise ValueError("chunks and embeddings length mismatch")
        matrix = np.asarray(embeddings, dtype=np.float32)
        rows = [
            (chunk.id, document_id, chunk.index, chunk.text, Jsonb(chunk.metadata), vector_literal(matrix[i]))
            for i, chunk in enumerate(chunks)
        ]
        async with self._db.transaction() as conn:
            await conn.execute("DELETE FROM chunks WHERE document_id = %s", (document_id,))
            async with conn.cursor() as cur:
                await cur.executemany(
                    "INSERT INTO chunks (id, document_id, position, text, metadata, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s::vector)",
                    rows,
                )
            await conn.execute(
                "UPDATE documents SET embedding_model = %s, chunk_count = %s, updated_at = now() WHERE id = %s",
                (self._embedding_model, len(chunks), document_id),
            )

    async def search(
        self, document_id: str, query_vector: np.ndarray, query_text: str, top_k: int
    ) -> list[RetrievedChunk]:
        async with self._db.connection() as conn:
            await self._check_model(conn, document_id)
            rows = await (
                await conn.execute(
                    _SEARCH_SQL,
                    {
                        "doc": document_id,
                        "vec": vector_literal(query_vector),
                        "query": keyword_query(query_text),
                        "vector_candidates": self._vector_candidates,
                        "keyword_candidates": self._keyword_candidates,
                        "rrf_k": self._rrf_k,
                        "top_k": top_k,
                    },
                )
            ).fetchall()
        return [
            RetrievedChunk(
                chunk=_to_chunk(row),
                score=round(float(row["score"]), 6),
                vector_score=round(float(row["cosine"]), 4) if row["cosine"] is not None else None,
                keyword_score=round(float(row["keyword"]), 4) if row["keyword"] is not None else None,
            )
            for row in rows
        ]

    async def exists(self, document_id: str) -> bool:
        """True only when chunks exist and were built by the current model.

        Returning False for a stale index is what makes ingestion rebuild it
        automatically instead of serving results that cannot be compared.
        """
        async with self._db.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT embedding_model, chunk_count FROM documents WHERE id = %s", (document_id,)
                )
            ).fetchone()
        return bool(row) and row["embedding_model"] == self._embedding_model and row["chunk_count"] > 0

    async def all_chunks(self, document_id: str) -> list[Chunk]:
        async with self._db.connection() as conn:
            await self._check_model(conn, document_id)
            rows = await (
                await conn.execute(
                    "SELECT id, document_id, position, text, metadata FROM chunks "
                    "WHERE document_id = %s ORDER BY position",
                    (document_id,),
                )
            ).fetchall()
        return [_to_chunk(row) for row in rows]

    # ---- internals ----

    async def _check_model(self, conn, document_id: str) -> None:
        row = await (
            await conn.execute("SELECT embedding_model FROM documents WHERE id = %s", (document_id,))
        ).fetchone()
        stored = row["embedding_model"] if row else None
        if stored is not None and stored != self._embedding_model:
            # Vectors from two models are not comparable, so refuse loudly
            # rather than quietly returning nothing.
            raise StaleIndexError(
                f"This source was indexed with {stored!r} but the server now uses "
                f"{self._embedding_model!r}. Use Re-read to rebuild it."
            )


def _to_chunk(row: dict[str, Any]) -> Chunk:
    return Chunk(
        id=row["id"],
        document_id=row["document_id"],
        index=row["position"],
        text=row["text"],
        metadata=row["metadata"] or {},
    )
