"""请求上下文。

每条日志都要带 request_id，异步作业还要带 job_id
（见 docs/engineering/03-code-and-test-standards.md 第 5 节）。用 ContextVar 承载，
这样日志与异常处理器不必层层传参。
"""

import uuid
from contextvars import ContextVar, Token
from typing import Final

REQUEST_ID_PREFIX: Final = "req_"

_request_id: ContextVar[str | None] = ContextVar("goalflow_request_id", default=None)


def new_request_id() -> str:
    """生成一个新的请求标识。前缀便于在日志里一眼区分于其他标识。"""
    return f"{REQUEST_ID_PREFIX}{uuid.uuid4().hex[:16]}"


def get_request_id() -> str | None:
    """取当前请求标识；不在请求上下文中时返回 None。"""
    return _request_id.get()


def bind_request_id(request_id: str) -> Token[str | None]:
    return _request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)
