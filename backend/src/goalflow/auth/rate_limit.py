"""进程内固定窗口限流（T03 决策 C13）。

计数只在当前 API 进程内有效：多个 worker 时阈值按进程数放大，重启后清零。这是有意的取舍——
换成 Redis 会让 API 多一个运行时依赖，换成 SQLite 表会让每次失败登录都去抢库级写锁。
部署层的 Nginx `limit_req` 负责兜底（T13）。

每次尝试都计数，成功也不清零：限流先于密码校验执行，才能挡住对哈希计算的消耗。
"""

import math
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from goalflow.contracts.errors import RETRY_AFTER_DETAIL_KEY, ErrorCode, GoalflowError

# 窗口数超过这个值时顺手清掉已过期的窗口，防止被大量不同的键撑爆内存。
_PRUNE_THRESHOLD: Final = 10_000


@dataclass(frozen=True)
class RateLimitRule:
    name: str
    limit: int
    window: timedelta


LOGIN_PER_IDENTIFIER: Final = RateLimitRule("login:identifier", 10, timedelta(minutes=15))
LOGIN_PER_IP_PREFIX: Final = RateLimitRule("login:ip", 30, timedelta(minutes=15))
REGISTER_PER_IP_PREFIX: Final = RateLimitRule("register:ip", 5, timedelta(hours=1))
RESET_PER_IP_PREFIX: Final = RateLimitRule("reset:ip", 10, timedelta(minutes=15))
# 改密要校验当前密码。会话被盗时，这里挡住的是对当前密码的猜测。
CHANGE_PASSWORD_PER_USER: Final = RateLimitRule("change-password:user", 10, timedelta(minutes=15))

_LONGEST_WINDOW: Final = max(
    rule.window
    for rule in (
        LOGIN_PER_IDENTIFIER,
        LOGIN_PER_IP_PREFIX,
        REGISTER_PER_IP_PREFIX,
        RESET_PER_IP_PREFIX,
        CHANGE_PASSWORD_PER_USER,
    )
)


class FixedWindowRateLimiter:
    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        # (规则名, 键) → (窗口开始时刻, 已用次数)
        self._windows: dict[tuple[str, str], tuple[datetime, int]] = {}

    def hit(self, rule: RateLimitRule, key: str) -> None:
        """记一次尝试；超过阈值时抛 RATE_LIMITED，并给出需要等待的秒数。"""
        now = self._clock()
        with self._lock:
            if len(self._windows) > _PRUNE_THRESHOLD:
                horizon = now - _LONGEST_WINDOW
                self._windows = {slot: state for slot, state in self._windows.items() if state[0] > horizon}

            slot = (rule.name, key)
            started_at, used = self._windows.get(slot, (now, 0))
            if now >= started_at + rule.window:
                started_at, used = now, 0

            if used >= rule.limit:
                retry_after = max(1, math.ceil((started_at + rule.window - now).total_seconds()))
                raise GoalflowError(
                    ErrorCode.RATE_LIMITED,
                    "尝试次数过多，请稍后再试",
                    details={RETRY_AFTER_DETAIL_KEY: retry_after},
                )
            self._windows[slot] = (started_at, used + 1)
