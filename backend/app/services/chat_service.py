"""Conversations, modes and grounded streamed answers.

A conversation targets exactly one thing: a single source, or a collection
of them. Each message carries a mode, which decides whether the answer comes
from the best matching excerpts or from the whole document. Every entry
point takes the owner, and nothing is returned that the owner does not hold.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.core.exceptions import (
    IngestionError,
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
    Collection,
    Conversation,
    Message,
    MessageRole,
    RetrievedChunk,
    Source,
    SourceKind,
    SourceStatus,
)
from app.domain.ports import CollectionRepository, ConversationRepository, SourceRepository, Transcriber
from app.providers.llm.router import ModelRouter
from app.services.document_service import DocumentService
from app.services.events import StreamEvent
from app.services.language import detect_language
from app.services.modes import ModeSpec, get_mode
from app.services.prompts import build_retrieval_messages, format_timestamp, spoken
from app.services.retrieval_service import RetrievalService
from app.services.transcript import clean_transcript

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
    def document_ids(self) -> list[str]:
        return [source.document_id for source in self.sources]

    @property
    def by_document(self) -> dict[str, Source]:
        return {source.document_id: source for source in self.sources}

    @property
    def titles(self) -> dict[str, str]:
        """Source titles keyed by document id, which is what chunks carry."""
        return {source.document_id: source.title for source in self.sources}

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
        transcriber: Transcriber | None = None,
        max_history_messages: int = 8,
        citation_preview_chars: int = 420,
        max_voice_bytes: int = 6 * 1024 * 1024,
    ):
        self._sources = sources
        self._conversations = conversations
        self._collections = collections
        self._retrieval = retrieval
        self._documents = documents
        self._router = router
        self._transcriber = transcriber
        self._max_history = max_history_messages
        self._preview_chars = citation_preview_chars
        self._max_voice_bytes = max_voice_bytes

    @property
    def voice_available(self) -> bool:
        return self._transcriber is not None and self._transcriber.available

    # ---- conversations ----

    async def create_conversation(
        self,
        owner_id: str,
        source_id: str | None = None,
        title: str | None = None,
        collection_id: str | None = None,
    ) -> Conversation:
        if bool(source_id) == bool(collection_id):
            raise ValidationError("Provide exactly one of source_id or collection_id.")
        if source_id:
            target_title = (await self._require_source(owner_id, source_id)).title
        else:
            target_title = (await self._require_collection(owner_id, collection_id or "")).title
        conversation = Conversation(
            id=new_id("cnv"),
            owner_id=owner_id,
            title=title or target_title,
            source_id=source_id,
            collection_id=collection_id,
        )
        return await self._conversations.add(conversation)

    async def get_conversation(self, owner_id: str, conversation_id: str) -> Conversation:
        conversation = await self._conversations.get(owner_id, conversation_id)
        if conversation is None:
            raise NotFoundError("Conversation not found.")
        return conversation

    async def list_conversations(self, owner_id: str, source_id: str) -> list[Conversation]:
        await self._require_source(owner_id, source_id)
        return await self._conversations.list_for_source(owner_id, source_id)

    async def list_collection_conversations(self, owner_id: str, collection_id: str) -> list[Conversation]:
        await self._require_collection(owner_id, collection_id)
        return await self._conversations.list_for_collection(owner_id, collection_id)

    async def delete_conversation(self, owner_id: str, conversation_id: str) -> None:
        await self.get_conversation(owner_id, conversation_id)
        await self._conversations.delete(owner_id, conversation_id)

    async def list_messages(self, owner_id: str, conversation_id: str) -> list[Message]:
        await self.get_conversation(owner_id, conversation_id)
        return await self._conversations.list_messages(conversation_id)

    # ---- answering ----

    async def stream_answer(
        self,
        owner_id: str,
        conversation_id: str,
        content: str,
        mode: ChatMode | str = ChatMode.QA,
        *,
        spoken_answer: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        spec = get_mode(mode)
        if spoken_answer:
            # Same task, written to be heard rather than read.
            spec = dataclasses.replace(spec, instruction=spoken(spec.instruction))
        user_input = (content or "").strip()
        if spec.needs_input and not user_input:
            raise ValidationError(f"{spec.label} needs {spec.input_label or 'input'}.")
        if len(user_input) > _MAX_INPUT_CHARS:
            raise ValidationError(f"The message is too long (max {_MAX_INPUT_CHARS} characters).")

        conversation = await self.get_conversation(owner_id, conversation_id)
        target = await self._resolve_target(conversation)

        if spec.is_document_scope:
            async for event in self._answer_from_document(conversation, target, spec, user_input):
                yield event
        else:
            async for event in self._answer_from_excerpts(conversation, target, spec, user_input):
                yield event

    async def stream_voice_answer(
        self,
        owner_id: str,
        conversation_id: str,
        audio: bytes,
        filename: str,
        mode: ChatMode | str = ChatMode.QA,
    ) -> AsyncIterator[StreamEvent]:
        """A spoken question: transcribe it, say what was heard, then answer aloud-style.

        The transcript is the first event so the user sees their words while
        the answer is still being produced, and can spot a misheard name.
        """
        if not self.voice_available:
            raise ValidationError(
                "Voice needs a speech-to-text provider. Set GROQ_API_KEY to enable Whisper transcription."
            )
        if not audio:
            raise ValidationError("The recording is empty.")
        if len(audio) > self._max_voice_bytes:
            raise ValidationError(
                f"The recording is {len(audio) / (1024 * 1024):.1f} MB; a spoken question is capped at "
                f"{self._max_voice_bytes // (1024 * 1024)} MB."
            )
        await self.get_conversation(owner_id, conversation_id)

        try:
            segments, meta = await self._transcriber.transcribe(audio, filename)  # type: ignore[union-attr]
        except IngestionError as exc:
            yield StreamEvent("error", f"I could not transcribe that. {exc.message}")
            return
        heard = " ".join(" ".join(segment.text.split()) for segment in segments if segment.text.strip()).strip()
        text = clean_transcript(heard)
        if not text:
            yield StreamEvent("error", "I did not catch anything. Try speaking a little closer to the microphone.")
            return
        yield StreamEvent(
            "transcript",
            {
                "text": text,
                "language": meta.get("language"),
                "duration_seconds": meta.get("duration_seconds"),
            },
        )
        async for event in self.stream_answer(owner_id, conversation_id, text, mode, spoken_answer=True):
            yield event

    # ---- retrieval-scoped modes ----

    async def _answer_from_excerpts(
        self, conversation: Conversation, target: Target, spec: ModeSpec, question: str
    ) -> AsyncIterator[StreamEvent]:
        history = await self._conversations.list_messages(conversation.id, limit=self._max_history)
        retrieved = await self._retrieval.retrieve_many(
            target.document_ids, self._retrieval_query(question, history)
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
            conversation, spec, question, citations, messages, emit_citations=False, language=detect_language(question)
        ):
            yield event

    # ---- document-scoped modes ----

    async def _answer_from_document(
        self, conversation: Conversation, target: Target, spec: ModeSpec, user_input: str
    ) -> AsyncIterator[StreamEvent]:
        chunks = await self._retrieval.all_chunks(target.document_ids)
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
        language = detect_language(user_input) if user_input else detect_language(answer)
        yield await self._persist(conversation, spec, prompt_text, answer, model_id, citations, language)

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
        language: str | None = None,
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
        yield await self._persist(conversation, spec, prompt_text, answer, model_id, citations, language)

    async def _persist(
        self,
        conversation: Conversation,
        spec: ModeSpec,
        prompt_text: str,
        answer: str,
        model_id: str | None,
        citations: list[Citation],
        language: str | None = None,
    ) -> StreamEvent:
        user_message = await self._conversations.add_message(
            Message(
                id=new_id("msg"),
                conversation_id=conversation.id,
                role=MessageRole.USER,
                content=prompt_text,
                mode=spec.id,
            )
        )
        assistant_message = await self._conversations.add_message(
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
                # The language of the question, named so a voice can be chosen to read the answer.
                "language": language,
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

    async def _resolve_target(self, conversation: Conversation) -> Target:
        owner_id = conversation.owner_id
        if conversation.source_id:
            source = await self._require_source(owner_id, conversation.source_id)
            if source.status is not SourceStatus.READY:
                raise SourceNotReadyError(f"The source is not ready yet (status: {source.status.value}).")
            return Target(title=source.title, kind=source.kind, sources=[source])

        collection = await self._require_collection(owner_id, conversation.collection_id or "")
        members = await self._sources.get_many(owner_id, collection.source_ids)
        sources = [source for source in members if source.status is SourceStatus.READY]
        if not sources:
            raise SourceNotReadyError("This collection has no ready sources yet.")
        kinds = {source.kind for source in sources}
        return Target(
            title=collection.title,
            kind=kinds.pop() if len(kinds) == 1 else SourceKind.WEB,
            sources=sources,
        )

    def _citations_from_retrieval(self, retrieved: list[RetrievedChunk], target: Target) -> list[Citation]:
        by_document = target.by_document
        citations: list[Citation] = []
        for position, item in enumerate(retrieved, start=1):
            citations.append(
                Citation(
                    index=position,
                    chunk_id=item.chunk.id,
                    text=item.chunk.text,
                    score=item.score,
                    metadata=self._citation_metadata(item.chunk, target, by_document),
                )
            )
        return citations

    def _citations_from_markers(self, answer: str, chunks: list[Chunk], target: Target) -> list[Citation]:
        """Build citations for the [n] markers a whole-document answer actually used."""
        if not chunks:
            return []
        by_document = target.by_document
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
                    metadata=self._citation_metadata(chunk, target, by_document),
                )
            )
        return citations

    def _citation_metadata(self, chunk: Chunk, target: Target, by_document: dict[str, Source]) -> dict[str, Any]:
        metadata = dict(chunk.metadata)
        metadata["chunk_index"] = chunk.index
        source = by_document.get(chunk.document_id)
        if target.count > 1 and source is not None:
            metadata["source_id"] = source.id
            metadata["source_title"] = source.title
        start = metadata.get("start")
        if start is not None:
            seconds = int(float(start))
            metadata["timestamp"] = format_timestamp(float(start))
            link = self._deep_link(source, seconds)
            if link:
                metadata["link"] = link
        return metadata

    @staticmethod
    def _deep_link(source: Source | None, seconds: int) -> str | None:
        """A link back to the exact moment, where the source kind supports one."""
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
        preview = text if len(text) <= self._preview_chars else text[: self._preview_chars].rstrip() + "…"
        return {
            "index": citation.index,
            "chunk_id": citation.chunk_id,
            "text": preview,
            "score": citation.score,
            "metadata": citation.metadata,
        }

    async def _require_source(self, owner_id: str, source_id: str) -> Source:
        source = await self._sources.get(owner_id, source_id)
        if source is None:
            raise NotFoundError("Source not found.")
        return source

    async def _require_collection(self, owner_id: str, collection_id: str) -> Collection:
        if self._collections is None:
            raise NotFoundError("Collections are not enabled.")
        collection = await self._collections.get(owner_id, collection_id)
        if collection is None:
            raise NotFoundError("Collection not found.")
        return collection
