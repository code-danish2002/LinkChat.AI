"""Turns a fetched or uploaded payload into a LoadedDocument.

Both the HTTP loader and the upload loader end up holding bytes plus a
content type, so the dispatch between text extraction and audio
transcription lives here rather than in either loader.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.exceptions import IngestionError
from app.domain.models import LoadedDocument, Segment, SourceKind
from app.domain.ports import Transcriber
from app.ingestion.extractors import detect_kind, extract_payload

log = logging.getLogger(__name__)


class PayloadProcessor:
    def __init__(
        self,
        *,
        transcriber: Transcriber | None = None,
        max_chars: int = 2_000_000,
        min_chars: int = 120,
        max_audio_bytes: int = 24 * 1024 * 1024,
    ):
        self._transcriber = transcriber
        self._max_chars = max_chars
        self._min_chars = min_chars
        self._max_audio_bytes = max_audio_bytes

    def is_audio(self, content_type: str, reference: str) -> bool:
        return detect_kind(content_type, reference) is SourceKind.AUDIO

    async def process(
        self, payload: bytes, *, content_type: str, reference: str, extra_metadata: dict[str, Any] | None = None
    ) -> LoadedDocument:
        if self.is_audio(content_type, reference):
            document = await self._process_audio(payload, reference)
        else:
            document = await self._process_text(payload, content_type, reference)
        if extra_metadata:
            document.metadata.update({k: v for k, v in extra_metadata.items() if v is not None})
        return document

    # ---- text-like payloads ----

    async def _process_text(self, payload: bytes, content_type: str, reference: str) -> LoadedDocument:
        title, text, meta, kind = await asyncio.to_thread(
            extract_payload, payload, content_type=content_type, reference=reference
        )
        text = text[: self._max_chars]
        if len(text) < self._min_chars:
            raise IngestionError(
                "Almost no readable text could be extracted. The page is probably rendered with "
                "JavaScript or protected against automated access."
            )
        meta["content_type"] = (content_type or "").split(";")[0].strip() or None
        return LoadedDocument(
            title=title,
            kind=kind,
            segments=[Segment(text=text)],
            metadata={k: v for k, v in meta.items() if v is not None},
        )

    # ---- audio payloads ----

    async def _process_audio(self, payload: bytes, reference: str) -> LoadedDocument:
        if self._transcriber is None or not self._transcriber.available:
            raise IngestionError(
                "Audio needs a speech-to-text provider. Set GROQ_API_KEY to enable Whisper "
                "transcription."
            )
        if len(payload) > self._max_audio_bytes:
            megabytes = self._max_audio_bytes / (1024 * 1024)
            raise IngestionError(
                f"The audio file is {len(payload) / (1024 * 1024):.1f} MB, above the "
                f"{megabytes:.0f} MB limit for hosted transcription."
            )

        filename = reference.split("?")[0].rstrip("/").rsplit("/", 1)[-1] or "audio.mp3"
        segments, meta = await self._transcriber.transcribe(payload, filename)
        if not segments:
            raise IngestionError("The transcription came back empty; the audio may be silent.")
        title = meta.pop("title", None) or filename
        return LoadedDocument(
            title=title,
            kind=SourceKind.AUDIO,
            segments=segments,
            metadata={k: v for k, v in meta.items() if v is not None},
        )
