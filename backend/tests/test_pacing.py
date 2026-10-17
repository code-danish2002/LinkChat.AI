"""The token pacer, driven by a fake clock so nothing actually sleeps."""

from __future__ import annotations

from app.services.pacing import TokenPacer


class FakeTime:
    """A clock the test advances, plus a sleep that advances it."""

    def __init__(self):
        self.now = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


async def test_requests_inside_the_budget_never_wait():
    time = FakeTime()
    pacer = TokenPacer(8000, clock=time.clock, sleep=time.sleep)
    assert await pacer.reserve(3000) == 0.0
    assert await pacer.reserve(4000) == 0.0
    assert time.slept == []
    assert pacer.used == 7000


async def test_exceeding_the_budget_waits_for_the_window_to_roll():
    time = FakeTime()
    pacer = TokenPacer(8000, clock=time.clock, sleep=time.sleep)
    await pacer.reserve(5000)
    waited = await pacer.reserve(5000)

    assert waited > 60, "must wait for the first reservation to leave the minute"
    assert time.slept, "the pacer should have slept"
    # The expired reservation is dropped, so only the new one counts.
    assert pacer.used == 5000


async def test_spend_leaves_the_window_after_a_minute():
    time = FakeTime()
    pacer = TokenPacer(8000, clock=time.clock, sleep=time.sleep)
    await pacer.reserve(8000)
    time.now += 61
    assert pacer.used == 0
    assert await pacer.reserve(8000) == 0.0


async def test_waiting_is_capped_so_a_request_is_never_stuck():
    time = FakeTime()
    pacer = TokenPacer(1000, max_wait_seconds=10, clock=time.clock, sleep=time.sleep)
    await pacer.reserve(1000)
    waited = await pacer.reserve(1000)
    assert waited <= 10


async def test_a_single_oversized_request_is_sent_rather_than_blocked():
    time = FakeTime()
    pacer = TokenPacer(1000, clock=time.clock, sleep=time.sleep)
    # Nothing spent yet, so an over-budget request goes straight through.
    assert await pacer.reserve(5000) == 0.0


async def test_pacing_can_be_disabled():
    time = FakeTime()
    pacer = TokenPacer(0, clock=time.clock, sleep=time.sleep)
    assert not pacer.enabled
    assert await pacer.reserve(100_000) == 0.0
    assert time.slept == []
