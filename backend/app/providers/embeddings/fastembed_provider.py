"""Local open-source embeddings via fastembed (ONNX, CPU friendly).

Candidates are tried in order at startup; the first model that loads wins.
That model's name is stored with every index, so an index built by a
different model is never silently mixed with one built by another.

Some models are trained for asymmetric search and need a prefix telling them
whether a text is a query or a document. fastembed does not apply those for
most models, so they are applied here. Skipping them does not fail loudly, it
just retrieves worse, which is why the prefixes live next to the model list
and are covered by tests.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.core.exceptions import EmbeddingUnavailableError
from app.domain.ports import EmbeddingProvider

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PrefixScheme:
    """How a model wants queries and documents marked.

    Each template takes one `{text}` placeholder.
    """

    query: str
    document: str

    def for_query(self, text: str) -> str:
        return self.query.format(text=text)

    def for_document(self, text: str) -> str:
        return self.document.format(text=text)


#: Prefixes required by models fastembed does not handle itself. Taken from each
#: model's own documentation, reachable via `TextEmbedding.list_supported_models()`.
#: Every key must be a name fastembed can actually load, which a test enforces.
PREFIX_SCHEMES: dict[str, PrefixScheme] = {
    "google/embeddinggemma-300m": PrefixScheme(
        query="task: search result | query: {text}",
        document="title: none | text: {text}",
    ),
    "intfloat/multilingual-e5-large": PrefixScheme(
        query="query: {text}",
        document="passage: {text}",
    ),
}


def prefix_scheme_for(model_name: str) -> PrefixScheme | None:
    return PREFIX_SCHEMES.get(model_name)


class FastEmbedProvider(EmbeddingProvider):
    def __init__(self, candidates: list[str], cache_dir: Path, *, batch_size: int = 32):
        if not candidates:
            raise EmbeddingUnavailableError("No embedding models configured.")
        self._batch_size = batch_size
        self._model, self._model_name = self._load_first_available(candidates, cache_dir)
        self._prefixes = prefix_scheme_for(self._model_name)
        probe = next(iter(self._model.embed(["dimension probe"])))
        self._dimension = int(np.asarray(probe).shape[-1])
        log.info(
            "embedding model ready: %s (dim=%d, prefixes=%s)",
            self._model_name,
            self._dimension,
            "yes" if self._prefixes else "not needed",
        )

    @staticmethod
    def _load_first_available(candidates: list[str], cache_dir: Path):
        from fastembed import TextEmbedding

        cache_dir.mkdir(parents=True, exist_ok=True)
        errors: list[str] = []
        for name in candidates:
            try:
                log.info("loading embedding model %s (first run downloads it)", name)
                return TextEmbedding(model_name=name, cache_dir=str(cache_dir)), name
            except Exception as exc:  # noqa: BLE001 - try the next candidate
                errors.append(f"{name}: {exc.__class__.__name__}: {str(exc)[:120]}")
                log.warning("embedding model %s unavailable: %s", name, exc)
        raise EmbeddingUnavailableError(
            "None of the configured embedding models could be loaded.", details={"errors": errors}
        )

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def prefixes(self) -> PrefixScheme | None:
        return self._prefixes

    async def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self._dimension), dtype=np.float32)
        return await asyncio.to_thread(self._embed_sync, texts)

    async def embed_query(self, text: str) -> np.ndarray:
        return await asyncio.to_thread(self._embed_query_sync, text)

    def _embed_sync(self, texts: list[str]) -> np.ndarray:
        if self._prefixes is not None:
            texts = [self._prefixes.for_document(text) for text in texts]
        vectors = list(self._model.embed(texts, batch_size=self._batch_size))
        return _normalize(np.asarray(vectors, dtype=np.float32))

    def _embed_query_sync(self, text: str) -> np.ndarray:
        if self._prefixes is not None:
            # The prefix changes the meaning of the text for the model, so the
            # query form must go through the document path too: query_embed
            # would re-prefix or ignore it depending on the model.
            vector = next(iter(self._model.embed([self._prefixes.for_query(text)])))
        else:
            vector = next(iter(self._model.query_embed([text])))
        return _normalize(np.asarray(vector, dtype=np.float32)[None, :])[0]


def _normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms
