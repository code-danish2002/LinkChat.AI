"""Keeps multi-request work inside a provider's tokens-per-minute ceiling.

Free tiers cap tokens per minute far below their context windows, so work
that needs several requests has to pace itself. This is a sliding window
over the last minute: before each request the caller reserves its estimated
cost, and the pacer waits only when the window is genuinely full.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)

WINDOW_SECONDS = 60.0


class TokenPacer:
    def __init__(
        self,
        tokens_per_minute: int,
        *,
        max_wait_seconds: float = 90.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._budget = max(0, tokens_per_minute)
        self._max_wait = max_wait_seconds
        self._clock = clock
        self._sleep = sleep
        self._spent: deque[tuple[float, int]] = deque()

    @property
    def enabled(self) -> bool:
        return self._budget > 0

    def _prune(self) -> None:
        cutoff = self._clock() - WINDOW_SECONDS
        while self._spent and self._spent[0][0] <= cutoff:
            self._spent.popleft()

    @property
    def used(self) -> int:
        self._prune()
        return sum(tokens for _, tokens in self._spent)

    async def reserve(self, tokens: int) -> float:
        """Wait until `tokens` fit in the current window. Returns seconds waited."""
        if not self.enabled or tokens <= 0:
            self._record(tokens)
            return 0.0

        waited = 0.0
        while True:
            self._prune()
            used = sum(spent for _, spent in self._spent)
            if not self._spent or used + tokens <= self._budget:
                break
            # Wait for the oldest reservation to leave the window.
            oldest_at = self._spent[0][0]
            delay = max(0.0, oldest_at + WINDOW_SECONDS - self._clock()) + 0.25
            if waited + delay > self._max_wait:
                log.info("pacer giving up waiting after %.0fs; sending anyway", waited)
                break
            await self._sleep(delay)
            waited += delay

        self._record(tokens)
        return waited

    def _record(self, tokens: int) -> None:
        self._spent.append((self._clock(), max(0, tokens)))
