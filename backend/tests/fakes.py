"""Shared doubles so tests never touch the network or load real models.

`MemoryStore` is an in-memory stand-in for Postgres that keeps the same
rules the schema enforces: owner scoping, one document per content key,
and a document disappearing once no source points at it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from app.core.exceptions import StaleIndexError
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
    utcnow,
)
from app.domain.ports import (
    CollectionRepository,
    ConversationRepository,
    DocumentRepository,
    EmbeddingProvider,
    IdentityRepository,
    SourceLoader,
    SourceRepository,
    Transcriber,
    VectorStore,
)


class HashEmbeddings(EmbeddingProvider):
    """Deterministic pseudo-embeddings keyed by text, so retrieval is repeatable."""

    model_name = "hash-test"
    dimension = 16

    def _vector(self, text: str) -> np.ndarray:
        seed = abs(hash(text)) % (2**32)
        rng = np.random.default_rng(seed)
        vector = rng.standard_normal(self.dimension).astype(np.float32)
        return vector / np.linalg.norm(vector)

    async def embed_documents(self, texts: list[str]) -> np.ndarray:
        return np.vstack([self._vector(t) for t in texts])

    async def embed_query(self, text: str) -> np.ndarray:
        return self._vector(text)


class FakeLoader(SourceLoader):
    kind = SourceKind.WEB

    def __init__(self, sections: int = 20, host: str = "example.com"):
        self._sections = sections
        self._host = host
        self.loads = 0

    def supports(self, url: str) -> bool:
        return self._host in url

    async def load(self, url: str) -> LoadedDocument:
        self.loads += 1
        text = "\n\n".join(
            f"Section {i}. LinkChat lets you chat with any URL. Fact {i}: the answer is {i * 7}."
            for i in range(self._sections)
        )
        return LoadedDocument(
            title="Example page",
            kind=self.kind,
            segments=[Segment(text=text)],
            metadata={"site_name": "Example"},
        )


class EchoLLMClient:
    """Streams back a fixed answer and records what it was asked."""

    def __init__(self, answer: str = "The answer is 7 [1]."):
        self.answer = answer
        self.calls: list[dict] = []

    async def stream_chat(self, provider, model, messages, *, temperature, max_tokens):
        self.calls.append(
            {
                "model": model,
                "system": messages[0].content,
                "user": messages[-1].content,
                "roles": [m.role for m in messages],
                "reminder_after_history": any(
                    m.role == "system" and "same language as the question" in m.content for m in messages[1:]
                ),
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        for word in self.answer.split(" "):
            yield word + " "


class FakeTranscriber(Transcriber):
    def __init__(self, available: bool = True):
        self._available = available

    @property
    def available(self) -> bool:
        return self._available

    async def transcribe(self, payload: bytes, filename: str):
        segments = [
            Segment(text=f"spoken line {i} about the subject", start=float(i * 5), end=float(i * 5 + 5))
            for i in range(12)
        ]
        return segments, {"transcription_model": "fake/whisper", "language": "en", "title": filename}


# ---------------------------------------------------------------- in-memory database


@dataclass
class MemoryStore:
    documents: dict[str, Document] = field(default_factory=dict)
    chunks: dict[str, list[Chunk]] = field(default_factory=dict)
    matrices: dict[str, np.ndarray] = field(default_factory=dict)
    sources: dict[str, Source] = field(default_factory=dict)
    collections: dict[str, Collection] = field(default_factory=dict)
    conversations: dict[str, Conversation] = field(default_factory=dict)
    messages: dict[str, list[Message]] = field(default_factory=dict)
    guests: dict[str, datetime] = field(default_factory=dict)
    users: dict[str, User] = field(default_factory=dict)
    login_codes: dict[str, LoginCode] = field(default_factory=dict)
    sessions: dict[str, Session] = field(default_factory=dict)

    # ---- cascades, mirroring the schema's foreign keys and trigger ----

    def delete_source(self, source_id: str) -> None:
        source = self.sources.pop(source_id, None)
        if source is None:
            return
        for conversation in [c for c in self.conversations.values() if c.source_id == source_id]:
            self.delete_conversation(conversation.id)
        for collection in self.collections.values():
            collection.source_ids = [sid for sid in collection.source_ids if sid != source_id]
        if not any(s.document_id == source.document_id for s in self.sources.values()):
            self.documents.pop(source.document_id, None)
            self.chunks.pop(source.document_id, None)
            self.matrices.pop(source.document_id, None)

    def delete_collection(self, collection_id: str) -> None:
        self.collections.pop(collection_id, None)
        for conversation in [c for c in self.conversations.values() if c.collection_id == collection_id]:
            self.delete_conversation(conversation.id)

    def delete_conversation(self, conversation_id: str) -> None:
        self.conversations.pop(conversation_id, None)
        self.messages.pop(conversation_id, None)

    def delete_owned(self, owner_id: str) -> None:
        for source in [s for s in self.sources.values() if s.owner_id == owner_id]:
            self.delete_source(source.id)
        for collection in [c for c in self.collections.values() if c.owner_id == owner_id]:
            self.delete_collection(collection.id)
        for conversation in [c for c in self.conversations.values() if c.owner_id == owner_id]:
            self.delete_conversation(conversation.id)

    def view(self, source: Source) -> Source:
        """A source with its document's current fields, as the SQL join returns it."""
        document = self.documents[source.document_id]
        return Source(
            id=source.id,
            owner_id=source.owner_id,
            document_id=source.document_id,
            url=document.url,
            kind=document.kind,
            title=document.title,
            status=document.status,
            error=document.error,
            chunk_count=document.chunk_count,
            metadata=document.metadata,
            created_at=source.created_at,
            updated_at=source.updated_at,
        )


