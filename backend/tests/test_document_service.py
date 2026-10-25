"""Whole-document modes: single pass when it fits, windowed condensing when it does not."""

from __future__ import annotations

import asyncio

import pytest

from app.core.ids import new_id
from app.domain.models import ChatMode, Chunk, SourceKind
from app.providers.llm.health import BreakerRegistry, FailureKind
from app.providers.llm.openai_compatible import ProviderError
from app.providers.llm.registry import ModelRegistry, ModelSpec, ProviderSpec
from app.providers.llm.router import ModelRouter
from app.services.document_service import DocumentService
from app.services.modes import get_mode


class ScriptedClient:
    """Answers every call, failing the first `fail_first` condense CALLS.

    A pass gets `pass_attempts` tries, so failing that many calls in a row
    exhausts one whole pass.
    """

    def __init__(self, fail_first: int = 0):
        self.calls: list[str] = []
        self._fail_first = fail_first

    async def stream_chat(self, provider, model, messages, *, temperature, max_tokens):
        system = messages[0].content
        condensing = "Condense this part" in system
        self.calls.append("condense" if condensing else "final")
        if condensing and self._fail_first > 0:
            self._fail_first -= 1
            raise ProviderError("rate limited", FailureKind.TRANSIENT, 429)
        for word in ("notes" if condensing else "Final answer [1] [2].").split(" "):
            yield word + " "


def make_router(client) -> ModelRouter:
    registry = ModelRegistry(
        {"p": ProviderSpec(name="p", base_url="http://p", api_key_env=None)},
        [ModelSpec(provider="p", model="m", display_name="M")],
    )
    breakers = BreakerRegistry(failure_threshold=99, base_cooldown=0.01)
    return ModelRouter(registry, breakers, client, temperature=0.0, max_tokens=256)


