from app.providers.llm.health import BreakerState, CircuitBreaker, FailureKind


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_breaker_opens_after_threshold_and_recovers():
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=2, base_cooldown=10, max_cooldown=100, hard_cooldown=1000, clock=clock)
    assert breaker.is_available()
    breaker.record_failure(FailureKind.TRANSIENT, "boom")
    assert breaker.state is BreakerState.CLOSED
    breaker.record_failure(FailureKind.TRANSIENT, "boom")
    assert breaker.state is BreakerState.OPEN
    assert not breaker.is_available()

    clock.now += 11
    assert breaker.state is BreakerState.HALF_OPEN
    assert breaker.is_available()  # one probe allowed
    assert not breaker.is_available()  # second caller must wait
    breaker.record_success(0.5)
    assert breaker.state is BreakerState.CLOSED
    assert breaker.stats.consecutive_failures == 0


def test_exponential_cooldown_and_hard_failures():
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=1, base_cooldown=10, max_cooldown=25, hard_cooldown=500, clock=clock)
    breaker.record_failure(FailureKind.TRANSIENT, "1")
    assert breaker.retry_in_seconds() == 10
    clock.now += 10
    breaker.is_available()
    breaker.record_failure(FailureKind.TRANSIENT, "2")
    assert breaker.retry_in_seconds() == 20
    clock.now += 20
    breaker.is_available()
    breaker.record_failure(FailureKind.TRANSIENT, "3")
    assert breaker.retry_in_seconds() == 25  # capped

    breaker.record_failure(FailureKind.HARD, "bad key")
    assert breaker.retry_in_seconds() == 500
