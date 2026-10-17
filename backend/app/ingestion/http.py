"""Generic loader for anything reachable over HTTP.

This is the catch-all: HTML pages, PDFs, Office documents, EPUB, plain text
and direct audio links all arrive as bytes with a content type, which the
payload processor dispatches. When a page yields very little text and the
browser fallback is enabled, the page is re-fetched through a headless
browser before giving up.
"""

from __future__ import annotations

import logging

import httpx

from app.core.exceptions import IngestionError
from app.domain.models import LoadedDocument, SourceKind
from app.domain.ports import SourceLoader
from app.ingestion.browser import BrowserRenderer
from app.ingestion.processor import PayloadProcessor

log = logging.getLogger(__name__)


class HttpLoader(SourceLoader):
    kind = SourceKind.WEB
    label = "web"  # also PDF, Office, EPUB and audio, detected from the response

    def __init__(
        self,
        http: httpx.AsyncClient,
        processor: PayloadProcessor,
        *,
        renderer: BrowserRenderer | None = None,
        render_below_chars: int = 600,
    ):
        self._http = http
        self._processor = processor
        self._renderer = renderer
        self._render_below_chars = render_below_chars

    def supports(self, url: str) -> bool:
        return url.lower().startswith(("http://", "https://"))

    async def load(self, url: str) -> LoadedDocument:
        response = await self._fetch(url)
        content_type = response.headers.get("content-type", "")
        final_url = str(response.url)

        try:
            document = await self._processor.process(
                response.content, content_type=content_type, reference=final_url
            )
        except IngestionError:
            rendered = await self._render(url)
            if rendered is None:
                raise
            document = await self._processor.process(
                rendered.encode("utf-8"), content_type="text/html", reference=final_url
            )
            document.metadata["rendered"] = True

        # Thin but not empty: a rendered retry often finds the real content.
        if (
            document.kind is SourceKind.WEB
            and len(document.text) < self._render_below_chars
            and not document.metadata.get("rendered")
        ):
            rendered = await self._render(url)
            if rendered is not None:
                try:
                    better = await self._processor.process(
                        rendered.encode("utf-8"), content_type="text/html", reference=final_url
                    )
                except IngestionError:
                    better = None
                if better is not None and len(better.text) > len(document.text):
                    better.metadata["rendered"] = True
                    document = better

        document.metadata.setdefault("final_url", final_url)
        return document

    # ---- internals ----

    async def _fetch(self, url: str) -> httpx.Response:
        try:
            response = await self._http.get(url)
        except httpx.TimeoutException as exc:
            raise IngestionError("The website took too long to respond.") from exc
        except httpx.HTTPError as exc:
            raise IngestionError(f"Could not reach the website ({exc.__class__.__name__}).") from exc

        if response.status_code >= 400:
            raise IngestionError(
                f"The website answered with HTTP {response.status_code}. "
                "It may block automated access or the page may not exist."
            )
        return response

    async def _render(self, url: str) -> str | None:
        if self._renderer is None:
            return None
        log.info("retrying %s through a headless browser", url)
        return await self._renderer.render(url)
