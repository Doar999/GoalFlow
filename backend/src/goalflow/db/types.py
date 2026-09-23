"""跨模块共用的列类型。

SQLite 的声明类型只影响亲和性，不做校验（T01 B6），所以"存进去的是什么格式"完全由这里
决定。每个类型都要保证：写入时拒绝格式不对的值，而不是把它原样存进去。
"""

from datetime import UTC, datetime
from typing import Final

from sqlalchemy import String
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

# 2026-09-23T10:20:30.123456+00:00，固定 32 个字符。
_UTC_TIMESTAMP_LENGTH: Final = 32


class UtcDateTime(TypeDecorator[datetime]):
    """事件时间：存 ISO 8601 UTC 带微秒的 TEXT（03 第 1 节，T01 B3）。

    两处与 SQLAlchemy 自带 `DateTime` 的差别都关系到正确性：

    - **只接受带时区的 datetime。** 朴素 datetime 无从知道是哪个时区，静默当成 UTC 存进去
      会让比较结果差出几个小时，而且不会报错。
    - **微秒位固定输出。** `isoformat()` 在微秒为 0 时会省略小数部分，结果长短不一，
      字符串比较就不再等价于时间比较——`WHERE expires_at > ?` 会悄悄出错。
    """

    impl = String(_UTC_TIMESTAMP_LENGTH)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("事件时间必须带时区；朴素 datetime 无法确定它是哪个时区的时间")
        return value.astimezone(UTC).isoformat(timespec="microseconds")

    def process_result_value(self, value: str | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return datetime.fromisoformat(value)
