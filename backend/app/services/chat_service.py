"""Conversations, modes and grounded streamed answers.

A conversation targets exactly one thing: a single source, or a collection
of them. Each message carries a mode, which decides whether the answer comes
from the best matching excerpts or from the whole document.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.core.exceptions import (
    NoModelAvailableError,
    NotFoundError,
    SourceNotReadyError,
    ValidationError,
)
from app.core.ids import new_id
from app.domain.models import (
    ChatMode,
    Chunk,
    Citation,
    Conversation,
    Message,
    MessageRole,
    RetrievedChunk,
    Source,
    SourceKind,
    SourceStatus,
)
from app.domain.ports import CollectionRepository, ConversationRepository, SourceRepository
from app.providers.llm.router import ModelRouter
from app.services.document_service import DocumentService
from app.services.events import StreamEvent
from app.services.modes import ModeSpec, get_mode
from app.services.prompts import build_retrieval_messages, format_timestamp
from app.services.retrieval_service import RetrievalService

log = logging.getLogger(__name__)

_MAX_INPUT_CHARS = 4000
_FULLWIDTH_CITATION = re.compile(r"【(\d{1,3})】")
_CITATION_MARKER = re.compile(r"\[(\d{1,3})\]")


@dataclass
class Target:
    """What a conversation is pointed at, flattened for the services below."""

    title: str
    kind: SourceKind
    sources: list[Source] = field(default_factory=list)

    @property
    def source_ids(self) -> list[str]:
        return [source.id for source in self.sources]

    @property
    def titles(self) -> dict[str, str]:
        return {source.id: source.title for source in self.sources}

    @property
    def count(self) -> int:
        return len(self.sources)


class ChatService:
    def __init__(
        self,
        *,
        sources: SourceRepository,
        conversations: ConversationRepository,
        retrieval: RetrievalService,
        documents: DocumentService,
        router: ModelRouter,
        collections: CollectionRepository | None = None,
        max_history_messages: int = 8,
        citation_preview_chars: int = 420,
    ):
        self._sources = sources
        self._conversations = conversations
        self._collections = collections
        self._retrieval = retrieval
        self._documents = documents
        self._router = router
        self._max_history = max_history_messages
        self._preview_chars = citation_preview_chars

    # ---- conversations ----

    def create_conversation(
        self,
        source_id: str | None = None,
        title: str | None = None,
        collection_id: str | None = None,
    ) -> Conversation:
        if bool(source_id) == bool(collection_id):
            raise ValidationError("Provide exactly one of source_id or collection_id.")
        if source_id:
            target_title = self._require_source(source_id).title
        else:
            target_title = self._require_collection(collection_id or "").title
        conversation = Conversation(
            id=new_id("cnv"),
            title=title or target_title,
            source_id=source_id,
            collection_id=collection_id,
        )
        return self._conversations.add(conversation)

    def get_conversation(self, conversation_id: str) -> Conversation:
        conversation = self._conversations.get(conversation_id)
        if conversation is None:
            raise NotFoundError("Conversation not found.")
        return conversation

    def list_conversations(self, source_id: str) -> list[Conversation]:
        self._require_source(source_id)
        return self._conversations.list_for_source(source_id)

    def list_collection_conversations(self, collection_id: str) -> list[Conversation]:
        self._require_collection(collection_id)
        return self._conversations.list_for_collection(collection_id)

    def delete_conversation(self, conversation_id: str) -> None:
        self.get_conversation(conversation_id)
        self._conversations.delete(conversation_id)

    def list_messages(self, conversation_id: str) -> list[Message]:
        self.get_conversation(conversation_id)
        return self._conversations.list_messages(conversation_id)

    # ---- answering ----

    async def stream_answer(
        self, conversation_id: str, content: str, mode: ChatMode | str = ChatMode.QA
    ) -> AsyncIterator[StreamEvent]:
        spec = get_mode(mode)
        user_input = (content or "").strip()
        if spec.needs_input and not user_input:
            raise ValidationError(f"{spec.label} needs {spec.input_label or 'input'}.")
        if len(user_input) > _MAX_INPUT_CHARS:
            raise ValidationError(f"The message is too long (max {_MAX_INPUT_CHARS} characters).")

        conversation = self.get_conversation(conversation_id)
        target = self._resolve_target(conversation)

        if spec.is_document_scope:
            async for event in self._answer_from_document(conversation, target, spec, user_input):
                yield event
        else:
            async for event in self._answer_from_excerpts(conversation, target, spec, user_input):
                yield event

    # ---- retrieval-scoped modes ----

    async def _answer_from_excerpts(
        self, conversation: Conversation, target: Target, spec: ModeSpec, question: str
    ) -> AsyncIterator[StreamEvent]:
        history = self._conversations.list_messages(conversation.id, limit=self._max_history)
        retrieved = await self._retrieval.retrieve_many(
            target.source_ids, self._retrieval_query(question, history)
        )
        citations = self._citations_from_retrieval(retrieved, target)
        yield StreamEvent("citations", [self._citation_payload(c) for c in citations])

        messages = build_retrieval_messages(
            title=target.title,
            kind=target.kind,
            instruction=spec.instruction,
            history=history,
            question=question,
            citations=citations,
            source_count=target.count,
        )
        async for event in self._generate(
            conversation, spec, question, citations, messages, emit_citations=False
        ):
            yield event

    # ---- document-scoped modes ----

    async def _answer_from_document(
        self, conversation: Conversation, target: Target, spec: ModeSpec, user_input: str
    ) -> AsyncIterator[StreamEvent]:
        chunks = await self._retrieval.all_chunks(target.source_ids)
        prompt_text = user_input or spec.default_prompt
        answer_parts: list[str] = []
        model_id: str | None = None

        async for event in self._documents.stream(
            spec=spec,
            title=target.title,
            kind=target.kind,
            chunks=chunks,
            user_input=user_input or None,
            source_titles=target.titles,
            source_count=target.count,
            affinity=conversation.id,
        ):
            if event.type == "token":
                answer_parts.append(event.data)
                yield event
            elif event.type == "model":
                model_id = event.data
                yield event
            elif event.type == "error":
                yield event
                return
            elif event.type == "progress":
                yield event

        answer = self._normalize(answer_parts)
        citations = self._citations_from_markers(answer, chunks, target)
        if citations:
            yield StreamEvent("citations", [self._citation_payload(c) for c in citations])
        yield self._persist(conversation, spec, prompt_text, answer, model_id, citations)

    # ---- shared generation ----

    async def _generate(
        self,
        conversation: Conversation,
        spec: ModeSpec,
        prompt_text: str,
        citations: list[Citation],
        messages: list[Any],
        *,
        emit_citations: bool,
    ) -> AsyncIterator[StreamEvent]:
        answer_parts: list[str] = []
        model_id: str | None = None
        try:
            # The conversation id spreads users across the pool while keeping one
            # thread on one model, so its answers stay in a consistent voice.
            async for event in self._router.stream(
                messages,
                temperature=spec.temperature,
                max_tokens=spec.max_tokens,
                affinity=conversation.id,
            ):
                if event.type == "model":
                    model_id = event.data
                    yield StreamEvent("model", event.data)
                elif event.type == "token":
                    answer_parts.append(event.data)
                    yield StreamEvent("token", event.data)
                elif event.type == "error":
                    yield StreamEvent("error", event.data)
                    return
        except NoModelAvailableError as exc:
            yield StreamEvent("error", exc.message)
            return

        answer = self._normalize(answer_parts)
        if emit_citations and citations:
            yield StreamEvent("citations", [self._citation_payload(c) for c in citations])
        yield self._persist(conversation, spec, prompt_text, answer, model_id, citations)

    def _persist(
        self,
        conversation: Conversation,
        spec: ModeSpec,
        prompt_text: str,
        answer: str,
        model_id: str | None,
        citations: list[Citation],
    ) -> StreamEvent:
        user_message = self._conversations.add_message(
            Message(
                id=new_id("msg"),
                conversation_id=conversation.id,
                role=MessageRole.USER,
                content=prompt_text,
                mode=spec.id,
            )
        )
        assistant_message = self._conversations.add_message(
            Message(
                id=new_id("msg"),
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                content=answer,
                model=model_id,
                mode=spec.id,
                citations=citations,
            )
        )
        return StreamEvent(
            "done",
            {
                "user_message_id": user_message.id,
                "assistant_message_id": assistant_message.id,
                "model": model_id,
                "mode": spec.id.value,
            },
        )

    # ---- helpers ----

    @staticmethod
    def _normalize(parts: list[str]) -> str:
        """Some models emit full-width brackets; store the canonical [n] form."""
        return _FULLWIDTH_CITATION.sub(r"[\1]", "".join(parts)).strip()

    @staticmethod
    def _retrieval_query(question: str, history: list[Message]) -> str:
        """Short follow-ups retrieve badly alone, so prepend the previous question."""
        if len(question.split()) <= 6:
            previous = [m for m in history if m.role is MessageRole.USER]
            if previous:
                return f"{previous[-1].content}\n{question}"
        return question

    def _resolve_target(self, conversation: Conversation) -> Target:
        if conversation.source_id:
            source = self._require_source(conversation.source_id)
            if source.status is not SourceStatus.READY:
                raise SourceNotReadyError(
                    f"The source is not ready yet (status: {source.status.value})."
                )
            return Target(title=source.title, kind=source.kind, sources=[source])

        collection = self._require_collection(conversation.collection_id or "")
        sources = [
            source
            for source in (self._sources.get(sid) for sid in collection.source_ids)
            if source is not None and source.status is SourceStatus.READY
        ]
        if not sources:
            raise SourceNotReadyError("This collection has no ready sources yet.")
        kinds = {source.kind for source in sources}
        return Target(
            title=collection.title,
            kind=kinds.pop() if len(kinds) == 1 else SourceKind.WEB,
            sources=sources,
        )

    def _citations_from_retrieval(
        self, retrieved: list[RetrievedChunk], target: Target
    ) -> list[Citation]:
        titles = target.titles
        citations: list[Citation] = []
        for position, item in enumerate(retrieved, start=1):
            citations.append(
                Citation(
                    index=position,
                    chunk_id=item.chunk.id,
                    text=item.chunk.text,
                    score=item.score,
                    metadata=self._citation_metadata(item.chunk, target, titles),
                )
            )
        return citations

    def _citations_from_markers(
        self, answer: str, chunks: list[Chunk], target: Target
    ) -> list[Citation]:
        """Build citations for the [n] markers a whole-document answer actually used."""
        if not chunks:
            return []
        titles = target.titles
        used = sorted(
            {
                int(match.group(1))
                for match in _CITATION_MARKER.finditer(answer)
                if 1 <= int(match.group(1)) <= len(chunks)
            }
        )
        citations: list[Citation] = []
        for position in used:
            chunk = chunks[position - 1]
            citations.append(
                Citation(
                    index=position,
                    chunk_id=chunk.id,
                    text=chunk.text,
                    score=1.0,
                    metadata=self._citation_metadata(chunk, target, titles),
                )
            )
        return citations

    def _citation_metadata(
        self, chunk: Chunk, target: Target, titles: dict[str, str]
    ) -> dict[str, Any]:
        metadata = dict(chunk.metadata)
        metadata["chunk_index"] = chunk.index
        if target.count > 1:
            metadata["source_id"] = chunk.source_id
            metadata["source_title"] = titles.get(chunk.source_id)
        start = metadata.get("start")
        if start is not None:
            seconds = int(float(start))
            metadata["timestamp"] = format_timestamp(float(start))
            link = self._deep_link(chunk.source_id, target, seconds)
            if link:
                metadata["link"] = link
        return metadata

    @staticmethod
    def _deep_link(source_id: str, target: Target, seconds: int) -> str | None:
        """A link back to the exact moment, where the source kind supports one."""
        source = next((s for s in target.sources if s.id == source_id), None)
        if source is None:
            return None
        if source.kind is SourceKind.YOUTUBE:
            base = source.metadata.get("canonical_url") or source.url
            separator = "&" if "?" in base else "?"
            return f"{base}{separator}t={seconds}s"
        if source.kind is SourceKind.AUDIO and source.url.startswith("http"):
            return f"{source.url}#t={seconds}"
        return None

    def _citation_payload(self, citation: Citation) -> dict[str, Any]:
        text = citation.text
        preview = (
            text
            if len(text) <= self._preview_chars
            else text[: self._preview_chars].rstrip() + "…"
        )
        return {
            "index": citation.index,
            "chunk_id": citation.chunk_id,
            "text": preview,
            "score": citation.score,
            "metadata": citation.metadata,
        }

    def _require_source(self, source_id: str) -> Source:
        source = self._sources.get(source_id)
        if source is None:
            raise NotFoundError("Source not found.")
        return source

    def _require_collection(self, collection_id: str):
        if self._collections is None:
            raise NotFoundError("Collections are not enabled.")
        collection = self._collections.get(collection_id)
        if collection is None:
            raise NotFoundError("Collection not found.")
        return collection
