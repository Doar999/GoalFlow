"""进程内固定窗口限流（决策 C13）。"""

from datetime import timedelta

import pytest
from auth_support import FakeClock

from goalflow.auth.rate_limit import FixedWindowRateLimiter, RateLimitRule
from goalflow.contracts.errors import ErrorCode, GoalflowError

RULE = RateLimitRule("probe", 3, timedelta(minutes=15))


def test_blocks_after_limit_and_reports_remaining_wait():
    clock = FakeClock()
    limiter = FixedWindowRateLimiter(clock)
    for _ in range(3):
        limiter.hit(RULE, "k")

    clock.advance(timedelta(minutes=5))
    with pytest.raises(GoalflowError) as caught:
        limiter.hit(RULE, "k")

    assert caught.value.code is ErrorCode.RATE_LIMITED
    assert caught.value.retryable is True
    assert caught.value.details == {"retry_after_seconds": 600}


def test_window_resets_after_it_elapses():
    clock = FakeClock()
    limiter = FixedWindowRateLimiter(clock)
    for _ in range(3):
        limiter.hit(RULE, "k")

    clock.advance(timedelta(minutes=15))
    limiter.hit(RULE, "k")


def test_keys_and_rules_are_counted_separately():
    limiter = FixedWindowRateLimiter(FakeClock())
    other_rule = RateLimitRule("other", 3, timedelta(minutes=15))
    for _ in range(3):
        limiter.hit(RULE, "k")

    limiter.hit(RULE, "another-key")
    limiter.hit(other_rule, "k")
