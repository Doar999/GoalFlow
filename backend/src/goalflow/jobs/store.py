"""作业模块内部共用的常量与底层读写。只在 `goalflow.jobs` 包内使用，其他模块不要 import。

状态变化一律走 `transition()`：带条件的 UPDATE 加影响行数判定（03 第 7 节）。条件里写明
"我以为它现在是什么状态、持有的是哪个租约"，影响 0 行就说明别人已经改过它，调用方放弃。
每次状态变化都递增 revision，并在同一个事务里追加对应事件（E19）。
"""

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, cast

from sqlalchemy import ColumnElement, Result, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from goalflow.contracts.enums import JobEventType, JobStatus
from goalflow.idempotency import ResultRef
from goalflow.jobs.models import Job, JobEvent

# 决策 E12：租约 60 秒，后台线程每 20 秒续一次。
LEASE_DURATION: Final = timedelta(seconds=60)
HEARTBEAT_INTERVAL: Final = timedelta(seconds=20)
# 决策 E13：最多 3 次尝试，第 1、2 次失败后分别等 30 秒、120 秒。
MAX_ATTEMPTS: Final = 3
RETRY_BACKOFF: Final = (timedelta(seconds=30), timedelta(seconds=120))
# 决策 E16：outbox 投递失败的退避，从 10 秒起翻倍，封顶 10 分钟。
OUTBOX_BACKOFF_BASE: Final = timedelta(seconds=10)
OUTBOX_BACKOFF_CAP: Final = timedelta(minutes=10)
# 决策 E17：queued 超过这么久没被领取，且期间没有新的 outbox，就补发一条。
STUCK_QUEUED_AFTER: Final = timedelta(minutes=5)

MAX_KIND_LENGTH: Final = 64
MAX_DEDUPE_KEY_LENGTH: Final = 200
MAX_ERROR_MESSAGE_LENGTH: Final = 500


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


def rowcount(result: Result[Any]) -> int:
    return cast(CursorResult[Any], result).rowcount


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def retry_backoff(attempts: int) -> timedelta:
    """第 `attempts` 次尝试失败后要等多久。调用方保证 attempts < MAX_ATTEMPTS。"""
    return RETRY_BACKOFF[min(attempts, len(RETRY_BACKOFF)) - 1]


def outbox_backoff(attempts: int) -> timedelta:
    return min(OUTBOX_BACKOFF_BASE * (1 << max(attempts - 1, 0)), OUTBOX_BACKOFF_CAP)


def transition(
    session: Session,
    job_id: str,
    *,
    where: Sequence[ColumnElement[bool]],
    values: Mapping[str, Any],
    now: datetime,
) -> bool:
    """条件更新一个作业的状态，返回是否真的更新了。revision 与 updated_at 随之推进。"""
    statement = (
        update(Job)
        .where(Job.id == job_id, *where)
        .values(revision=Job.revision + 1, updated_at=now, **values)
        .execution_options(synchronize_session=False)
    )
    return rowcount(session.execute(statement)) == 1


def append_event(
    session: Session,
    *,
    job_id: str,
    owner_id: str,
    event_type: JobEventType,
    now: datetime,
    payload: Mapping[str, Any] | None = None,
) -> int:
    """追加一条事件，返回它的 sequence。

    取 max(sequence) + 1 在并发下是安全的：只在写事务里调用，而写事务在库级写锁上串行。
    (job_id, sequence) 唯一约束兜底。
    """
    last = session.scalar(select(func.max(JobEvent.sequence)).where(JobEvent.job_id == job_id))
    sequence = (last or 0) + 1
    session.add(
        JobEvent(
            id=new_id(),
            owner_id=owner_id,
            job_id=job_id,
            sequence=sequence,
            event_type=event_type.value,
            payload_json=dump_json(dict(payload or {})),
            created_at=now,
        )
    )
    session.flush()
    return sequence


def settle_attempt(
    session: Session,
    *,
    job_id: str,
    owner_id: str,
    attempts: int,
    lease_token: str,
    cancel_requested: bool,
    now: datetime,
    error_code: str,
    error_message: str,
    retryable: bool,
    reason: str,
) -> JobStatus | None:
    """一次尝试没能成功提交时，决定作业去向：取消、等待重试或失败。

    Worker 捕获到异常、恢复扫描发现租约过期，都走这里，规则只有一份（E13、E15）：
    已请求取消的一律转 cancelled；可重试且次数未用尽的转 retry_wait；其余转 failed。
    条件是"仍是 running 且租约仍是这一个"，不满足返回 None——说明别人已经处理过它。
    """
    running_with_lease = [Job.status == JobStatus.RUNNING, Job.lease_token == lease_token]
    released = {"lease_token": None, "lease_until": None}
    message = error_message[:MAX_ERROR_MESSAGE_LENGTH]

    target: JobStatus
    event: JobEventType
    payload: dict[str, Any]
    if cancel_requested:
        target, event, payload = JobStatus.CANCELLED, JobEventType.CANCELLED, {}
        values: dict[str, Any] = {**released, "status": target, "finished_at": now}
    elif retryable and attempts < MAX_ATTEMPTS:
        next_attempt_at = now + retry_backoff(attempts)
        target, event = JobStatus.RETRY_WAIT, JobEventType.RETRYING
        payload = {
            "attempt": attempts,
            "reason": reason,
            "error_code": error_code,
            "next_attempt_at": next_attempt_at.isoformat(timespec="microseconds"),
        }
        values = {
            **released,
            "status": target,
            "next_attempt_at": next_attempt_at,
            "error_code": error_code,
            "error_message": message,
        }
    else:
        target, event = JobStatus.FAILED, JobEventType.FAILED
        payload = {"error_code": error_code, "message": message}
        values = {**released, "status": target, "finished_at": now, "error_code": error_code, "error_message": message}

    if not transition(session, job_id, where=running_with_lease, values=values, now=now):
        return None
    append_event(session, job_id=job_id, owner_id=owner_id, event_type=event, now=now, payload=payload)
    return target


def refs_to_json(refs: Sequence[ResultRef]) -> str:
    return dump_json([{"type": ref.type, "id": ref.id} for ref in refs])


def refs_from_json(raw: str | None) -> tuple[ResultRef, ...]:
    if raw is None:
        return ()
    return tuple(ResultRef(item["type"], item["id"]) for item in json.loads(raw))


@dataclass(frozen=True)
class JobView:
    """作业的只读快照。对外（PR-3 的接口）与对处理函数都只给它，不给 ORM 实体。"""

    id: str
    owner_id: str
    kind: str
    status: JobStatus
    revision: int
    attempts: int
    input_refs: Mapping[str, Any]
    input_revision: int | None
    result_refs: tuple[ResultRef, ...]
    error_code: str | None
    error_message: str | None
    cancel_requested: bool
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


def view_of(job: Job) -> JobView:
    return JobView(
        id=job.id,
        owner_id=job.owner_id,
        kind=job.kind,
        status=JobStatus(job.status),
        revision=job.revision,
        attempts=job.attempts,
        input_refs=json.loads(job.input_refs_json),
        input_revision=job.input_revision,
        result_refs=refs_from_json(job.result_refs_json),
        error_code=job.error_code,
        error_message=job.error_message,
        cancel_requested=job.cancel_requested_at is not None,
        created_at=job.created_at,
        updated_at=job.updated_at,
        finished_at=job.finished_at,
    )
