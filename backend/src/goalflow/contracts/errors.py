"""错误码与业务异常基类。错误码在此单点定义，新增属于公共契约变更。

见 docs/engineering/01-contracts-and-ownership.md 第 4 节。
"""

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """对外错误码。"""

    # 类 docstring 会进入 OpenAPI 文档，因此出处说明放在注释里：
    # REVISION_CONFLICT 及以下五项来自 docs/development/05-module-contracts.md 的"共同规则"，
    # 其余为承载 HTTP 层与三条全局约束所必需的最小补充，见 T02 交接卡决策 A8。

    # —— 通用 ——
    VALIDATION_FAILED = "VALIDATION_FAILED"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    RATE_LIMITED = "RATE_LIMITED"

    # —— 账号（T03 交接卡决策 C11）——
    # INVALID_CREDENTIALS 与 UNAUTHENTICATED 分开：后者表示"没有有效会话"，前端据此跳转登录页；
    # 登录失败若复用它会引起误跳转。
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    ACCOUNT_IDENTIFIER_UNAVAILABLE = "ACCOUNT_IDENTIFIER_UNAVAILABLE"
    REGISTRATION_CLOSED = "REGISTRATION_CLOSED"

    # —— 幂等与版本（01-contracts-and-ownership.md 第 5 节）——
    IDEMPOTENCY_KEY_CONFLICT = "IDEMPOTENCY_KEY_CONFLICT"
    REVISION_CONFLICT = "REVISION_CONFLICT"

    # —— 业务冲突（05-module-contracts.md 共同规则）——
    BUDGET_CONFLICT = "BUDGET_CONFLICT"
    DEPENDENCY_CYCLE = "DEPENDENCY_CYCLE"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    INPUT_STALE = "INPUT_STALE"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"

    # —— 目标生命周期（T04 交接卡决策 A10/A2；17 号文档第 2 节）——
    # 状态机不允许的转换：paused → completed、maintenance 请求 completed、撤销窗口外撤销等。
    # 与 VALIDATION_FAILED 分开：请求本身格式正确，被拒的是当前状态下的这次操作。
    GOAL_STATE_CONFLICT = "GOAL_STATE_CONFLICT"


HTTP_STATUS_BY_ERROR_CODE: dict[ErrorCode, int] = {
    ErrorCode.VALIDATION_FAILED: 422,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.INVALID_CREDENTIALS: 401,
    ErrorCode.ACCOUNT_IDENTIFIER_UNAVAILABLE: 409,
    ErrorCode.REGISTRATION_CLOSED: 403,
    ErrorCode.IDEMPOTENCY_KEY_CONFLICT: 409,
    ErrorCode.REVISION_CONFLICT: 409,
    ErrorCode.BUDGET_CONFLICT: 409,
    ErrorCode.DEPENDENCY_CYCLE: 409,
    ErrorCode.CONFIRMATION_REQUIRED: 409,
    ErrorCode.INPUT_STALE: 409,
    ErrorCode.MODEL_UNAVAILABLE: 503,
    ErrorCode.GOAL_STATE_CONFLICT: 409,
}

# 可重试指的是"原样重试有意义"。版本冲突与预算冲突需要用户先看到新状态再决定，
# 原样重试只会再次失败，因此不算可重试。
RETRYABLE_ERROR_CODES: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.INTERNAL_ERROR,
        ErrorCode.MODEL_UNAVAILABLE,
        # 等到 details.retry_after_seconds 之后原样重试即可。
        ErrorCode.RATE_LIMITED,
    }
)

# RATE_LIMITED 的 details 必带此键，全局处理器据此补 Retry-After 响应头。
RETRY_AFTER_DETAIL_KEY = "retry_after_seconds"


class GoalflowError(Exception):
    """业务异常基类。路由不自行拼错误 JSON，一律抛出本异常由全局处理器转换。"""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        # details 不得包含其他用户的数据，也不得包含模型凭证或其片段。
        self.details: dict[str, Any] = details or {}

    @property
    def http_status(self) -> int:
        return HTTP_STATUS_BY_ERROR_CODE[self.code]

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE_ERROR_CODES

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code.value!r}, message={self.message!r})"
