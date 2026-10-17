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
class Source:
    id: str
    url: str
    kind: SourceKind
    title: str
    status: SourceStatus = SourceStatus.PENDING
    error: str | None = None
    chunk_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


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
    source_id: str
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
    title: str
    source_ids: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass
class Conversation:
    """A chat thread against exactly one target: a source or a collection."""

    id: str
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