def make_chunks(count: int, size: int = 400) -> list[Chunk]:
    return [
        Chunk(id=new_id("chk"), document_id="doc_1", index=i, text=f"part {i} " + "word " * (size // 5))
        for i in range(count)
    ]


async def collect(service: DocumentService, chunks, mode=ChatMode.SUMMARY):
    return [
        (event.type, event.data)
        async for event in service.stream(
            spec=get_mode(mode),
            title="Doc",
            kind=SourceKind.WEB,
            chunks=chunks,
            user_input=None,
        )
    ]


async def test_short_document_uses_a_single_request():
    client = ScriptedClient()
    service = DocumentService(make_router(client), max_input_tokens=100_000, tokens_per_minute=0)
    events = await collect(service, make_chunks(3))

    assert client.calls == ["final"], "a document that fits needs no condensing pass"
    assert not [e for e in events if e[0] == "progress"]
    assert "Final answer" in "".join(data for kind, data in events if kind == "token")


async def test_long_document_is_condensed_in_windows():
    client = ScriptedClient()
    service = DocumentService(make_router(client), max_input_tokens=1200, map_max_tokens=64, tokens_per_minute=0)
    events = await collect(service, make_chunks(12))

    condense_calls = client.calls.count("condense")
    assert condense_calls > 1
    assert client.calls[-1] == "final"
    progress = [data for kind, data in events if kind == "progress"]
    assert any("passes" in message for message in progress)


async def test_window_count_is_capped():
    client = ScriptedClient()
    service = DocumentService(
        make_router(client), max_input_tokens=1100, max_windows=3, tokens_per_minute=0, max_rounds=1
    )
    await collect(service, make_chunks(30))
    assert client.calls.count("condense") == 3


async def test_a_pass_is_retried_before_being_given_up_on():
    """One failed call is retried, so the pass still succeeds."""
    client = ScriptedClient(fail_first=1)
    service = DocumentService(make_router(client), max_input_tokens=1200, tokens_per_minute=0)
    events = await collect(service, make_chunks(12))

    assert client.calls[-1] == "final"
    progress = [data for kind, data in events if kind == "progress"]
    assert not any("could not be read" in message for message in progress)
    assert not any("failed" in message for message in progress)


async def test_partial_condense_failure_still_answers():
    # Three failed calls exhaust one pass at the default attempt count.
    client = ScriptedClient(fail_first=3)
    service = DocumentService(make_router(client), max_input_tokens=1200, tokens_per_minute=0)
    events = await collect(service, make_chunks(12))

    assert client.calls[-1] == "final"
    progress = [data for kind, data in events if kind == "progress"]
    assert any("could not be read" in message for message in progress)
    assert any("parts failed; answering from the rest" in message for message in progress)
    assert "Final answer" in "".join(data for kind, data in events if kind == "token")


async def test_every_condense_failing_is_reported_as_an_error():
    client = ScriptedClient(fail_first=99)
    service = DocumentService(make_router(client), max_input_tokens=1200, tokens_per_minute=0)
    events = await collect(service, make_chunks(12))

    assert [kind for kind, _ in events][-1] == "error"
    assert "per-minute token budget" in events[-1][1]
    assert "final" not in client.calls


async def test_empty_source_is_rejected():
    service = DocumentService(make_router(ScriptedClient()), tokens_per_minute=0)
    events = await collect(service, [])
    assert events == [("error", "This source has no indexed text to work from.")]


@pytest.mark.parametrize("mode", [ChatMode.OUTLINE, ChatMode.EXTRACT, ChatMode.BLOG_POST])
async def test_mode_instruction_reaches_the_prompt(mode):
    client = ScriptedClient()
    service = DocumentService(make_router(client), max_input_tokens=100_000, tokens_per_minute=0)
    captured: list[str] = []

    original = client.stream_chat

    async def spy(provider, model, messages, **kwargs):
        captured.append(messages[0].content)
        async for delta in original(provider, model, messages, **kwargs):
            yield delta

    client.stream_chat = spy  # type: ignore[method-assign]
    await collect(service, make_chunks(2), mode)
    assert get_mode(mode).instruction[:40] in captured[0]


class CoolingClient:
    """Rate limits every condense call until `until_call`, then succeeds."""

    def __init__(self, until_call: int):
        self.calls = 0
        self._until = until_call

    async def stream_chat(self, provider, model, messages, *, temperature, max_tokens):
        condensing = "Condense this part" in messages[0].content
        if condensing:
            self.calls += 1
            if self.calls <= self._until:
                raise ProviderError("429 rate limited", FailureKind.RATE_LIMITED, 429)
        for word in ("notes" if condensing else "Final answer [1].").split(" "):
            yield word + " "


async def test_a_cooling_pool_is_waited_out_rather_than_abandoned(monkeypatch):
    """A rate limit opens every breaker, so the run must wait for the cooldown.

    The cooldown is real but tiny, so the breaker genuinely reopens; capping the
    service's wait keeps the test fast.
    """
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def recording_sleep(seconds):
        slept.append(seconds)
        await real_sleep(seconds)

    monkeypatch.setattr("app.services.document_service.asyncio.sleep", recording_sleep)

    client = CoolingClient(until_call=1)
    registry = ModelRegistry(
        {"p": ProviderSpec(name="p", base_url="http://p", api_key_env=None)},
        [ModelSpec(provider="p", model="m", display_name="M")],
    )
    router = ModelRouter(
        registry,
        BreakerRegistry(failure_threshold=1, base_cooldown=0.3),
        client,
        temperature=0.0,
        max_tokens=256,
    )
    service = DocumentService(router, max_input_tokens=1200, tokens_per_minute=0, max_wait_seconds=0.6)
    events = await collect(service, make_chunks(12))

    assert slept, "the service should wait for the breaker cooldown"
    progress = [data for kind, data in events if kind == "progress"]
    assert any("rate limited" in message and "Waiting" in message for message in progress)
    # Having waited, the retry succeeds and the run completes normally.
    assert not any("could not be read" in message for message in progress)
    assert "Final answer" in "".join(data for kind, data in events if kind == "token")


async def test_pacing_waits_are_reported_as_progress():
    client = ScriptedClient()
    registry = ModelRegistry(
        {"p": ProviderSpec(name="p", base_url="http://p", api_key_env=None)},
        [ModelSpec(provider="p", model="m", display_name="M")],
    )
    router = ModelRouter(registry, BreakerRegistry(), client, temperature=0.0, max_tokens=256)
    # A tiny per-minute budget forces the pacer to wait between passes.
    service = DocumentService(router, max_input_tokens=1200, tokens_per_minute=900, max_wait_seconds=0.05)
    events = await collect(service, make_chunks(8))
    assert client.calls[-1] == "final", "capped waiting must not stall the run"
    assert [kind for kind, _ in events].count("token") > 0


class VerboseClient:
    """Condensing returns long notes, so a second reduction round is required."""

    def __init__(self, note_words: int = 400):
        self.rounds: list[int] = []
        self.calls: list[str] = []
        self._note = "detail " * note_words

    async def stream_chat(self, provider, model, messages, *, temperature, max_tokens):
        condensing = "Condense this part" in messages[0].content
        self.calls.append("condense" if condensing else "final")
        payload = self._note if condensing else "Final answer [1]."
        for word in payload.split(" "):
            yield word + " "


async def test_oversized_notes_are_condensed_again():
    """12 long note blocks cannot fit one request, so a second round must run."""
    client = VerboseClient()
    service = DocumentService(make_router(client), max_input_tokens=1200, tokens_per_minute=0, max_rounds=3)
    events = await collect(service, make_chunks(14))

    progress = [data for kind, data in events if kind == "progress"]
    assert any("condensing them again" in message for message in progress)
    assert any("round 2" in message for message in progress)
    assert client.calls[-1] == "final"
    assert "Final answer" in "".join(data for kind, data in events if kind == "token")


async def test_final_request_never_exceeds_the_budget():
    """Even if rounds run out, the body is capped so the provider cannot reject it."""
    client = VerboseClient(note_words=800)
    captured: list[int] = []
    service = DocumentService(make_router(client), max_input_tokens=1200, tokens_per_minute=0, max_rounds=1)

    original = client.stream_chat

    async def spy(provider, model, messages, **kwargs):
        if "Condense this part" not in messages[0].content:
            captured.append(sum(len(m.content) for m in messages))
        async for delta in original(provider, model, messages, **kwargs):
            yield delta

    client.stream_chat = spy  # type: ignore[method-assign]
    await collect(service, make_chunks(14))

    assert captured, "a final request should have been made"
    # content budget is 1200 - 700 = 500 tokens, so roughly 2000 characters plus the prompt
    assert captured[0] < 500 * 4 + 2000


async def test_running_out_of_rounds_says_so_instead_of_promising_another():
    client = VerboseClient(note_words=800)
    service = DocumentService(
        make_router(client), max_input_tokens=1200, tokens_per_minute=0, max_rounds=2
    )
    events = await collect(service, make_chunks(16))

    progress = [data for kind, data in events if kind == "progress"]
    assert any("as much of them as one request allows" in message for message in progress)
    # The last word on condensing must not advertise a further round.
    condensing = [m for m in progress if "condensing them again" in m]
    assert len(condensing) == 1, "only the round that actually runs should be announced"
    assert "Final answer" in "".join(data for kind, data in events if kind == "token")
