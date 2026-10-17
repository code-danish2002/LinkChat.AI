import pytest

from app.core.exceptions import NoModelAvailableError
from app.domain.models import ChatMessage
from app.providers.llm.health import BreakerRegistry, FailureKind
from app.providers.llm.openai_compatible import ProviderError
from app.providers.llm.registry import ModelRegistry, ModelSpec, ProviderSpec
from app.providers.llm.router import ModelRouter


class FakeClient:
    """Scripted behaviour per model id: 'fail', 'empty', 'midfail' or an answer string."""

    def __init__(self, script: dict[str, str]):
        self.script = script
        self.calls: list[str] = []

    async def stream_chat(self, provider, model, messages, *, temperature, max_tokens):
        self.calls.append(model)
        behaviour = self.script[model]
        if behaviour == "fail":
            raise ProviderError("HTTP 500", FailureKind.TRANSIENT, 500)
        if behaviour == "auth":
            raise ProviderError("HTTP 401", FailureKind.HARD, 401)
        if behaviour == "too_large":
            raise ProviderError("HTTP 413 request too large", FailureKind.TOO_LARGE, 413)
        if behaviour == "empty":
            return
        if behaviour == "midfail":
            yield "partial "
            raise ProviderError("connection reset", FailureKind.TRANSIENT)
        for word in behaviour.split(" "):
            yield word + " "


def make_router(script: dict[str, str], monkeypatch) -> tuple[ModelRouter, FakeClient]:
    monkeypatch.setenv("TEST_KEY", "x")
    providers = {"p": ProviderSpec(name="p", base_url="http://p", api_key_env="TEST_KEY")}
    models = [ModelSpec(provider="p", model=m, display_name=m, priority=i) for i, m in enumerate(script)]
    registry = ModelRegistry(providers, models)
    client = FakeClient(script)
    router = ModelRouter(
        registry,
        BreakerRegistry(failure_threshold=1, base_cooldown=60),
        client,
        temperature=0,
        max_tokens=10,
    )
    return router, client


async def collect(router):
    return [(e.type, e.data) async for e in router.stream([ChatMessage("user", "hi")])]


async def test_failover_skips_broken_models(monkeypatch):
    router, client = make_router({"a": "fail", "b": "empty", "c": "hello world"}, monkeypatch)
    events = await collect(router)
    assert client.calls == ["a", "b", "c"]
    assert events[0] == ("model", "p/c")
    assert "".join(d for t, d in events if t == "token").strip() == "hello world"
    assert events[-1] == ("done", "p/c")

    # a is circuit-open now, so the next call skips it and lands on c again
    await collect(router)
    assert client.calls[-1] == "c" and client.calls.count("a") == 1

    statuses = {s.model: s for s in router.snapshot()}
    # a failed outright and b returned nothing; both cost a round trip, so both
    # step aside. Only b's cooldown is the short non-escalating kind.
    assert statuses["a"].state == "open"
    assert statuses["b"].state == "open"
    assert statuses["c"].state == "closed"
    assert statuses["c"].successes == 2


async def test_all_models_failing_raises(monkeypatch):
    router, _ = make_router({"a": "fail", "b": "auth"}, monkeypatch)
    with pytest.raises(NoModelAvailableError) as info:
        await collect(router)
    assert len(info.value.details["attempts"]) == 2


async def test_mid_stream_failure_is_reported_not_spliced(monkeypatch):
    router, client = make_router({"a": "midfail", "b": "backup"}, monkeypatch)
    events = await collect(router)
    assert client.calls == ["a"]
    assert events[0] == ("model", "p/a")
    assert events[-1][0] == "error"


def test_inactive_provider_models_are_not_candidates(monkeypatch):
    monkeypatch.delenv("MISSING_KEY", raising=False)
    providers = {"p": ProviderSpec(name="p", base_url="http://p", api_key_env="MISSING_KEY")}
    registry = ModelRegistry(providers, [ModelSpec(provider="p", model="m", display_name="m")])
    assert registry.active_models() == []