class MemoryDocumentRepository(DocumentRepository):
    def __init__(self, store: MemoryStore):
        self._s = store

    async def add(self, document: Document) -> Document:
        self._s.documents[document.id] = document
        return document

    async def update(self, document: Document) -> Document:
        document.updated_at = utcnow()
        self._s.documents[document.id] = document
        return document

    async def get(self, document_id: str) -> Document | None:
        return self._s.documents.get(document_id)

    async def get_by_key(self, content_key: str) -> Document | None:
        return next((d for d in self._s.documents.values() if d.content_key == content_key), None)


class MemorySourceRepository(SourceRepository):
    def __init__(self, store: MemoryStore):
        self._s = store

    async def add(self, source: Source) -> Source:
        self._s.sources[source.id] = source
        return source

    async def get(self, owner_id: str, source_id: str) -> Source | None:
        source = self._s.sources.get(source_id)
        return self._s.view(source) if source and source.owner_id == owner_id else None

    async def get_many(self, owner_id: str, source_ids: list[str]) -> list[Source]:
        found = []
        for sid in source_ids:
            source = await self.get(owner_id, sid)
            if source is not None:
                found.append(source)
        return found

    async def get_by_document(self, owner_id: str, document_id: str) -> Source | None:
        for source in self._s.sources.values():
            if source.owner_id == owner_id and source.document_id == document_id:
                return self._s.view(source)
        return None

    async def list(self, owner_id: str, limit: int = 50) -> list[Source]:
        mine = [s for s in self._s.sources.values() if s.owner_id == owner_id]
        mine.sort(key=lambda s: s.created_at, reverse=True)
        return [self._s.view(s) for s in mine[:limit]]

    async def delete(self, owner_id: str, source_id: str) -> None:
        source = self._s.sources.get(source_id)
        if source and source.owner_id == owner_id:
            self._s.delete_source(source_id)


class MemoryCollectionRepository(CollectionRepository):
    def __init__(self, store: MemoryStore):
        self._s = store

    async def add(self, collection: Collection) -> Collection:
        self._s.collections[collection.id] = collection
        return collection

    async def update(self, collection: Collection) -> Collection:
        collection.updated_at = utcnow()
        self._s.collections[collection.id] = collection
        return collection

    async def get(self, owner_id: str, collection_id: str) -> Collection | None:
        collection = self._s.collections.get(collection_id)
        return collection if collection and collection.owner_id == owner_id else None

    async def list(self, owner_id: str, limit: int = 50) -> list[Collection]:
        mine = [c for c in self._s.collections.values() if c.owner_id == owner_id]
        mine.sort(key=lambda c: c.updated_at, reverse=True)
        return mine[:limit]

    async def delete(self, owner_id: str, collection_id: str) -> None:
        if await self.get(owner_id, collection_id):
            self._s.delete_collection(collection_id)


