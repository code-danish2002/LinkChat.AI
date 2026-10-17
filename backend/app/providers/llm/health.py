"""Per-model circuit breakers.

closed  -> requests flow; consecutive failures are counted
open    -> the model is skipped until the cooldown elapses
half    -> after cooldown one request is allowed through to probe recovery

Transient failures (timeouts, 5xx, rate limits) use an exponential cooldown.
Hard failures (bad key, unknown model) park the model for much longer,
because retrying them is pointless until the operator intervenes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum


class FailureKind(StrEnum):
    TRANSIENT = "transient"
    RATE_LIMITED = "rate_limited"
    #: The request itself was too big for this model. The model is healthy, so
    #: its breaker stays closed and a smaller request may well succeed.
    TOO_LARGE = "too_large"
    #: The model streamed nothing usable. Reasoning models do this when the
    #: output cap is consumed by internal thinking, which is a request-shape
    #: problem rather than a sick model, so the breaker stays closed.
    EMPTY = "empty"
    HARD = "hard"


#: Failures that say something about the request, not the model's health, and so
#: never take a model out of the pool.
_NOT_HEALTH_SIGNALS = frozenset({FailureKind.TOO_LARGE})


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class BreakerStats:
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    opened_count: int = 0
    last_error: str | None = None
    last_failure_at: float | None = None
    last_success_at: float | None = None
    latency_samples: list[float] = field(default_factory=list)

    @property
    def average_latency_ms(self) -> float | None:
        if not self.latency_samples:
            return None
        return round(sum(self.latency_samples) / len(self.latency_samples) * 1000, 1)


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 2,
        base_cooldown: float = 60.0,
        max_cooldown: float = 900.0,
        hard_cooldown: float = 3600.0,
        clock=time.monotonic,
    ):
        self._threshold = max(1, failure_threshold)
        self._base = base_cooldown
        self._max = max_cooldown
        self._hard = hard_cooldown
        self._clock = clock
        self._open_until: float | None = None
        self._probing = False
        self.stats = BreakerStats()

    # ---- state ----

    @property
    def state(self) -> BreakerState:
        if self._open_until is None:
            return BreakerState.CLOSED
        if self._clock() >= self._open_until:
            return BreakerState.HALF_OPEN
        return BreakerState.OPEN

    def is_available(self) -> bool:
        state = self.state
        if state is BreakerState.CLOSED:
            return True
        if state is BreakerState.HALF_OPEN and not self._probing:
            self._probing = True
            return True
        return False

    def retry_in_seconds(self) -> float | None:
        if self._open_until is None:
            return None
        return max(0.0, round(self._open_until - self._clock(), 1))

    # ---- feedback ----

    def record_success(self, latency_seconds: float | None = None) -> None:
        s = self.stats
        s.successes += 1
        s.consecutive_failures = 0
        s.last_success_at = time.time()
        if latency_seconds is not None:
            s.latency_samples = (s.latency_samples + [latency_seconds])[-20:]
        self._open_until = None
        self._probing = False

    def record_failure(self, kind: FailureKind, error: str) -> None:
        s = self.stats
        s.failures += 1
        s.consecutive_failures += 1
        s.last_error = error[:300]
        s.last_failure_at = time.time()
        self._probing = False

        if kind in _NOT_HEALTH_SIGNALS:
            # The model is fine; the request shape was wrong. Keep it available,
            # because a smaller request or a larger output cap will succeed.
            s.consecutive_failures = 0
            return
        if kind is FailureKind.HARD:
            self._open(self._hard)
        elif kind is FailureKind.EMPTY:
            # Producing nothing still costs a full round trip, so a model that
            # keeps doing it has to step aside. It is not broken though, so the
            # cooldown is short and never escalates.
            if s.consecutive_failures >= self._threshold:
                self._open(self._base, escalate=False)
        elif kind is FailureKind.RATE_LIMITED or s.consecutive_failures >= self._threshold:
            cooldown = min(self._base * (2**s.opened_count), self._max)
            self._open(cooldown)

    def _open(self, cooldown: float, *, escalate: bool = True) -> None:
        if escalate:
            self.stats.opened_count += 1
        self._open_until = self._clock() + cooldown


class BreakerRegistry:
    """Lazily creates one breaker per model id with shared settings."""

    def __init__(self, **breaker_kwargs):
        self._kwargs = breaker_kwargs
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(self, model_id: str) -> CircuitBreaker:
        breaker = self._breakers.get(model_id)
        if breaker is None:
            breaker = self._breakers[model_id] = CircuitBreaker(**self._kwargs)
        return breaker