async def test_no_configured_providers(monkeypatch):
    monkeypatch.delenv("MISSING_KEY", raising=False)
    providers = {"p": ProviderSpec(name="p", base_url="http://p", api_key_env="MISSING_KEY")}
    registry = ModelRegistry(providers, [ModelSpec(provider="p", model="m", display_name="m")])
    router = ModelRouter(registry, BreakerRegistry(), FakeClient({}), temperature=0, max_tokens=1)
    with pytest.raises(NoModelAvailableError):
        await collect(router)


async def test_spread_rotates_the_starting_model(monkeypatch):
    """Bulk work should use the whole pool, not saturate the first model."""
    router, client = make_router({"a": "one", "b": "two", "c": "three"}, monkeypatch)
    for _ in range(4):
        _ = [event async for event in router.stream([ChatMessage("user", "hi")], spread=True)]
    assert client.calls == ["a", "b", "c", "a"]


async def test_without_spread_the_best_model_always_goes_first(monkeypatch):
    router, client = make_router({"a": "one", "b": "two", "c": "three"}, monkeypatch)
    for _ in range(3):
        await collect(router)
    assert client.calls == ["a", "a", "a"]


async def test_spread_still_fails_over(monkeypatch):
    router, client = make_router({"a": "fine", "b": "fail", "c": "fine"}, monkeypatch)
    # Second call starts at b, which fails, so it must fall through to c.
    _ = [event async for event in router.stream([ChatMessage("user", "hi")], spread=True)]
    events = [(event.type, event.data) async for event in router.stream([ChatMessage("user", "hi")], spread=True)]
    assert client.calls == ["a", "b", "c"]
    assert events[-1] == ("done", "p/c")


async def test_oversized_request_does_not_sideline_the_model(monkeypatch):
    """A 413 is about the payload, so the model stays in rotation."""
    router, _ = make_router({"a": "too_large", "b": "backup"}, monkeypatch)
    events = await collect(router)
    assert events[-1] == ("done", "p/b")
    statuses = {s.model: s for s in router.snapshot()}
    assert statuses["a"].state == "closed", "model must remain available for smaller requests"


async def test_bulk_work_skips_slow_models(monkeypatch):
    """A model marked bulk: false answers normally but sits out condensing."""
    monkeypatch.setenv("TEST_KEY", "x")
    providers = {"p": ProviderSpec(name="p", base_url="http://p", api_key_env="TEST_KEY")}
    models = [
        ModelSpec(provider="p", model="slow", display_name="Slow", priority=1, bulk=False),
        ModelSpec(provider="p", model="fast", display_name="Fast", priority=2),
        ModelSpec(provider="p", model="brisk", display_name="Brisk", priority=3),
    ]
    client = FakeClient({"slow": "a", "fast": "b", "brisk": "c"})
    router = ModelRouter(
        ModelRegistry(providers, models), BreakerRegistry(), client, temperature=0, max_tokens=10
    )

    # Interactive answers keep priority order, so the slow flagship goes first.
    await collect(router)
    assert client.calls == ["slow"]

    # Bulk work rotates across the fast models only.
    for _ in range(3):
        _ = [event async for event in router.stream([ChatMessage("user", "hi")], spread=True)]
    assert client.calls[1:] == ["fast", "brisk", "fast"]


async def test_bulk_falls_back_to_slow_models_when_nothing_else_is_left(monkeypatch):
    monkeypatch.setenv("TEST_KEY", "x")
    providers = {"p": ProviderSpec(name="p", base_url="http://p", api_key_env="TEST_KEY")}
    models = [
        ModelSpec(provider="p", model="fast", display_name="Fast", priority=1),
        ModelSpec(provider="p", model="slow", display_name="Slow", priority=2, bulk=False),
    ]
    client = FakeClient({"fast": "fail", "slow": "answer"})
    router = ModelRouter(
        ModelRegistry(providers, models),
        BreakerRegistry(failure_threshold=1, base_cooldown=60),
        client,
        temperature=0,
        max_tokens=10,
    )
    events = [
        (event.type, event.data)
        async for event in router.stream([ChatMessage("user", "hi")], spread=True)
    ]
    assert client.calls == ["fast", "slow"]
    assert events[-1] == ("done", "p/slow")


