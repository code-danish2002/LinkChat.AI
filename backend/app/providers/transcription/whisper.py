"""Speech-to-text through OpenAI-compatible `/audio/transcriptions` endpoints.

Groq serves Whisper on the same base URL and key as its chat models, so the
transcription pool reuses the provider definitions in models.yaml and the
same first-one-that-works failover as chat.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.exceptions import IngestionError
from app.domain.models import Segment
from app.domain.ports import Transcriber
from app.providers.llm.registry import ModelRegistry

log = logging.getLogger(__name__)

_MIME_BY_EXTENSION = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".webm": "audio/webm",
}


class WhisperTranscriber(Transcriber):
    def __init__(self, registry: ModelRegistry, http: httpx.AsyncClient, *, timeout_seconds: float = 300.0):
        self._registry = registry
        self._http = http
        self._timeout = httpx.Timeout(timeout_seconds, connect=15.0)

    @property
    def available(self) -> bool:
        return bool(self._registry.active_transcription_models())

    async def transcribe(self, payload: bytes, filename: str) -> tuple[list[Segment], dict[str, Any]]:
        candidates = self._registry.active_transcription_models()
        if not candidates:
            raise IngestionError("No speech-to-text provider is configured.")

        errors: list[str] = []
        for spec, provider in candidates:
            try:
                data = await self._request(provider, spec.model, payload, filename)
            except Exception as exc:  # noqa: BLE001 - try the next model
                errors.append(f"{spec.id}: {exc}")
                log.warning("transcription with %s failed: %s", spec.id, exc)
                continue

            segments = _to_segments(data)
            if not segments:
                errors.append(f"{spec.id}: empty transcript")
                continue
            meta = {
                "transcription_model": spec.id,
                "language": data.get("language"),
                "duration_seconds": round(float(data["duration"]), 2) if data.get("duration") else None,
            }
            return segments, meta

        raise IngestionError("Transcription failed on every configured model. " + "; ".join(errors[-2:]))

    async def _request(self, provider, model: str, payload: bytes, filename: str) -> dict[str, Any]:
        extension = filename[filename.rfind(".") :].lower() if "." in filename else ".mp3"
        files = {"file": (filename, payload, _MIME_BY_EXTENSION.get(extension, "application/octet-stream"))}
        form = {
            "model": model,
            "response_format": "verbose_json",
            "timestamp_granularities[]": "segment",
        }
        response = await self._http.post(
            f"{provider.base_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {provider.api_key}", **provider.extra_headers},
            files=files,
            data=form,
            timeout=self._timeout,
        )
        if response.status_code >= 400:
            detail = " ".join(response.text.split())[:200]
            raise IngestionError(f"HTTP {response.status_code}: {detail}")
        return response.json()


def _to_segments(payload: dict[str, Any]) -> list[Segment]:
    raw = payload.get("segments")
    if isinstance(raw, list) and raw:
        segments: list[Segment] = []
        for item in raw:
            text = " ".join(str(item.get("text", "")).split())
            if not text:
                continue
            start = item.get("start")
            end = item.get("end")
            segments.append(
                Segment(
                    text=text,
                    start=float(start) if start is not None else None,
                    end=float(end) if end is not None else None,
                )
            )
        if segments:
            return segments
    text = " ".join(str(payload.get("text", "")).split())
    return [Segment(text=text)] if text else []
