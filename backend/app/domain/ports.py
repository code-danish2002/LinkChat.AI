"""Ports (interfaces) between the application core and its adapters.

Services depend only on these abstractions; concrete adapters (fastembed,
the local vector store, SQLite, HTTP loaders) live under `providers/`,
`storage/` and `ingestion/`. Swapping an adapter never touches a service.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from app.domain.models import (
    Chunk,
    Collection,
    Conversation,
    LoadedDocument,
    Message,
    RetrievedChunk,
    Segment,
    Source,
    SourceKind,
)


class SourceLoader(ABC):
    """Turns a URL into a LoadedDocument. One loader per kind of service."""

    kind: SourceKind
    #: What this loader accepts, for the capabilities endpoint. Defaults to the
    #: kind it produces, which loaders that detect the kind at runtime override.
    label: str = ""

    @abstractmethod
    def supports(self, url: str) -> bool: ...

    @abstractmethod
    async def load(self, url: str) -> LoadedDocument: ...


class Transcriber(ABC):
    """Turns audio bytes into timed segments."""

    @property
    @abstractmethod
    def available(self) -> bool:
        """False when no speech-to-text provider has credentials."""

    @abstractmethod
    async def transcribe(self, payload: bytes, filename: str) -> tuple[list[Segment], dict]: ...


class EmbeddingProvider(ABC):
    @property
    @abstractmethod
    def model_name(self) -> str: ...

    @property
    @abstractmethod
    def dimension(self) -> int: ...

    @abstractmethod
    async def embed_documents(self, texts: list[str]) -> np.ndarray: ...

    @abstractmethod
    async def embed_query(self, text: str) -> np.ndarray: ...


class VectorStore(ABC):
    @abstractmethod
    async def upsert(self, source_id: str, chunks: list[Chunk], embeddings: np.ndarray) -> None: ...

    @abstractmethod
    async def search(
        self, source_id: str, query_vector: np.ndarray, query_text: str, top_k: int
    ) -> list[RetrievedChunk]: ...

    @abstractmethod
    async def delete(self, source_id: str) -> None: ...

    @abstractmethod
    async def exists(self, source_id: str) -> bool: ...

    @abstractmethod
    async def all_chunks(self, source_id: str) -> list[Chunk]:
        """Every chunk for a source, in document order. Used by whole-document modes."""


class SourceRepository(ABC):
    @abstractmethod
    def add(self, source: Source) -> Source: ...

    @abstractmethod
    def update(self, source: Source) -> Source: ...

    @abstractmethod
    def get(self, source_id: str) -> Source | None: ...

    @abstractmethod
    def get_by_url(self, url: str) -> Source | None: ...

    @abstractmethod
    def list(self, limit: int = 50) -> list[Source]: ...

    @abstractmethod
    def delete(self, source_id: str) -> None: ...


class CollectionRepository(ABC):
    @abstractmethod
    def add(self, collection: Collection) -> Collection: ...

    @abstractmethod
    def update(self, collection: Collection) -> Collection: ...

    @abstractmethod
    def get(self, collection_id: str) -> Collection | None: ...

    @abstractmethod
    def list(self, limit: int = 50) -> list[Collection]: ...

    @abstractmethod
    def delete(self, collection_id: str) -> None: ...


class ConversationRepository(ABC):
    @abstractmethod
    def add(self, conversation: Conversation) -> Conversation: ...

    @abstractmethod
    def get(self, conversation_id: str) -> Conversation | None: ...

    @abstractmethod
    def list_for_source(self, source_id: str) -> list[Conversation]: ...

    @abstractmethod
    def list_for_collection(self, collection_id: str) -> list[Conversation]: ...

    @abstractmethod
    def delete(self, conversation_id: str) -> None: ...

    @abstractmethod
    def add_message(self, message: Message) -> Message: ...

    @abstractmethod
    def list_messages(self, conversation_id: str, limit: int | None = None) -> list[Message]: ...