async def test_conversations_are_spread_across_the_pool(monkeypatch):
    """Different conversations start on different models, so one model's
    rate-limit budget is not everyone's bottleneck."""
    monkeypatch.setenv("TEST_KEY", "x")
    providers = {"p": ProviderSpec(name="p", base_url="http://p", api_key_env="TEST_KEY")}
    names = [f"m{i}" for i in range(6)]
    models = [
        ModelSpec(provider="p", model=name, display_name=name, priority=i)
        for i, name in enumerate(names)
    ]
    client = FakeClient({name: "answer" for name in names})
    router = ModelRouter(
        ModelRegistry(providers, models), BreakerRegistry(), client, temperature=0, max_tokens=10
    )

    for conversation in [f"cnv_{i}" for i in range(40)]:
        _ = [
            event
            async for event in router.stream([ChatMessage("user", "hi")], affinity=conversation)
        ]
    used = set(client.calls)
    assert len(used) >= 4, f"load concentrated on too few models: {used}"


async def test_one_conversation_keeps_the_same_model(monkeypatch):
    """Answers in a thread should not switch voice between turns."""
    monkeypatch.setenv("TEST_KEY", "x")
    providers = {"p": ProviderSpec(name="p", base_url="http://p", api_key_env="TEST_KEY")}
    names = [f"m{i}" for i in range(6)]
    models = [
        ModelSpec(provider="p", model=name, display_name=name, priority=i)
        for i, name in enumerate(names)
    ]
    client = FakeClient({name: "answer" for name in names})
    router = ModelRouter(
        ModelRegistry(providers, models), BreakerRegistry(), client, temperature=0, max_tokens=10
    )

    for _ in range(5):
        _ = [
            event async for event in router.stream([ChatMessage("user", "hi")], affinity="cnv_same")
        ]
    assert len(set(client.calls)) == 1, "a single conversation must stay on one model"


async def test_affinity_is_stable_across_processes(monkeypatch):
    """The offset must not depend on Python's per-process hash seed."""
    from app.providers.llm.router import _affinity_offset

    assert _affinity_offset("cnv_abc", 7) == _affinity_offset("cnv_abc", 7)
    offsets = {_affinity_offset(f"cnv_{i}", 7) for i in range(50)}
    assert len(offsets) >= 5, "keys should land on many different models"


async def test_affinity_still_fails_over(monkeypatch):
    """A busy or broken assigned model must not strand the conversation."""
    monkeypatch.setenv("TEST_KEY", "x")
    providers = {"p": ProviderSpec(name="p", base_url="http://p", api_key_env="TEST_KEY")}
    models = [
        ModelSpec(provider="p", model=name, display_name=name, priority=i)
        for i, name in enumerate(("a", "b", "c"))
    ]
    client = FakeClient({"a": "fail", "b": "fail", "c": "answer"})
    router = ModelRouter(
        ModelRegistry(providers, models),
        BreakerRegistry(failure_threshold=1, base_cooldown=60),
        client,
        temperature=0,
        max_tokens=10,
    )
    # A key chosen so the conversation is assigned the first, failing model.
    from app.providers.llm.router import _affinity_offset

    key = next(f"cnv_{i}" for i in range(100) if _affinity_offset(f"cnv_{i}", 3) == 0)
    events = [
        (event.type, event.data)
        async for event in router.stream([ChatMessage("user", "hi")], affinity=key)
    ]
    assert events[-1] == ("done", "p/c")
    assert client.calls == ["a", "b", "c"], "it must walk past the failing models"