class MemoryConversationRepository(ConversationRepository):
    def __init__(self, store: MemoryStore):
        self._s = store

    async def add(self, conversation: Conversation) -> Conversation:
        self._s.conversations[conversation.id] = conversation
        self._s.messages.setdefault(conversation.id, [])
        return conversation

    async def get(self, owner_id: str, conversation_id: str) -> Conversation | None:
        conversation = self._s.conversations.get(conversation_id)
        return conversation if conversation and conversation.owner_id == owner_id else None

    async def list_for_source(self, owner_id: str, source_id: str) -> list[Conversation]:
        found = [c for c in self._s.conversations.values() if c.owner_id == owner_id and c.source_id == source_id]
        return sorted(found, key=lambda c: c.created_at, reverse=True)

    async def list_for_collection(self, owner_id: str, collection_id: str) -> list[Conversation]:
        found = [
            c for c in self._s.conversations.values() if c.owner_id == owner_id and c.collection_id == collection_id
        ]
        return sorted(found, key=lambda c: c.created_at, reverse=True)

    async def delete(self, owner_id: str, conversation_id: str) -> None:
        if await self.get(owner_id, conversation_id):
            self._s.delete_conversation(conversation_id)

    async def add_message(self, message: Message) -> Message:
        self._s.messages.setdefault(message.conversation_id, []).append(message)
        conversation = self._s.conversations.get(message.conversation_id)
        if conversation:
            conversation.updated_at = message.created_at
        return message

    async def list_messages(self, conversation_id: str, limit: int | None = None) -> list[Message]:
        messages = list(self._s.messages.get(conversation_id, []))
        return messages if limit is None else messages[-limit:]


class MemoryIdentityRepository(IdentityRepository):
    def __init__(self, store: MemoryStore):
        self._s = store

    async def touch_guest(self, guest_id: str) -> None:
        self._s.guests[guest_id] = utcnow()

    async def delete_owner(self, owner_id: str) -> None:
        self._s.delete_owned(owner_id)
        self._s.guests.pop(owner_id, None)

    async def purge_expired_guests(self, before: datetime) -> int:
        expired = [gid for gid, seen in self._s.guests.items() if seen < before]
        for gid in expired:
            await self.delete_owner(gid)
        return len(expired)

    async def get_user(self, user_id: str) -> User | None:
        return self._s.users.get(user_id)

    async def get_user_by_email(self, email: str) -> User | None:
        return next((u for u in self._s.users.values() if u.email == email.lower()), None)

    async def add_user(self, user: User) -> User:
        self._s.users[user.id] = user
        return user

    async def record_login(self, user_id: str, at: datetime) -> None:
        if user_id in self._s.users:
            self._s.users[user_id].last_login_at = at

    async def delete_user(self, user_id: str) -> None:
        self._s.delete_owned(user_id)
        self._s.users.pop(user_id, None)
        for session in [s for s in self._s.sessions.values() if s.user_id == user_id]:
            self._s.sessions.pop(session.token_hash, None)

    async def transfer_ownership(self, from_owner: str, to_owner: str) -> None:
        theirs = {s.document_id: s for s in self._s.sources.values() if s.owner_id == to_owner}
        for source in [s for s in self._s.sources.values() if s.owner_id == from_owner]:
            duplicate = theirs.get(source.document_id)
            if duplicate is None:
                source.owner_id = to_owner
                continue
            for conversation in self._s.conversations.values():
                if conversation.source_id == source.id:
                    conversation.source_id = duplicate.id
            for collection in self._s.collections.values():
                collection.source_ids = [
                    duplicate.id if sid == source.id and duplicate.id not in collection.source_ids else sid
                    for sid in collection.source_ids
                ]
            self._s.sources.pop(source.id)
        for collection in self._s.collections.values():
            if collection.owner_id == from_owner:
                collection.owner_id = to_owner
        for conversation in self._s.conversations.values():
            if conversation.owner_id == from_owner:
                conversation.owner_id = to_owner
        self._s.guests.pop(from_owner, None)

    async def add_login_code(self, code: LoginCode) -> LoginCode:
        self._s.login_codes[code.id] = code
        return code

    async def latest_login_code(self, email: str) -> LoginCode | None:
        pending = [c for c in self._s.login_codes.values() if c.email == email.lower() and c.consumed_at is None]
        return max(pending, key=lambda c: c.created_at) if pending else None

    async def record_code_attempt(self, code_id: str) -> int:
        code = self._s.login_codes[code_id]
        code.attempts += 1
        return code.attempts

    async def consume_login_code(self, code_id: str, at: datetime) -> None:
        self._s.login_codes[code_id].consumed_at = at

    async def purge_login_codes(self, before: datetime) -> int:
        gone = [cid for cid, c in self._s.login_codes.items() if c.expires_at < before or c.consumed_at]
        for cid in gone:
            self._s.login_codes.pop(cid)
        return len(gone)

    async def add_session(self, session: Session) -> Session:
        self._s.sessions[session.token_hash] = session
        return session

    async def get_session(self, token_hash: str) -> tuple[Session, User] | None:
        session = self._s.sessions.get(token_hash)
        if session is None:
            return None
        return session, self._s.users[session.user_id]

    async def touch_session(self, session_id: str, seen_at: datetime, expires_at: datetime) -> None:
        for session in self._s.sessions.values():
            if session.id == session_id:
                session.last_seen_at, session.expires_at = seen_at, expires_at

    async def delete_session(self, session_id: str) -> None:
        for key, session in list(self._s.sessions.items()):
            if session.id == session_id:
                self._s.sessions.pop(key)

    async def purge_sessions(self, before: datetime) -> int:
        gone = [k for k, s in self._s.sessions.items() if s.expires_at < before]
        for key in gone:
            self._s.sessions.pop(key)
        return len(gone)


