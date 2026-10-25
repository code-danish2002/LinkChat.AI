"""Ports (interfaces) between the application core and its adapters.

Services depend only on these abstractions; concrete adapters (fastembed,
Postgres, HTTP loaders, email providers) live under `providers/`,
`storage/` and `ingestion/`. Swapping an adapter never touches a service.

Ownership runs through every repository: a source, collection or
conversation belongs to an `owner_id`, and lookups always take the owner so
nobody can read another owner's rows by guessing an id. Documents and
chunks are shared and therefore not owner-scoped.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import numpy as np

from app.domain.models import (
    Chunk,
    Collection,
    Conversation,
    Document,
    LoadedDocument,
    LoginCode,
    Message,
    RetrievedChunk,
    Segment,
    Session,
    Source,
    SourceKind,
    User,
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


class EmailSender(ABC):
    """Delivers a login code. The console implementation just logs it."""

    #: Shown to the frontend so it can explain where the code will arrive.
    name: str = ""

    @abstractmethod
    async def send(self, *, to: str, subject: str, text: str) -> None: ...


class VectorStore(ABC):
    """Chunks and their embeddings, one set per document."""

    @abstractmethod
    async def upsert(self, document_id: str, chunks: list[Chunk], embeddings: np.ndarray) -> None:
        """Replace the document's chunks and record which model embedded them."""

    @abstractmethod
    async def search(
        self, document_id: str, query_vector: np.ndarray, query_text: str, top_k: int
    ) -> list[RetrievedChunk]: ...

    @abstractmethod
    async def exists(self, document_id: str) -> bool:
        """True only when the document has chunks built by the current model."""

    @abstractmethod
    async def all_chunks(self, document_id: str) -> list[Chunk]:
        """Every chunk for a document, in document order. Used by whole-document modes."""


class DocumentRepository(ABC):
    @abstractmethod
    async def add(self, document: Document) -> Document: ...

    @abstractmethod
    async def update(self, document: Document) -> Document: ...

    @abstractmethod
    async def get(self, document_id: str) -> Document | None: ...

    @abstractmethod
    async def get_by_key(self, content_key: str) -> Document | None: ...


class SourceRepository(ABC):
    @abstractmethod
    async def add(self, source: Source) -> Source: ...

    @abstractmethod
    async def get(self, owner_id: str, source_id: str) -> Source | None: ...

    @abstractmethod
    async def get_many(self, owner_id: str, source_ids: list[str]) -> list[Source]:
        """The owner's sources among `source_ids`, in the order given; unknown ids are skipped."""

    @abstractmethod
    async def get_by_document(self, owner_id: str, document_id: str) -> Source | None: ...

    @abstractmethod
    async def list(self, owner_id: str, limit: int = 50) -> list[Source]: ...

    @abstractmethod
    async def delete(self, owner_id: str, source_id: str) -> None: ...


class CollectionRepository(ABC):
    @abstractmethod
    async def add(self, collection: Collection) -> Collection: ...

    @abstractmethod
    async def update(self, collection: Collection) -> Collection: ...

    @abstractmethod
    async def get(self, owner_id: str, collection_id: str) -> Collection | None: ...

    @abstractmethod
    async def list(self, owner_id: str, limit: int = 50) -> list[Collection]: ...

    @abstractmethod
    async def delete(self, owner_id: str, collection_id: str) -> None: ...


class ConversationRepository(ABC):
    @abstractmethod
    async def add(self, conversation: Conversation) -> Conversation: ...

    @abstractmethod
    async def get(self, owner_id: str, conversation_id: str) -> Conversation | None: ...

    @abstractmethod
    async def list_for_source(self, owner_id: str, source_id: str) -> list[Conversation]: ...

    @abstractmethod
    async def list_for_collection(self, owner_id: str, collection_id: str) -> list[Conversation]: ...

    @abstractmethod
    async def delete(self, owner_id: str, conversation_id: str) -> None: ...

    @abstractmethod
    async def add_message(self, message: Message) -> Message: ...

    @abstractmethod
    async def list_messages(self, conversation_id: str, limit: int | None = None) -> list[Message]: ...


class IdentityRepository(ABC):
    """Users, guests, login codes and sessions."""

    # ---- guests ----

    @abstractmethod
    async def touch_guest(self, guest_id: str) -> None:
        """Create the guest if new, else move its last-seen time to now."""

    @abstractmethod
    async def delete_owner(self, owner_id: str) -> None:
        """Remove everything an owner holds, then the guest row if it was one."""

    @abstractmethod
    async def purge_expired_guests(self, before: datetime) -> int:
        """Delete guests unseen since `before` and all they own. Returns how many."""

    # ---- users ----

    @abstractmethod
    async def get_user(self, user_id: str) -> User | None: ...

    @abstractmethod
    async def get_user_by_email(self, email: str) -> User | None: ...

    @abstractmethod
    async def add_user(self, user: User) -> User: ...

    @abstractmethod
    async def record_login(self, user_id: str, at: datetime) -> None: ...

    @abstractmethod
    async def delete_user(self, user_id: str) -> None:
        """Remove the account, its sessions and everything it owns."""

    @abstractmethod
    async def transfer_ownership(self, from_owner: str, to_owner: str) -> None:
        """Move a guest's sources, collections and conversations to a user.

        Where the user already holds the same document, the guest's duplicate
        source is dropped and its conversations re-pointed at the user's copy.
        """

    # ---- login codes ----

    @abstractmethod
    async def add_login_code(self, code: LoginCode) -> LoginCode: ...

    @abstractmethod
    async def latest_login_code(self, email: str) -> LoginCode | None:
        """The newest unconsumed code for the address, expired or not."""

    @abstractmethod
    async def record_code_attempt(self, code_id: str) -> int:
        """Increment and return the attempt count."""

    @abstractmethod
    async def consume_login_code(self, code_id: str, at: datetime) -> None: ...

    @abstractmethod
    async def purge_login_codes(self, before: datetime) -> int: ...

    # ---- sessions ----

    @abstractmethod
    async def add_session(self, session: Session) -> Session: ...

    @abstractmethod
    async def get_session(self, token_hash: str) -> tuple[Session, User] | None: ...

    @abstractmethod
    async def touch_session(self, session_id: str, seen_at: datetime, expires_at: datetime) -> None: ...

    @abstractmethod
    async def delete_session(self, session_id: str) -> None: ...

    @abstractmethod
    async def purge_sessions(self, before: datetime) -> int: ...
