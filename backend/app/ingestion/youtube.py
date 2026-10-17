"""YouTube loader: caption transcript with timestamps, title via oEmbed."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from typing import Any

import httpx

from app.core.exceptions import IngestionError
from app.domain.models import LoadedDocument, Segment, SourceKind
from app.domain.ports import SourceLoader

log = logging.getLogger(__name__)

_VIDEO_ID_RE = re.compile(r"(?:youtu\.be/|watch\?(?:.*&)?v=|/embed/|/shorts/|/live/|/v/)([A-Za-z0-9_-]{11})")
_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")


def extract_video_id(url: str) -> str | None:
    match = _VIDEO_ID_RE.search(url)
    return match.group(1) if match else None


def select_track(tracks: list, preferred: Sequence[str]) -> tuple[object | None, bool, str]:
    """Pick a caption track, returning (track, was_translated, why).

    Order of preference:

    1. captions the uploader wrote in a preferred language;
    2. auto-generated captions in a preferred language;
    3. the original spoken language, which is whatever the auto-generated track
       is in, preferring the uploader's own captions for it;
    4. any uploader-written captions;
    5. a machine translation into the first preferred language.

    Keeping the original language beats translating it: the embedding model is
    multilingual and the chat models read these languages directly, so a
    machine translation would only add errors. Step 5 therefore exists for
    completeness rather than as a normal path. Never pick an arbitrary
    language, which is what a plain "first track" fallback does on a video
    carrying dozens of community translations.
    """
    if not tracks:
        return None, False, "no tracks"

    def matches(track, codes: Sequence[str]) -> bool:
        code = (getattr(track, "language_code", "") or "").lower()
        return any(code == c.lower() or code.startswith(f"{c.lower()}-") for c in codes)

    written = [t for t in tracks if not getattr(t, "is_generated", False)]
    generated = [t for t in tracks if getattr(t, "is_generated", False)]

    # Walk the preferred languages in order, so the first entry really is the
    # first choice rather than whichever track happens to come back first.
    for code in preferred:
        for candidate in written:
            if matches(candidate, [code]):
                return candidate, False, f"uploader captions in a preferred language ({code})"
        for candidate in generated:
            if matches(candidate, [code]):
                return candidate, False, f"auto-generated captions in a preferred language ({code})"

    # The auto-generated track is in the language actually spoken.
    spoken = [getattr(t, "language_code", "") for t in generated if getattr(t, "language_code", "")]
    for code in spoken:
        for candidate in written:
            if matches(candidate, [code]):
                return candidate, False, f"uploader captions in the spoken language ({code})"
    if generated:
        code = spoken[0] if spoken else "unknown"
        return generated[0], False, f"auto-generated captions in the spoken language ({code})"

    if written:
        return written[0], False, "uploader captions (no spoken-language track to compare)"

    for candidate in tracks:
        if getattr(candidate, "is_translatable", False):
            try:
                return candidate.translate(preferred[0]), True, f"machine translation into {preferred[0]}"
            except Exception:  # noqa: BLE001 - try the next track
                continue
    return tracks[0], False, "only track available"


class YouTubeLoader(SourceLoader):
    kind = SourceKind.YOUTUBE

    def __init__(self, http: httpx.AsyncClient, languages: list[str]):
        self._http = http
        self._languages = languages

    def supports(self, url: str) -> bool:
        lowered = url.lower()
        return any(host in lowered for host in _HOSTS) and extract_video_id(url) is not None

    async def load(self, url: str) -> LoadedDocument:
        video_id = extract_video_id(url)
        if not video_id:
            raise IngestionError("Could not find a YouTube video id in that URL.")

        transcript_task = asyncio.to_thread(self._fetch_transcript, video_id)
        meta_task = self._fetch_oembed(video_id)
        (segments, track_info), oembed = await asyncio.gather(transcript_task, meta_task)

        title = oembed.get("title") or f"YouTube video {video_id}"
        metadata: dict[str, Any] = {
            "video_id": video_id,
            "canonical_url": f"https://www.youtube.com/watch?v={video_id}",
            "author": oembed.get("author_name"),
            "thumbnail_url": oembed.get("thumbnail_url"),
            **track_info,
        }
        return LoadedDocument(title=title, kind=self.kind, segments=segments, metadata=metadata)

    # ---- internals ----

    def _fetch_transcript(self, video_id: str) -> tuple[list[Segment], dict[str, Any]]:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import TranscriptsDisabled, VideoUnavailable

        api = YouTubeTranscriptApi()
        try:
            transcripts = api.list(video_id)
        except TranscriptsDisabled as exc:
            raise IngestionError("This video has captions disabled, so there is nothing to read.") from exc
        except VideoUnavailable as exc:
            raise IngestionError("This video is unavailable or private.") from exc
        except Exception as exc:  # network / IP block / parsing
            raise IngestionError(
                "YouTube refused the transcript request. This usually happens on cloud IPs; "
                f"try again from a home network. ({exc.__class__.__name__})"
            ) from exc

        track, translated, reason = select_track(list(transcripts), self._languages)
        if track is None:
            raise IngestionError("No captions were found for this video.")
        log.info("caption track for %s: %s (%s)", video_id, getattr(track, "language_code", "?"), reason)

        try:
            fetched = track.fetch()
        except Exception as exc:  # noqa: BLE001
            raise IngestionError(f"Could not download the caption track ({exc.__class__.__name__}).") from exc

        segments: list[Segment] = []
        for snippet in fetched:
            text = " ".join(snippet.text.split())
            if not text or text in {"[Music]", "[Applause]", "[Laughter]"}:
                continue
            segments.append(Segment(text=text, start=snippet.start, end=snippet.start + snippet.duration))
        if not segments:
            raise IngestionError("The caption track is empty.")

        info = {
            "language": getattr(track, "language_code", None),
            "is_generated": getattr(track, "is_generated", None),
            "translated": translated,
            "track_choice": reason,
        }
        return segments, info

    async def _fetch_oembed(self, video_id: str) -> dict[str, Any]:
        try:
            response = await self._http.get(
                "https://www.youtube.com/oembed",
                params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
            )
            if response.status_code == 200:
                return response.json()
            log.info("oEmbed returned %s for %s", response.status_code, video_id)
        except httpx.HTTPError as exc:
            log.info("oEmbed lookup failed for %s: %s", video_id, exc)
        return {}
