"""Request rate limiting.

Without authentication there is no user to limit, so requests are grouped by
client address. Two buckets, because the costs are wildly different: answering
a question spends the model pool's per-minute token budget, and ingesting a
source spends about sixteen seconds of CPU.

A sliding window per key, held in memory. That means the limit is per process,
which is correct here because the application already runs as a single process
(see the SQLite note in the README). Moving to several replicas would need a
shared store such as Redis.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

WINDOW_SECONDS = 60.0


class SlidingWindow:
    """Counts events per key over the last minute."""

    def __init__(self, limit: int, clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> tuple[bool, int, float]:
        """Record an attempt. Returns (allowed, remaining, retry_after seconds)."""
        if self.limit <= 0:
            return True, 0, 0.0
        now = self._clock()
        hits = self._hits[key]
        while hits and hits[0] <= now - WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= self.limit:
            return False, 0, max(0.0, hits[0] + WINDOW_SECONDS - now)
        hits.append(now)
        return True, self.limit - len(hits), 0.0

    def forget(self, before_seconds: float = WINDOW_SECONDS * 5) -> None:
        """Drop keys that have gone quiet, so memory does not grow forever."""
        cutoff = self._clock() - before_seconds
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= cutoff]:
            del self._hits[key]


def client_key(request: Request) -> str:
    """The caller's address, trusting a proxy header only if present."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    #: Paths that must stay reachable for monitoring.
    EXEMPT = ("/api/v1/health", "/docs", "/openapi.json", "/redoc", "/")

    def __init__(self, app, *, chat_per_minute: int, ingest_per_minute: int, general_per_minute: int):
        super().__init__(app)
        self._chat = SlidingWindow(chat_per_minute)
        self._ingest = SlidingWindow(ingest_per_minute)
        self._general = SlidingWindow(general_per_minute)
        self._requests_since_sweep = 0

    def _bucket(self, request: Request) -> tuple[SlidingWindow, str]:
        path, method = request.url.path, request.method
        if method == "POST" and path.endswith("/messages"):
            return self._chat, "chat"
        if method == "POST" and (path == "/api/v1/sources" or path.endswith("/sources/upload")):
            return self._ingest, "ingest"
        return self._general, "general"

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in self.EXEMPT:
            return await call_next(request)

        # Periodically forget quiet callers rather than keeping every address seen.
        self._requests_since_sweep += 1
        if self._requests_since_sweep >= 1000:
            self._requests_since_sweep = 0
            for window in (self._chat, self._ingest, self._general):
                window.forget()

        window, name = self._bucket(request)
        allowed, remaining, retry_after = window.check(client_key(request))
        if not allowed:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(max(1, int(retry_after) + 1))},
                content={
                    "error": {
                        "code": "rate_limited",
                        "message": (
                            f"Too many {name} requests. The limit is {window.limit} per minute. "
                            f"Try again in {int(retry_after) + 1}s."
                        ),
                        "details": {"bucket": name, "limit": window.limit},
                    }
                },
            )
        response = await call_next(request)
        if window.limit > 0:
            response.headers["X-RateLimit-Limit"] = str(window.limit)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
