"""Splits loaded documents into overlapping chunks.

Plain text uses a recursive splitter (paragraphs -> lines -> sentences ->
words). Timed transcripts are grouped by caption segment so every chunk
keeps its start/end time for timestamped citations.
"""

from __future__ import annotations

import re

from app.core.ids import new_id
from app.domain.models import Chunk, LoadedDocument, Segment

_SEPARATORS = ["\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " ", ""]
_WS = re.compile(r"\s+")


class Chunker:
    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 150):
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    # ---- public ----

    def chunk(self, document: LoadedDocument, source_id: str) -> list[Chunk]:
        if document.is_timed:
            pieces = self._split_timed(document.segments)
        else:
            pieces = [(piece, {}) for piece in self.split_text(document.text)]
        return [
            Chunk(id=new_id("chk"), source_id=source_id, index=i, text=text, metadata=meta)
            for i, (text, meta) in enumerate(pieces)
            if text.strip()
        ]

    def split_text(self, text: str) -> list[str]:
        text = text.strip()
        if not text:
            return []
        return [p for p in self._recursive_split(text, _SEPARATORS) if p.strip()]

    # ---- plain text ----

    def _recursive_split(self, text: str, separators: list[str]) -> list[str]:
        if len(text) <= self.chunk_size:
            return [text]
        separator = separators[-1]
        remaining: list[str] = []
        for i, sep in enumerate(separators):
            if sep == "" or sep in text:
                separator, remaining = sep, separators[i + 1 :]
                break

        parts = list(text) if separator == "" else [p for p in text.split(separator) if p]
        pieces: list[str] = []
        buffer: list[str] = []
        buffer_len = 0
        sep_len = len(separator)

        def flush() -> None:
            if buffer:
                pieces.append(separator.join(buffer).strip())

        for part in parts:
            if len(part) > self.chunk_size and remaining:
                flush()
                buffer, buffer_len = [], 0
                pieces.extend(self._recursive_split(part, remaining))
                continue
            projected = buffer_len + len(part) + (sep_len if buffer else 0)
            if projected > self.chunk_size and buffer:
                flush()
                # keep a tail of the buffer as overlap
                overlap: list[str] = []
                overlap_len = 0
                for item in reversed(buffer):
                    if overlap_len + len(item) + sep_len > self.chunk_overlap:
                        break
                    overlap.insert(0, item)
                    overlap_len += len(item) + sep_len
                buffer, buffer_len = overlap, overlap_len
            buffer.append(part)
            buffer_len += len(part) + (sep_len if len(buffer) > 1 else 0)
        flush()
        return [p for p in pieces if p]

    # ---- timed transcripts ----

    def _split_timed(self, segments: list[Segment]) -> list[tuple[str, dict]]:
        pieces: list[tuple[str, dict]] = []
        window: list[Segment] = []
        window_len = 0

        def emit() -> None:
            if not window:
                return
            text = _WS.sub(" ", " ".join(s.text for s in window)).strip()
            pieces.append(
                (
                    text,
                    {
                        "start": round(window[0].start or 0.0, 2),
                        "end": round(window[-1].end or window[-1].start or 0.0, 2),
                    },
                )
            )

        for seg in segments:
            if window_len + len(seg.text) + 1 > self.chunk_size and window:
                emit()
                overlap: list[Segment] = []
                overlap_len = 0
                for item in reversed(window):
                    if overlap_len + len(item.text) + 1 > self.chunk_overlap:
                        break
                    overlap.insert(0, item)
                    overlap_len += len(item.text) + 1
                window, window_len = overlap, overlap_len
            window.append(seg)
            window_len += len(seg.text) + 1
        emit()
        return pieces
