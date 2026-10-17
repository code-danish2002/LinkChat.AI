"""Failover router over the model pool.

`stream()` walks the candidates in priority order, skipping models whose
circuit breaker is open. If a model fails before producing any content the
next candidate is tried transparently. Once content has started flowing a
failure ends the stream with an error, since silently switching models
mid-answer would splice two different responses together.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Literal

from app.core.exceptions import NoModelAvailableError
from app.domain.models import ChatMessage
from app.providers.llm.health import BreakerRegistry, FailureKind
from app.providers.llm.openai_compatible import OpenAICompatibleClient, ProviderError
from app.providers.llm.registry import ModelRegistry, ModelSpec

log = logging.getLogger(__name__)


def _affinity_offset(key: str, count: int) -> int:
    """A stable index from a key, independent of the process's hash seed."""
    digest = hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % count


@dataclass(frozen=True)
class LLMEvent:
    type: Literal["model", "token", "done", "error"]
    data: str


@dataclass
class ModelStatus:
    id: str
    provider: str
    model: str
    display_name: str
    priority: int
    active: bool
    state: str
    retry_in_seconds: float | None
    successes: int
    failures: int
    last_error: str | None
    average_latency_ms: float | None
    # None when the provider's model listing could not be read.
    available_upstream: bool | None = None


class ModelRouter:
    def __init__(
        self,
        registry: ModelRegistry,
        breakers: BreakerRegistry,
        client: OpenAICompatibleClient,
        *,
        temperature: float,
        max_tokens: int,
    ):
        self._registry = registry
        self._breakers = breakers
        self._client = client
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._upstream: dict[str, bool] = {}
        self._spread_cursor = 0

    def set_upstream_availability(self, availability: dict[str, bool]) -> None:
        """Record which models their provider actually lists (advisory only)."""
        self._upstream = dict(availability)

    # ---- routing ----

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        spread: bool = False,
        affinity: str | None = None,
    ) -> AsyncIterator[LLMEvent]:
        """Stream an answer from the first model that works.

        Providers meter each model separately, so which model a request starts
        on decides whose rate-limit budget it spends. Two ways to spread that
        load, both still falling through the whole pool on failure:

        `affinity` starts at a model derived from a stable key, normally the
        conversation id. Different conversations land on different models, so
        the pool's combined budget is used instead of everyone queueing behind
        the highest-priority model, while one conversation keeps a consistent
        model and therefore a consistent voice.

        `spread` rotates on every call, which suits bulk work where consistency
        does not matter and throughput does.
        """
        candidates = self._registry.active_models()
        if not candidates:
            raise NoModelAvailableError(
                "No model providers are configured. Set at least one API key "
                "(GROQ_API_KEY, CEREBRAS_API_KEY, OPENROUTER_API_KEY, TOGETHER_API_KEY, HF_TOKEN) "
                "or enable Ollama."
            )

        if spread:
            # Bulk work makes many requests, so slow members sit it out unless
            # nothing faster is left.
            eligible = [pair for pair in candidates if pair[0].bulk]
            fallback = [pair for pair in candidates if not pair[0].bulk]
            if eligible:
                if len(eligible) > 1:
                    offset = self._spread_cursor % len(eligible)
                    self._spread_cursor += 1
                    eligible = eligible[offset:] + eligible[:offset]
                candidates = eligible + fallback
        elif affinity and len(candidates) > 1:
            offset = _affinity_offset(affinity, len(candidates))
            candidates = candidates[offset:] + candidates[:offset]

        attempts: list[str] = []
        skipped = 0
        for spec, provider in candidates:
            breaker = self._breakers.get(spec.id)
            if not breaker.is_available():
                skipped += 1
                continue

            started = time.monotonic()
            produced = False
            requested = max_tokens or self._max_tokens
            call_max_tokens = min(requested, spec.max_output_tokens or requested)
            try:
                async for delta in self._client.stream_chat(
                    provider,
                    spec.model,
                    messages,
                    temperature=self._temperature if temperature is None else temperature,
                    max_tokens=call_max_tokens,
                ):
                    if not produced:
                        produced = True
                        yield LLMEvent("model", spec.id)
                    yield LLMEvent("token", delta)
            except ProviderError as exc:
                breaker.record_failure(exc.kind, exc.message)
                attempts.append(f"{spec.id}: {exc.message}")
                log.warning("model %s failed (%s): %s", spec.id, exc.kind, exc.message)
                if produced:
                    yield LLMEvent("error", f"{spec.display_name} stopped mid-answer: {exc.message}")
                    return
                continue

            if not produced:
                # Typically a reasoning model whose output cap went entirely on
                # thinking. Not a health problem, so the breaker stays closed.
                breaker.record_failure(FailureKind.EMPTY, "no content (output cap too small?)")
                attempts.append(f"{spec.id}: empty response")
                log.warning(
                    "model %s streamed no content at max_tokens=%d; trying the next model",
                    spec.id,
                    call_max_tokens,
                )
                continue

            breaker.record_success(time.monotonic() - started)
            yield LLMEvent("done", spec.id)
            return

        # How long until the soonest model is allowed through again. Bulk callers
        # wait this out instead of burning their remaining work on open breakers.
        cooldowns = [self._breakers.get(spec.id).retry_in_seconds() for spec, _ in candidates]
        retry_in = min((value for value in cooldowns if value is not None), default=None)
        raise NoModelAvailableError(
            "Every model in the pool is unavailable right now. "
            + (f"{skipped} skipped by circuit breaker. " if skipped else "")
            + (f"The soonest retries in {retry_in:.0f}s. " if retry_in else "")
            + ("Last errors: " + "; ".join(attempts[-3:]) if attempts else ""),
            details={"attempts": attempts, "skipped": skipped, "retry_in_seconds": retry_in},
        )

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        spread: bool = False,
        affinity: str | None = None,
    ) -> tuple[str, str]:
        """Non-streaming helper: returns (text, model_id)."""
        parts: list[str] = []
        model_id = ""
        async for event in self.stream(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            spread=spread,
            affinity=affinity,
        ):
            if event.type == "token":
                parts.append(event.data)
            elif event.type == "model":
                model_id = event.data
            elif event.type == "error":
                raise NoModelAvailableError(event.data)
        return "".join(parts), model_id

    # ---- observability ----

    def snapshot(self) -> list[ModelStatus]:
        active_ids = {spec.id for spec, _ in self._registry.active_models()}
        statuses: list[ModelStatus] = []
        for spec in self._registry.models:
            breaker = self._breakers.get(spec.id)
            statuses.append(
                self._status(spec, spec.id in active_ids, breaker, self._upstream.get(spec.id))
            )
        return statuses

    @staticmethod
    def _status(spec: ModelSpec, active: bool, breaker, upstream: bool | None = None) -> ModelStatus:
        return ModelStatus(
            id=spec.id,
            provider=spec.provider,
            model=spec.model,
            display_name=spec.display_name,
            priority=spec.priority,
            active=active,
            state=breaker.state.value if active else "inactive",
            retry_in_seconds=breaker.retry_in_seconds() if active else None,
            successes=breaker.stats.successes,
            failures=breaker.stats.failures,
            last_error=breaker.stats.last_error,
            average_latency_ms=breaker.stats.average_latency_ms,
            available_upstream=upstream if active else None,
        )

    def active_count(self) -> int:
        return len(self._registry.active_models())
