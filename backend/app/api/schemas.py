"""Request and response models for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.models import (
    ChatMode,
    Citation,
    Collection,
    Conversation,
    Message,
    Principal,
    Session,
    Source,
    User,
)
from app.providers.llm.router import ModelStatus
from app.services.modes import ModeSpec

# ---------------------------------------------------------------- auth


class RequestCodeRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    display_name: str | None = Field(default=None, max_length=80)


class RequestCodeResponse(BaseModel):
    sent: bool
    email: str
    expires_at: datetime
    delivery: str
    #: Present only when the server logs codes instead of emailing them (local development).
    debug_code: str | None = None


class VerifyCodeRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    code: str = Field(..., min_length=6, max_length=6)


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    created_at: datetime

    @classmethod
    def from_domain(cls, user: User) -> UserResponse:
        return cls(id=user.id, email=user.email, display_name=user.display_name, created_at=user.created_at)


class SessionResponse(BaseModel):
    token: str
    expires_at: datetime
    user: UserResponse

    @classmethod
    def build(cls, token: str, session: Session, user: User) -> SessionResponse:
        return cls(token=token, expires_at=session.expires_at, user=UserResponse.from_domain(user))


class MeResponse(BaseModel):
    kind: str  # guest | user
    owner_id: str
    user: UserResponse | None
    session_expires_at: datetime | None
    #: For guests: how long after their last request their data is deleted.
    guest_ttl_hours: int

    @classmethod
    def from_principal(cls, principal: Principal, *, guest_ttl_hours: int) -> MeResponse:
        return cls(
            kind=principal.kind.value,
            owner_id=principal.owner_id,
            user=UserResponse.from_domain(principal.user) if principal.user else None,
            session_expires_at=principal.session.expires_at if principal.session else None,
            guest_ttl_hours=guest_ttl_hours,
        )


# ---------------------------------------------------------------- sources


class CreateSourceRequest(BaseModel):
    url: str = Field(
        ...,
        min_length=3,
        max_length=2048,
        examples=["https://en.wikipedia.org/wiki/Retrieval-augmented_generation"],
    )


class SourceResponse(BaseModel):
    id: str
    document_id: str
    url: str
    kind: str
    title: str
    status: str
    error: str | None
    chunk_count: int
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, source: Source) -> SourceResponse:
        return cls(
            id=source.id,
            document_id=source.document_id,
            url=source.url,
            kind=source.kind.value,
            title=source.title,
            status=source.status.value,
            error=source.error,
            chunk_count=source.chunk_count,
            metadata=source.metadata,
            created_at=source.created_at,
            updated_at=source.updated_at,
        )


class CreateCollectionRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    source_ids: list[str] = Field(default_factory=list)


class UpdateCollectionRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    source_ids: list[str] | None = None


class CollectionResponse(BaseModel):
    id: str
    title: str
    source_ids: list[str]
    source_count: int
    ready_count: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, collection: Collection, ready_count: int) -> CollectionResponse:
        return cls(
            id=collection.id,
            title=collection.title,
            source_ids=collection.source_ids,
            source_count=len(collection.source_ids),
            ready_count=ready_count,
            created_at=collection.created_at,
            updated_at=collection.updated_at,
        )


class CreateConversationRequest(BaseModel):
    source_id: str | None = None
    collection_id: str | None = None
    title: str | None = Field(default=None, max_length=200)


class ConversationResponse(BaseModel):
    id: str
    source_id: str | None
    collection_id: str | None
    title: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, conversation: Conversation) -> ConversationResponse:
        return cls(
            id=conversation.id,
            source_id=conversation.source_id,
            collection_id=conversation.collection_id,
            title=conversation.title,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        )


class CitationResponse(BaseModel):
    index: int
    chunk_id: str
    text: str
    score: float
    metadata: dict[str, Any]

    @classmethod
    def from_domain(cls, citation: Citation) -> CitationResponse:
        return cls(
            index=citation.index,
            chunk_id=citation.chunk_id,
            text=citation.text,
            score=citation.score,
            metadata=citation.metadata,
        )


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    model: str | None
    mode: str
    citations: list[CitationResponse]
    created_at: datetime

    @classmethod
    def from_domain(cls, message: Message) -> MessageResponse:
        return cls(
            id=message.id,
            conversation_id=message.conversation_id,
            role=message.role.value,
            content=message.content,
            model=message.model,
            mode=message.mode.value,
            citations=[CitationResponse.from_domain(c) for c in message.citations],
            created_at=message.created_at,
        )


class SendMessageRequest(BaseModel):
    content: str = Field(default="", max_length=4000)
    mode: ChatMode = ChatMode.QA
    #: The answer will be read aloud, so it is written as short spoken prose.
    spoken: bool = False


class ModeResponse(BaseModel):
    id: str
    label: str
    description: str
    scope: str
    needs_input: bool
    input_label: str | None
    input_placeholder: str | None
    default_prompt: str

    @classmethod
    def from_domain(cls, spec: ModeSpec) -> ModeResponse:
        return cls(
            id=spec.id.value,
            label=spec.label,
            description=spec.description,
            scope=spec.scope,
            needs_input=spec.needs_input,
            input_label=spec.input_label,
            input_placeholder=spec.input_placeholder,
            default_prompt=spec.default_prompt,
        )


class ModelStatusResponse(BaseModel):
    id: str
    provider: str
    model: str
    display_name: str
    priority: int
    active: bool
    state: str
    retry_in_seconds: float | None
    successes: int
    failures: int
    last_error: str | None
    average_latency_ms: float | None
    available_upstream: bool | None

    @classmethod
    def from_domain(cls, status: ModelStatus) -> ModelStatusResponse:
        return cls(**status.__dict__)


class ModelPoolResponse(BaseModel):
    active_count: int
    total_count: int
    models: list[ModelStatusResponse]


class CapabilitiesResponse(BaseModel):
    uploads: bool
    audio_transcription: bool
    javascript_rendering: bool
    collections: bool
    #: Spoken questions through the voice endpoint; needs the same provider as audio transcription.
    voice: bool
    upload_extensions: list[str]
    max_upload_mb: int
    #: How login codes are delivered: "resend" means real email, "console" means the server log.
    email_login: str
    guest_ttl_hours: int
    session_ttl_hours: int


class HealthResponse(BaseModel):
    status: str
    version: str
    embedding_model: str
    active_models: int
    database: str  # ok | unreachable
    supported_sources: list[str]
    capabilities: CapabilitiesResponse


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody
