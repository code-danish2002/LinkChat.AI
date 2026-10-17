"""Rate limiting, which is what stops one caller draining the day's budget."""

from __future__ import annotations

from app.api.rate_limit import WINDOW_SECONDS, SlidingWindow


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_allows_up_to_the_limit_then_refuses():
    clock = FakeClock()
    window = SlidingWindow(3, clock=clock)
    assert [window.check("a")[0] for _ in range(3)] == [True, True, True]

    allowed, remaining, retry_after = window.check("a")
    assert not allowed and remaining == 0
    assert 0 < retry_after <= WINDOW_SECONDS


def test_remaining_counts_down():
    window = SlidingWindow(3, clock=FakeClock())
    assert [window.check("a")[1] for _ in range(3)] == [2, 1, 0]


def test_callers_are_counted_separately():
    window = SlidingWindow(2, clock=FakeClock())
    assert window.check("a")[0] and window.check("a")[0]
    assert not window.check("a")[0]
    assert window.check("b")[0], "one caller must not spend another's quota"


def test_the_window_slides_rather_than_resetting():
    clock = FakeClock()
    window = SlidingWindow(2, clock=clock)
    window.check("a")
    clock.now += 30
    window.check("a")
    assert not window.check("a")[0]

    clock.now += 31  # the first hit leaves the window, the second has not
    assert window.check("a")[0]
    assert not window.check("a")[0]


def test_retry_after_points_at_the_oldest_hit():
    clock = FakeClock()
    window = SlidingWindow(1, clock=clock)
    window.check("a")
    clock.now += 20
    _, _, retry_after = window.check("a")
    assert 39 <= retry_after <= 41, "60s window, 20s elapsed"


def test_a_zero_limit_disables_the_bucket():
    window = SlidingWindow(0, clock=FakeClock())
    assert all(window.check("a")[0] for _ in range(100))


def test_quiet_callers_are_forgotten_so_memory_stays_bounded():
    clock = FakeClock()
    window = SlidingWindow(5, clock=clock)
    for i in range(50):
        window.check(f"caller-{i}")
    assert len(window._hits) == 50

    clock.now += WINDOW_SECONDS * 6
    window.forget()
    assert window._hits == {}

    # an active caller survives a sweep
    window.check("busy")
    window.forget()
    assert "busy" in window._hits
