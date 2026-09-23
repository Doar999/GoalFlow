"""通用幂等存储的 Interface（T07 决策 E4—E8）。

写接口按下面的方式接入，事务边界仍归业务模块自己：

    request = IdempotentRequest.build(owner_id=user.user_id, operation="create_goal", key=key, body=payload)
    with database.write() as session:
        outcome = run_idempotent(session, request, lambda: _insert_goal(session, ...), now=clock())
        goal = session.get(Goal, outcome.result.id)   # 首次执行与重放都按引用读当前状态（E7）

**`run_idempotent` 必须在 `Database.write()` 里调用，业务写入必须在同一个事务里完成。**
正确性全靠这一条：`write()` 是 `BEGIN IMMEDIATE`，同一个 key 的并发请求在库级写锁上串行，
后到的那个拿到锁时，先到的已经提交，它直接看到记录并重放。所以这里没有"处理中"状态，
也没有两个请求同时执行业务的窗口。业务写入要是拆成多个事务（中间要调模型），这条保证就没了，
那种操作应改为提交作业，由作业去重兜底（E6）。
"""

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, cast

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.contracts.http import IDEMPOTENCY_KEY_MAX_LENGTH
from goalflow.db.session import Database
from goalflow.idempotency.models import IdempotencyRecord

# 决策 E8。过期的记录不再参与去重，同一个 key 按新请求处理。
RETENTION: Final = timedelta(days=7)

_OPERATION_MAX_LENGTH: Final = 64
_RESULT_TYPE_MAX_LENGTH: Final = 32
_RESULT_ID_MAX_LENGTH: Final = 36


@dataclass(frozen=True)
class ResultRef:
    """写操作产出的资源引用，如 `ResultRef("goal", goal_id)`、`ResultRef("job", job_id)`。"""

    type: str
    id: str

    def __post_init__(self) -> None:
        if not 0 < len(self.type) <= _RESULT_TYPE_MAX_LENGTH:
            raise ValueError(f"result type 长度须在 1—{_RESULT_TYPE_MAX_LENGTH} 之间：{self.type!r}")
        if not 0 < len(self.id) <= _RESULT_ID_MAX_LENGTH:
            raise ValueError(f"result id 长度须在 1—{_RESULT_ID_MAX_LENGTH} 之间")


def compute_request_hash(
    operation: str,
    *,
    body: BaseModel | None = None,
    path_params: Mapping[str, str | int] | None = None,
) -> str:
    """请求内容的摘要（决策 E5）。

    对**校验后的**请求模型取摘要，而不是对原始字节：客户端重试时空白、键顺序不同，或者
    省略了取默认值的字段，都不应被判成"不同内容"。
    """
    payload: dict[str, Any] = {
        "operation": operation,
        "path": dict(path_params or {}),
        "body": body.model_dump(mode="json") if body is not None else None,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IdempotentRequest:
    """一次写请求的去重身份。`owner_id` 只能来自服务端会话，不能来自客户端。"""

    owner_id: str
    operation: str
    key: str
    request_hash: str

    def __post_init__(self) -> None:
        if not 0 < len(self.operation) <= _OPERATION_MAX_LENGTH:
            raise ValueError(f"operation 长度须在 1—{_OPERATION_MAX_LENGTH} 之间：{self.operation!r}")
        if not 0 < len(self.key) <= IDEMPOTENCY_KEY_MAX_LENGTH:
            # 路由层的 require_idempotency_key 已经校验过；到这里还不合法说明调用方绕过了它。
            raise ValueError("Idempotency-Key 未经 require_idempotency_key 校验")

    @classmethod
    def build(
        cls,
        *,
        owner_id: str,
        operation: str,
        key: str,
        body: BaseModel | None = None,
        path_params: Mapping[str, str | int] | None = None,
    ) -> "IdempotentRequest":
        """`operation` 用 05-module-contracts 里的模块操作名，如 `create_goal`。"""
        return cls(
            owner_id=owner_id,
            operation=operation,
            key=key,
            request_hash=compute_request_hash(operation, body=body, path_params=path_params),
        )


@dataclass(frozen=True)
class IdempotentOutcome:
    result: ResultRef
    status_code: int
    # True 表示这次没有执行业务，结果来自先前的同一请求。
    replayed: bool


def _is_live(record: IdempotencyRecord, now: datetime) -> bool:
    return now - record.created_at < RETENTION


def run_idempotent(
    session: Session,
    request: IdempotentRequest,
    execute: Callable[[], tuple[ResultRef, int]],
    *,
    now: datetime,
) -> IdempotentOutcome:
    """同一个 key、同一内容只执行一次 `execute`；之后的请求重放第一次的结果引用与状态码。

    `execute` 在同一个会话里写业务数据，返回 `(结果引用, 2xx 状态码)`。它抛出的异常原样
    传出，由调用方的 `write()` 回滚——回滚时本函数还没插入记录，所以失败的请求不留痕迹，
    同一个 key 可以重试（E6）。
    """
    existing = session.scalars(
        select(IdempotencyRecord).where(
            IdempotencyRecord.owner_id == request.owner_id,
            IdempotencyRecord.request_key == request.key,
        )
    ).one_or_none()

    if existing is not None:
        if _is_live(existing, now):
            # operation 已经算进摘要（E5），换操作复用 key 同样会在这里被识别。
            if existing.request_hash != request.request_hash:
                raise GoalflowError(
                    ErrorCode.IDEMPOTENCY_KEY_CONFLICT,
                    "这个 Idempotency-Key 已用于另一项请求；发起新的操作请使用新的 key",
                )
            return IdempotentOutcome(
                result=ResultRef(existing.result_type, existing.result_id),
                status_code=existing.response_status,
                replayed=True,
            )
        # 过期但还没被清理：按新请求处理，不能让清理任务的执行时机决定行为（E8）。
        # 必须先 flush 删除：ORM 的工作单元默认先执行 INSERT 后执行 DELETE，会撞唯一约束。
        session.delete(existing)
        session.flush()

    result, status_code = execute()
    if not 200 <= status_code <= 299:
        raise ValueError(f"只有成功的请求会留下幂等记录，收到状态码 {status_code}")

    session.add(
        IdempotencyRecord(
            id=str(uuid.uuid4()),
            owner_id=request.owner_id,
            operation=request.operation,
            request_key=request.key,
            request_hash=request.request_hash,
            result_type=result.type,
            result_id=result.id,
            response_status=status_code,
            created_at=now,
        )
    )
    session.flush()
    return IdempotentOutcome(result=result, status_code=status_code, replayed=False)


def purge_expired(database: Database, *, now: datetime) -> int:
    """删除过了保留期的记录，返回删除条数。由 Beat 每日调用（Beat 装配随 PR-2）。"""
    with database.write() as session:
        result = session.execute(delete(IdempotencyRecord).where(IdempotencyRecord.created_at <= now - RETENTION))
        return cast(CursorResult[Any], result).rowcount
