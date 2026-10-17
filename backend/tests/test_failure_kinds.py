"""An oversized request must not take a healthy model out of the pool."""

from __future__ import annotations

import pytest

from app.providers.llm.health import BreakerState, CircuitBreaker, FailureKind
from app.providers.llm.openai_compatible import classify_status

# The first two bodies are the real wording Groq returns; both are per-minute
# allowances even though one calls itself "Request too large".
GROQ_TPM = (
    "Rate limit reached for model `openai/gpt-oss-120b` in organization `org_x` service tier "
    "`on_demand` on tokens per minute (TPM): Limit 8000, Used 6798, Requested 7224."
)
GROQ_ITPM = (
    "Request too large for model `qwen/qwen3.8-27b` in organization `org_x` service tier "
    "`on_demand` on input tokens per minute (ITPM): Limit 7000, Requested 7351, please reduce."
)


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (429, GROQ_TPM, FailureKind.RATE_LIMITED),
        (413, GROQ_ITPM, FailureKind.RATE_LIMITED),
        (429, GROQ_ITPM, FailureKind.RATE_LIMITED),
        (413, "Request body too large: 12 MB exceeds the 4 MB limit", FailureKind.TOO_LARGE),
        (413, "Please reduce the length of the messages", FailureKind.TOO_LARGE),
        (429, "Too many requests, slow down", FailureKind.RATE_LIMITED),
        (429, "Prompt exceeds the maximum context length", FailureKind.TOO_LARGE),
        (401, "invalid key", FailureKind.HARD),
        (402, "This model is not in the Free plan. Pro is $20/month", FailureKind.HARD),
        (403, "forbidden", FailureKind.HARD),
        (403, "This authentication method does not have sufficient permissions", FailureKind.HARD),
        (404, "no such model", FailureKind.HARD),
        (400, "The model `gone-2b` does not exist", FailureKind.HARD),
        (500, "upstream boom", FailureKind.TRANSIENT),
        (503, "overloaded", FailureKind.TRANSIENT),
    ],
)
def test_status_classification(status, body, expected):
    assert classify_status(status, body) is expected


def test_too_large_keeps_the_breaker_closed():
    breaker = CircuitBreaker(failure_threshold=1, base_cooldown=60)
    for _ in range(5):
        breaker.record_failure(FailureKind.TOO_LARGE, "Request too large")

    assert breaker.state is BreakerState.CLOSED
    assert breaker.is_available(), "a smaller request should still be allowed through"
    assert breaker.stats.failures == 5
    assert breaker.stats.consecutive_failures == 0


def test_a_per_minute_cap_opens_the_breaker_even_when_phrased_as_too_large():
    """Groq's ITPM error says "Request too large" but clears on its own."""
    breaker = CircuitBreaker(failure_threshold=5, base_cooldown=60)
    breaker.record_failure(classify_status(413, GROQ_ITPM), GROQ_ITPM)
    assert breaker.state is BreakerState.OPEN
    assert breaker.retry_in_seconds() == 60


def test_rate_limit_opens_the_breaker_immediately():
    breaker = CircuitBreaker(failure_threshold=5, base_cooldown=60)
    breaker.record_failure(FailureKind.RATE_LIMITED, "429")
    assert breaker.state is BreakerState.OPEN


def test_too_large_does_not_mask_a_real_failure():
    breaker = CircuitBreaker(failure_threshold=2, base_cooldown=60)
    breaker.record_failure(FailureKind.TRANSIENT, "boom")
    breaker.record_failure(FailureKind.TOO_LARGE, "too big")
    assert breaker.state is BreakerState.CLOSED
    breaker.record_failure(FailureKind.TRANSIENT, "boom")
    breaker.record_failure(FailureKind.TRANSIENT, "boom")
    assert breaker.state is BreakerState.OPEN


def test_repeated_empty_responses_step_a_model_aside_briefly():
    """Returning nothing still costs a round trip, so it cannot be free forever."""
    breaker = CircuitBreaker(failure_threshold=2, base_cooldown=60, max_cooldown=900)
    breaker.record_failure(FailureKind.EMPTY, "no content")
    assert breaker.state is BreakerState.CLOSED, "one empty response is forgiven"

    breaker.record_failure(FailureKind.EMPTY, "no content")
    assert breaker.state is BreakerState.OPEN
    assert breaker.retry_in_seconds() == 60


def test_empty_cooldown_never_escalates():
    """Unlike a sick model, an empty response must not back off exponentially."""
    clock_now = [1000.0]
    breaker = CircuitBreaker(
        failure_threshold=1,
        base_cooldown=60,
        max_cooldown=900,
        clock=lambda: clock_now[0],
    )
    for _ in range(4):
        breaker.record_failure(FailureKind.EMPTY, "no content")
        assert breaker.retry_in_seconds() == 60
        clock_now[0] += 61
        breaker.is_available()


def test_an_empty_response_does_not_escalate_a_later_real_failure():
    clock_now = [1000.0]
    breaker = CircuitBreaker(
        failure_threshold=1, base_cooldown=60, max_cooldown=900, clock=lambda: clock_now[0]
    )
    breaker.record_failure(FailureKind.EMPTY, "no content")
    clock_now[0] += 61
    breaker.is_available()
    breaker.record_success(0.1)
    breaker.record_failure(FailureKind.TRANSIENT, "boom")
    assert breaker.retry_in_seconds() == 60, "the first real failure starts at the base cooldown"
