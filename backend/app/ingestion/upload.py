"""Loader for files the user uploaded instead of linking.

Uploads are stored under `data/uploads` and identified by a pseudo-URL of
the form `upload://<digest>/<filename>`. That keeps one identity column for
every source, makes de-duplication by content free, and lets uploads flow
through exactly the same pipeline as a link.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path

from app.core.exceptions import IngestionError, ValidationError
from app.domain.models import LoadedDocument, SourceKind
from app.domain.ports import SourceLoader
from app.ingestion.processor import PayloadProcessor

UPLOAD_SCHEME = "upload://"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str) -> str:
    cleaned = _SAFE_NAME.sub("_", (name or "").strip().rsplit("/", 1)[-1]).strip("._")
    return cleaned[:120] or "upload"


def upload_url(digest: str, filename: str) -> str:
    return f"{UPLOAD_SCHEME}{digest}/{safe_filename(filename)}"


def parse_upload_url(url: str) -> tuple[str, str]:
    if not url.startswith(UPLOAD_SCHEME):
        raise ValidationError("Not an upload reference.")
    remainder = url[len(UPLOAD_SCHEME) :]
    digest, _, filename = remainder.partition("/")
    if not digest or not filename:
        raise ValidationError("Malformed upload reference.")
    return digest, filename


class UploadStore:
    """Content-addressed storage for uploaded files."""

    def __init__(self, root: Path):
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def path_for(self, digest: str, filename: str) -> Path:
        name = safe_filename(filename)
        extension = name[name.rfind(".") :].lower() if "." in name else ""
        return self._root / f"{digest}{extension}"

    def save(self, payload: bytes, filename: str) -> tuple[str, Path]:
        digest = hashlib.sha256(payload).hexdigest()[:24]
        path = self.path_for(digest, filename)
        if not path.exists():
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_bytes(payload)
            temporary.replace(path)
        return digest, path

    def delete(self, digest: str, filename: str) -> None:
        try:
            self.path_for(digest, filename).unlink()
        except FileNotFoundError:
            pass


class UploadLoader(SourceLoader):
    kind = SourceKind.TEXT  # placeholder; the real kind comes from the payload
    label = "upload"

    def __init__(self, store: UploadStore, processor: PayloadProcessor):
        self._store = store
        self._processor = processor

    def supports(self, url: str) -> bool:
        return url.startswith(UPLOAD_SCHEME)

    async def load(self, url: str) -> LoadedDocument:
        digest, filename = parse_upload_url(url)
        path = self._store.path_for(digest, filename)
        if not path.exists():
            raise IngestionError("The uploaded file is no longer on disk; upload it again.")
        payload = await asyncio.to_thread(path.read_bytes)
        document = await self._processor.process(
            payload, content_type="", reference=filename, extra_metadata={"filename": filename, "uploaded": True}
        )
        if not document.title or document.title == filename:
            document.title = filename
        return document
