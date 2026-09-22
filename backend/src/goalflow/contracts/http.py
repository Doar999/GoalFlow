"""统一错误结构，以及幂等与 expected_revision 两条全局约束的契约形状。

三条全局约束见 docs/engineering/01-contracts-and-ownership.md 第 5 节：
身份只从服务端会话获得、写操作携带 Idempotency-Key、改既有状态携带 expected_revision。

本模块只定义**形状与校验**。去重记录的存储、版本比对的执行分别属于 T03 与 T07，
见 T02 交接卡决策 A4。
"""

import re
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from goalflow.contracts.errors import ErrorCode, GoalflowError

REQUEST_ID_HEADER: Final = "X-Request-Id"
IDEMPOTENCY_KEY_HEADER: Final = "Idempotency-Key"

IDEMPOTENCY_KEY_MAX_LENGTH: Final = 200
# 限定字符集是为了让 key 能安全地进入日志、URL 与数据库唯一索引而不需要再转义。
_IDEMPOTENCY_KEY_PATTERN: Final = re.compile(r"\A[A-Za-z0-9_.:\-]+\Z")


class ErrorResponse(BaseModel):
    """所有 API 错误的唯一响应体。各模块不得自定义错误体。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "REVISION_CONFLICT",
                "message": "计划版本已过期，请刷新后重试",
                "request_id": "req_0000000000",
                "retryable": False,
                "details": {},
            }
        }
    )

    code: ErrorCode = Field(description="错误码，取自 contracts.errors.ErrorCode 单点定义")
    message: str = Field(description="面向用户的可读说明")
    request_id: str = Field(description="本次请求标识，与响应头 X-Request-Id 一致")
    retryable: bool = Field(description="原样重试是否有意义")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="补充信息。不含其他用户的数据，也不含模型凭证或其片段。",
    )


class RevisionedRequest(BaseModel):
    """修改既有状态的请求基类：必须声明自己看到的是哪个版本。"""

    expected_revision: int = Field(
        ge=0,
        description="客户端读到的版本号。与服务端当前版本不一致时返回 REVISION_CONFLICT。",
    )


class RevisionedResource(BaseModel):
    """被版本保护的资源在响应中必须带上新版本号，否则客户端无法进行下一次修改。"""

    revision: int = Field(ge=0, description="服务端当前版本号")


def normalize_idempotency_key(raw: str | None) -> str:
    """校验并规范化 Idempotency-Key，失败时抛 VALIDATION_FAILED。

    相同 key 配不同内容应返回 IDEMPOTENCY_KEY_CONFLICT，那一步需要读取已记录的请求，
    属于存储层职责，不在本函数内。
    """
    if raw is None:
        raise GoalflowError(
            ErrorCode.VALIDATION_FAILED,
            f"写操作必须携带 {IDEMPOTENCY_KEY_HEADER} 请求头",
            details={"header": IDEMPOTENCY_KEY_HEADER},
        )

    key = raw.strip()
    if not key:
        raise GoalflowError(
            ErrorCode.VALIDATION_FAILED,
            f"{IDEMPOTENCY_KEY_HEADER} 不能为空",
            details={"header": IDEMPOTENCY_KEY_HEADER},
        )
    if len(key) > IDEMPOTENCY_KEY_MAX_LENGTH:
        raise GoalflowError(
            ErrorCode.VALIDATION_FAILED,
            f"{IDEMPOTENCY_KEY_HEADER} 长度不能超过 {IDEMPOTENCY_KEY_MAX_LENGTH} 个字符",
            details={"header": IDEMPOTENCY_KEY_HEADER, "max_length": IDEMPOTENCY_KEY_MAX_LENGTH},
        )
    if not _IDEMPOTENCY_KEY_PATTERN.match(key):
        raise GoalflowError(
            ErrorCode.VALIDATION_FAILED,
            f"{IDEMPOTENCY_KEY_HEADER} 只允许字母、数字与 _ . : - 四种符号",
            details={"header": IDEMPOTENCY_KEY_HEADER},
        )
    return key
