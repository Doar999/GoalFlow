"""结构化日志。每条带 request_id，异步作业再带 job_id。

见 docs/engineering/03-code-and-test-standards.md 第 5 节。
**禁止记录**模型 API Key、口令、会话令牌及其片段或哈希前缀——本模块不做自动脱敏，
调用方不要把这些值放进日志参数或 extra 里。
"""

import json
import logging
import sys
from typing import Any, Final

from goalflow.core.context import get_request_id

# LogRecord 的内建属性。extra 里的自定义字段靠"不在此集合中"识别。
_RESERVED_RECORD_FIELDS: Final = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """单行 JSON，便于容器日志采集。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = get_request_id()
        if request_id is not None:
            payload["request_id"] = request_id

        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_FIELDS:
                payload[key] = value

        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    """配置根 logger。重复调用会替换已有 handler，不会叠加。"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
