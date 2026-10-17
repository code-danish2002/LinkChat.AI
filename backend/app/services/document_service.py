"""Runs whole-document modes such as summarise, outline and extract.

Retrieval answers questions, but a summary needs the entire source. Hosted
free tiers cap tokens per minute far below their context windows, so this
service plans around that budget rather than the context window:

- a source that fits one paced request is sent in one request;
- anything longer is condensed window by window, and if the combined notes
  are still too big they are condensed again, as many rounds as it takes;
- windows leave room for the prompt itself, and the pacer spreads requests
  so the minute budget is not blown;
- condensing rotates across the pool, because providers meter each model
  separately;
- when the pool is cooling down after a rate limit, work waits for the
  cooldown rather than failing every remaining pass instantly.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence

from app.core.exceptions import NoModelAvailableError
from app.domain.models import ChatMessage, Chunk, SourceKind
from app.providers.llm.router import ModelRouter
from app.services.events import StreamEvent
from app.services.modes import ModeSpec
from app.services.pacing import TokenPacer
from app.services.prompts import (
    build_condense_messages,
    build_document_messages,
    estimate_tokens,
    render_chunks,
)

log = logging.getLogger(__name__)

#: Output tokens to reserve for pacing. Reserving the full cap would waste most
#: of a free tier's minute, but reasoning models really do spend most of a large
#: cap, so the reservation has to stay close to reality.
_EXPECTED_OUTPUT_TOKENS = 1200


class DocumentService:
    def __init__(
        self,
        router: ModelRouter,
        *,
        max_input_tokens: int = 2500,
        prompt_overhead_tokens: int = 700,
        map_max_tokens: int = 2000,
        reduce_max_tokens: int = 1600,
        max_windows: int = 24,
        tokens_per_minute: int = 8000,
        max_wait_seconds: float = 90.0,
        pass_attempts: int = 3,
        max_rounds: int = 3,
    ):
        """`tokens_per_minute` is one model's budget, not the whole pool's."""
        self._router = router
        self._prompt_overhead = max(0, prompt_overhead_tokens)
        # Content budget per request, after leaving room for the prompt scaffolding.
        self._content_budget = max(400, max_input_tokens - self._prompt_overhead)
        self._map_max_tokens = map_max_tokens
        self._reduce_max_tokens = reduce_max_tokens
        self._max_windows = max_windows
        self._tokens_per_minute = tokens_per_minute
        self._max_wait = max_wait_seconds
        self._pass_attempts = max(1, pass_attempts)
        self._max_rounds = max(1, max_rounds)

    async def stream(
        self,
        *,
        spec: ModeSpec,
        title: str,
        kind: SourceKind,
        chunks: list[Chunk],
        user_input: str | None,
        source_titles: dict[str, str] | None = None,
        source_count: int = 1,
        affinity: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        if not chunks:
            yield StreamEvent("error", "This source has no indexed text to work from.")
            return

        # Providers meter each model separately and condensing rotates across the
        # pool, so usable capacity scales with the number of active models.
        budget = self._tokens_per_minute * max(1, self._router.active_count())
        pacer = TokenPacer(budget, max_wait_seconds=self._max_wait)
        windows = self._plan(chunks, source_titles)

        if len(windows) == 1:
            body = windows[0]
        else:
            yield StreamEvent(
                "progress",
                f"Reading {len(chunks)} sections in {len(windows)} passes, because the source is "
                "longer than one request allows.",
            )
            blocks = windows
            body = ""
            for round_number in range(1, self._max_rounds + 1):
                notes: list[str] = []
                for number, window in enumerate(blocks, start=1):
                    label = f"pass {number}" if round_number == 1 else f"round {round_number}, part {number}"
                    messages = build_condense_messages(title=title, kind=kind, window=window)
                    async for event in self._paced(pacer, messages, self._map_max_tokens, label):
                        yield event

                    condensed: str | None = None
                    for attempt in range(1, self._pass_attempts + 1):
                        condensed, wait_for = await self._try_condense(
                            messages, number, len(blocks), attempt
                        )
                        if condensed or attempt == self._pass_attempts:
                            break
                        if wait_for:
                            yield StreamEvent(
                                "progress",
                                f"The model pool is rate limited. Waiting {wait_for:.0f}s before "
                                f"retrying {label}.",
                            )
                            await asyncio.sleep(wait_for)

                    if condensed:
                        notes.append(condensed)
                        yield StreamEvent(
                            "progress", f"Condensed {label} of {len(blocks)}."
                        )
                    else:
                        yield StreamEvent(
                            "progress", f"{label.capitalize()} could not be read; continuing."
                        )

                if not notes:
                    yield StreamEvent(
                        "error",
                        "Every pass over this source failed, which usually means the provider's "
                        "per-minute token budget is exhausted. Wait a minute and try again, or add "
                        "a second provider key so the pool has more capacity.",
                    )
                    return
                if len(notes) < len(blocks):
                    yield StreamEvent(
                        "progress",
                        f"{len(blocks) - len(notes)} of {len(blocks)} parts failed; "
                        "answering from the rest.",
                    )

                body = "\n\n".join(notes)
                fits = estimate_tokens(body) <= self._content_budget
                if fits or len(notes) == 1:
                    break
                if round_number == self._max_rounds:
                    # Out of rounds; the body is capped below instead.
                    yield StreamEvent(
                        "progress",
                        "The notes are still long after the final condensing round, so the answer "
                        "uses as much of them as one request allows.",
                    )
                    break

                # The notes together still exceed one request, so condense again.
                blocks = self._regroup(notes)
                yield StreamEvent(
                    "progress",
                    f"The notes are still too long for one request, so condensing them again in "
                    f"{len(blocks)} parts.",
                )

            # Last resort, so the final request can never be rejected as too large.
            body = self._truncate(body)

        messages = build_document_messages(
            title=title,
            kind=kind,
            instruction=spec.instruction,
            document=body,
            user_input=user_input,
            source_count=source_count,
        )
        max_tokens = spec.max_tokens or self._reduce_max_tokens
        async for event in self._paced(pacer, messages, max_tokens, "the final answer"):
            yield event

        for attempt in range(1, self._pass_attempts + 1):
            try:
                async for event in self._router.stream(
                    messages,
                    temperature=spec.temperature,
                    max_tokens=max_tokens,
                    affinity=affinity,
                ):
                    yield StreamEvent(event.type, event.data)
                return
            except NoModelAvailableError as exc:
                wait_for = self._cooldown(exc)
                if attempt == self._pass_attempts or not wait_for:
                    yield StreamEvent("error", exc.message)
                    return
                yield StreamEvent(
                    "progress",
                    f"The model pool is rate limited. Waiting {wait_for:.0f}s before retrying "
                    "the final answer.",
                )
                await asyncio.sleep(wait_for)

    # ---- internals ----

    async def _paced(
        self, pacer: TokenPacer, messages: Sequence[ChatMessage], max_tokens: int, label: str
    ) -> AsyncIterator[StreamEvent]:
        waited = await pacer.reserve(self._cost(messages, max_tokens))
        if waited > 1:
            yield StreamEvent(
                "progress",
                f"Pausing {waited:.0f}s to stay inside the provider's per-minute token budget, "
                f"then continuing with {label}.",
            )

    @staticmethod
    def _cost(messages: Sequence[ChatMessage], max_tokens: int) -> int:
        prompt = sum(estimate_tokens(message.content) for message in messages)
        return prompt + min(max_tokens, _EXPECTED_OUTPUT_TOKENS)

    def _cooldown(self, error: NoModelAvailableError) -> float | None:
        """Seconds to wait before the pool is worth trying again."""
        value = error.details.get("retry_in_seconds")
        if not isinstance(value, (int, float)) or value <= 0:
            return None
        return min(float(value) + 1.0, self._max_wait)

    async def _try_condense(
        self, messages: Sequence[ChatMessage], number: int, total: int, attempt: int
    ) -> tuple[str | None, float | None]:
        """One condensing attempt. Returns the notes, or how long to wait."""
        try:
            text, _ = await self._router.complete(
                messages, max_tokens=self._map_max_tokens, spread=True
            )
        except NoModelAvailableError as exc:
            log.warning("condense pass %d/%d attempt %d failed: %s", number, total, attempt, exc.message)
            return None, self._cooldown(exc)
        if text.strip():
            return text.strip(), None
        log.warning("condense pass %d/%d attempt %d returned nothing", number, total, attempt)
        return None, None

    def _regroup(self, texts: list[str]) -> list[str]:
        """Pack note blocks into windows that each fit the content budget."""
        groups: list[str] = []
        current: list[str] = []
        for text in texts:
            candidate = [*current, text]
            if current and estimate_tokens("\n\n".join(candidate)) > self._content_budget:
                groups.append("\n\n".join(current))
                current = [text]
            else:
                current = candidate
        if current:
            groups.append("\n\n".join(current))
        return groups

    def _truncate(self, body: str) -> str:
        """Hard cap, in characters, so the final request is never rejected."""
        limit = self._content_budget * 4
        if len(body) <= limit:
            return body
        log.info("condensed notes still too long (%d chars); truncating to %d", len(body), limit)
        return body[:limit].rsplit("\n\n", 1)[0]

    def _plan(self, chunks: list[Chunk], source_titles: dict[str, str] | None) -> list[str]:
        """Group chunks into rendered windows that each fit the content budget."""

        def rendered(group: list[Chunk]) -> str:
            return render_chunks(group, source_titles=source_titles)

        windows: list[str] = []
        current: list[Chunk] = []
        for chunk in chunks:
            candidate = [*current, chunk]
            if current and estimate_tokens(rendered(candidate)) > self._content_budget:
                windows.append(rendered(current))
                current = [chunk]
            else:
                current = candidate
        if current:
            windows.append(rendered(current))

        if len(windows) > self._max_windows:
            log.info("document needs %d windows; truncating to %d", len(windows), self._max_windows)
            windows = windows[: self._max_windows]
        return windows