_TOKEN_RE = re.compile(r"[a-z0-9]+")


class MemoryVectorStore(VectorStore):
    """Cosine plus keyword overlap, fused with reciprocal rank fusion like the real store."""

    def __init__(self, store: MemoryStore, *, embedding_model: str, candidates: int = 12, rrf_k: int = 60):
        self._s = store
        self._model = embedding_model
        self._candidates = candidates
        self._rrf_k = rrf_k

    async def upsert(self, document_id: str, chunks: list[Chunk], embeddings: np.ndarray) -> None:
        self._s.chunks[document_id] = list(chunks)
        self._s.matrices[document_id] = np.asarray(embeddings, dtype=np.float32)
        document = self._s.documents.get(document_id)
        if document is not None:
            document.embedding_model = self._model
            document.chunk_count = len(chunks)

    async def search(self, document_id: str, query_vector: np.ndarray, query_text: str, top_k: int):
        self._check(document_id)
        chunks = self._s.chunks.get(document_id, [])
        if not chunks:
            return []
        cosine = self._s.matrices[document_id] @ np.asarray(query_vector, dtype=np.float32)
        vector_order = np.argsort(-cosine)[: self._candidates]
        tokens = set(_TOKEN_RE.findall(query_text.lower()))
        keyword = np.array([len(tokens & set(_TOKEN_RE.findall(c.text.lower()))) for c in chunks], dtype=np.float32)
        keyword_order = [int(i) for i in np.argsort(-keyword)[: self._candidates] if keyword[i] > 0]

        fused: dict[int, float] = {}
        for rank, idx in enumerate(vector_order):
            fused[int(idx)] = fused.get(int(idx), 0.0) + 1.0 / (self._rrf_k + rank + 1)
        for rank, idx in enumerate(keyword_order):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (self._rrf_k + rank + 1)
        ranked = sorted(fused.items(), key=lambda item: (-item[1], -float(cosine[item[0]])))[:top_k]
        return [
            RetrievedChunk(
                chunk=chunks[idx],
                score=round(score, 6),
                vector_score=round(float(cosine[idx]), 4),
                keyword_score=round(float(keyword[idx]), 4),
            )
            for idx, score in ranked
        ]

    async def exists(self, document_id: str) -> bool:
        document = self._s.documents.get(document_id)
        return bool(document) and document.embedding_model == self._model and document.chunk_count > 0

    async def all_chunks(self, document_id: str) -> list[Chunk]:
        self._check(document_id)
        return sorted(self._s.chunks.get(document_id, []), key=lambda c: c.index)

    def _check(self, document_id: str) -> None:
        document = self._s.documents.get(document_id)
        if document and document.embedding_model and document.embedding_model != self._model:
            raise StaleIndexError("stale index; use Re-read")
