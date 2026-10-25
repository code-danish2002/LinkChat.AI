"""Domain entities. Plain dataclasses, free of transport and storage concerns."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def utcnow() -> datetime:
    return datetime.now(UTC)


class SourceKind(StrEnum):
    YOUTUBE = "youtube"
    WEB = "web"
    PDF = "pdf"
    TEXT = "text"
    DOCX = "docx"
    PPTX = "pptx"
    XLSX = "xlsx"
    EPUB = "epub"
    AUDIO = "audio"


class ChatMode(StrEnum):
    """What the user wants done with the source.

    `qa` answers a typed question from retrieved excerpts. Every other mode is
    a preset task over the whole document, so it needs no question at all.
    """

    QA = "qa"
    SUMMARY = "summary"
    KEY_POINTS = "key_points"
    OUTLINE = "outline"
    ACTION_ITEMS = "action_items"
    EXTRACT = "extract"
    FLASHCARDS = "flashcards"
    QUIZ = "quiz"
    SIMPLIFY = "simplify"
    TRANSLATE = "translate"
    FACT_CHECK = "fact_check"
    BLOG_POST = "blog_post"


class SourceStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


@dataclass
class Document:
    """One piece of content, shared by everyone who added it.

    Keyed by `content_key`: the normalised URL, or `upload:<digest>` for a
    file. Ingestion state lives here, so the second person to add a URL that
    is still being read simply attaches to the work in progress.
    """

    id: str
    content_key: str
    url: str
    kind: SourceKind
    title: str
    status: SourceStatus = SourceStatus.PENDING
    error: str | None = None
    chunk_count: int = 0
    embedding_model: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass
class Source:
    """One owner's handle on a document.

    The content fields (url, kind, status, ...) are a read-only view of the
    underlying document; repositories fill them in on the way out. `title`
    is the owner's own name for it when set, else the document title.
    """

    id: str
    owner_id: str
    document_id: str
    url: str
    kind: SourceKind
    title: str
    status: SourceStatus = SourceStatus.PENDING
    error: str | None = None
    chunk_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    @classmethod
    def for_document(cls, source_id: str, owner_id: str, document: Document, title: str | None = None) -> Source:
        return cls(
            id=source_id,
            owner_id=owner_id,
            document_id=document.id,
            url=document.url,
            kind=document.kind,
            title=title or document.title,
            status=document.status,
            error=document.error,
            chunk_count=document.chunk_count,
            metadata=document.metadata,
        )


@dataclass
class Segment:
    """A unit of loaded text. Timed segments (video captions) carry start/end seconds."""

    text: str
    start: float | None = None
    end: float | None = None


@dataclass
class LoadedDocument:
    title: str
    kind: SourceKind
    segments: list[Segment]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n".join(seg.text for seg in self.segments)

    @property
    def is_timed(self) -> bool:
        return any(seg.start is not None for seg in self.segments)


@dataclass
class Chunk:
    id: str
    document_id: str
    index: int
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievedChunk:
    chunk: Chunk
    score: float
    vector_score: float | None = None
    keyword_score: float | None = None


@dataclass
class Collection:
    """A named group of sources that can be queried as one corpus."""

    id: str
    owner_id: str
    title: str
    source_ids: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass
class Conversation:
    """A chat thread against exactly one target: a source or a collection."""

    id: str
    owner_id: str
    title: str
    source_id: str | None = None
    collection_id: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if bool(self.source_id) == bool(self.collection_id):
            raise ValueError("a conversation targets either a source or a collection, not both")


@dataclass
class Citation:
    index: int
    chunk_id: str
    text: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Message:
    id: str
    conversation_id: str
    role: MessageRole
    content: str
    model: str | None = None
    mode: ChatMode = ChatMode.QA
    citations: list[Citation] = field(default_factory=list)
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class ChatMessage:
    """A message in the shape LLM providers expect."""

    role: str
    content: str


# ---------------------------------------------------------------- identity


@dataclass
class User:
    id: str
    email: str
    display_name: str
    created_at: datetime = field(default_factory=utcnow)
    last_login_at: datetime | None = None


@dataclass
class LoginCode:
    """A pending "enter the code we emailed you" challenge. Only the hash is stored."""

    id: str
    email: str
    code_hash: str
    expires_at: datetime
    display_name: str | None = None
    attempts: int = 0
    consumed_at: datetime | None = None
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class Session:
    id: str
    user_id: str
    token_hash: str
    expires_at: datetime
    created_at: datetime = field(default_factory=utcnow)
    last_seen_at: datetime = field(default_factory=utcnow)


class PrincipalKind(StrEnum):
    GUEST = "guest"
    USER = "user"


@dataclass(frozen=True)
class Principal:
    """Who is asking. Every owned row carries `owner_id`, which is either a
    guest id the browser generated or a user id."""

    owner_id: str
    kind: PrincipalKind
    user: User | None = None
    session: Session | None = None

    @property
    def is_guest(self) -> bool:
        return self.kind is PrincipalKind.GUEST
