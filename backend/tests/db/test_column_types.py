"""`goalflow.db.types.UtcDateTime`：存进去的格式决定了字符串比较是否等价于时间比较。"""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects import sqlite

from goalflow.db.types import UtcDateTime

_DIALECT = sqlite.dialect()


def _store(value: datetime) -> str | None:
    return UtcDateTime().process_bind_param(value, _DIALECT)


def test_naive_datetime_is_rejected():
    with pytest.raises(ValueError, match="必须带时区"):
        _store(datetime(2026, 9, 23, 10, 0))


def test_other_timezones_are_converted_to_utc():
    shanghai = timezone(timedelta(hours=8))

    assert _store(datetime(2026, 9, 23, 18, 0, tzinfo=shanghai)) == "2026-09-23T10:00:00.000000+00:00"


def test_width_is_fixed_so_lexical_order_is_chronological():
    """isoformat() 默认在微秒为 0 时省略小数部分，长短不一会让字符串比较出错。"""
    whole = _store(datetime(2026, 9, 23, 10, 0, 0, tzinfo=UTC))
    fractional = _store(datetime(2026, 9, 23, 10, 0, 0, 1, tzinfo=UTC))

    assert whole is not None and fractional is not None
    assert len(whole) == len(fractional) == 32
    assert whole < fractional


def test_round_trip_keeps_microseconds_and_timezone():
    original = datetime(2026, 9, 23, 10, 20, 30, 123456, tzinfo=UTC)
    stored = _store(original)

    assert UtcDateTime().process_result_value(stored, _DIALECT) == original
