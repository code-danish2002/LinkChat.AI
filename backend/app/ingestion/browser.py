"""Headless-browser rendering for pages that build their content in JavaScript.

Plain fetching returns an empty shell for single-page apps and many shops,
so the HTTP loader retries through a real browser when extraction came back
thin. Playwright is an optional dependency: when it or its browser binary is
missing, the renderer reports itself unavailable and ingestion falls back to
the non-rendered text instead of failing.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class BrowserRenderer:
    def __init__(self, *, timeout_seconds: float = 25.0, wait_until: str = "networkidle"):
        self._timeout_ms = int(timeout_seconds * 1000)
        self._wait_until = wait_until
        self._unavailable_reason: str | None = None

    @property
    def unavailable_reason(self) -> str | None:
        return self._unavailable_reason

    async def render(self, url: str) -> str | None:
        """Fully rendered HTML, or None when rendering is not possible."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self._unavailable_reason = (
                "playwright is not installed (pip install -r requirements-optional.txt)"
            )
            log.info("browser fallback skipped: %s", self._unavailable_reason)
            return None

        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(args=["--no-sandbox"])
                try:
                    page = await browser.new_page(viewport={"width": 1280, "height": 1600})
                    await page.goto(url, timeout=self._timeout_ms, wait_until=self._wait_until)
                    return await page.content()
                finally:
                    await browser.close()
        except Exception as exc:  # noqa: BLE001 - any failure means "no rendered HTML"
            message = str(exc)
            if "Executable doesn't exist" in message or "playwright install" in message:
                self._unavailable_reason = "chromium is not installed (run: playwright install chromium)"
                log.warning("browser fallback unavailable: %s", self._unavailable_reason)
            else:
                log.info("browser rendering failed for %s: %s", url, exc.__class__.__name__)
            return None
