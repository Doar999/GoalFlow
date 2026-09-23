"""作业模块的 Interface：提交、查询、取消、outbox 分发与恢复扫描。

提交方（业务模块）的标准写法：

    with database.write() as session:

        def execute() -> tuple[ResultRef, int]:
            job = submit_job(session, owner_id=..., kind=..., dedupe_key=..., input_refs=..., now=now)
            return ResultRef("job", job.job_id), 202

        outcome = run_idempotent(session, idempotent_request, execute, now=now)
    dispatch_outbox(database, celery_publisher(), now=clock(), job_ids=[outcome.result.id])   # 事务提交之后

`submit_job` 必须在调用方的写事务里：作业、outbox 与 queued 事件和业务写入同生共死（E16）。
投递放在事务提交之后，失败了由 Beat 补投——**不要在写事务里投递消息**。
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import exists, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from goalflow.contracts.enums import TERMINAL_JOB_STATUSES, JobEventType, JobStatus
from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.db.session import Database
from goalflow.jobs.handlers import JobRegistry
from goalflow.jobs.handlers import registry as default_registry
from goalflow.jobs.models import (
    DEDUPE_HOLDING_STATUSES,
    OUTBOX_DISPATCH_JOB,
    OUTBOX_PENDING,
    OUTBOX_SENT,
    Job,
    OutboxEvent,
)
from goalflow.jobs.store import (
    MAX_DEDUPE_KEY_LENGTH,
    STUCK_QUEUED_AFTER,
    JobView,
    append_event,
    dump_json,
    new_id,
    outbox_backoff,
    settle_attempt,
    transition,
    view_of,
)

# 把 job_id 交给队列。实现方不得在其中写库；失败就抛异常，由 outbox 退避后重投。
Publisher = Callable[[str], None]

_DISPATCH_BATCH: Final = 100
_LEASE_EXPIRED_MESSAGE: Final = "上一次执行意外中断"

_logger: Final = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubmittedJob:
    job_id: str
    # False 表示命中去重，返回的是已有作业（01 第 5 节：重复的异步请求返回原作业）。
    created: bool


def submit_job(
    session: Session,
    *,
    owner_id: str,
    kind: str,
    dedupe_key: str,
    input_refs: Mapping[str, Any],
    now: datetime,
    input_revision: int | None = None,
    registry: JobRegistry = default_registry,
) -> SubmittedJob:
    """在调用方的写事务里提交作业。相同 (owner, kind, dedupe_key) 已有有效作业时直接返回它（E10）。

    `dedupe_key` 由提交方按输入计算，例如"日期 + planning_revision"：输入变了，键就该变。
    """
    if kind not in registry:
        # 在提交时拒绝，而不是等 Worker 领取后才发现没人处理（E18）。
        raise ValueError(f"未注册的作业种类：{kind!r}")
    if not 0 < len(dedupe_key) <= MAX_DEDUPE_KEY_LENGTH:
        raise ValueError(f"dedupe_key 长度须在 1—{MAX_DEDUPE_KEY_LENGTH} 之间")

    existing = session.scalar(
        select(Job.id).where(
            Job.owner_id == owner_id,
            Job.kind == kind,
            Job.dedupe_key == dedupe_key,
            Job.status.in_(DEDUPE_HOLDING_STATUSES),
        )
    )
    if existing is not None:
        return SubmittedJob(job_id=existing, created=False)

    job_id = new_id()
    session.add(
        Job(
            id=job_id,
            owner_id=owner_id,
            kind=kind,
            dedupe_key=dedupe_key,
            input_refs_json=dump_json(dict(input_refs)),
            input_revision=input_revision,
            status=JobStatus.QUEUED,
            attempts=0,
            revision=1,
            created_at=now,
            updated_at=now,
        )
    )
    session.flush()
    _add_outbox(session, job_id=job_id, owner_id=owner_id, now=now)
    append_event(session, job_id=job_id, owner_id=owner_id, event_type=JobEventType.QUEUED, now=now)
    return SubmittedJob(job_id=job_id, created=True)


def _not_found() -> GoalflowError:
    # 他人的作业与不存在的作业返回同一个错误，不泄露 ID 是否存在（E21）。
    return GoalflowError(ErrorCode.NOT_FOUND, "作业不存在")


def get_job(database: Database, *, owner_id: str, job_id: str) -> JobView:
    with database.read() as session:
        job = session.scalar(select(Job).where(Job.id == job_id, Job.owner_id == owner_id))
        if job is None:
            raise _not_found()
        return view_of(job)


def cancel_job(
    database: Database,
    *,
    owner_id: str,
    job_id: str,
    expected_revision: int,
    now: datetime,
) -> JobView:
    """取消作业（E15）。

    - 已是终态，或已请求过取消：原样返回当前状态，不报错——用户在"刚好成功"的时刻点取消很常见。
    - queued、retry_wait：直接转 cancelled。
    - running：只记下取消请求，由 Worker 在检查点或提交时转 cancelled。已成功发布的结果不会被撤销。
    """
    with database.write() as session:
        job = session.scalar(select(Job).where(Job.id == job_id, Job.owner_id == owner_id))
        if job is None:
            raise _not_found()
        status = JobStatus(job.status)
        if status in TERMINAL_JOB_STATUSES or (status is JobStatus.RUNNING and job.cancel_requested_at is not None):
            return view_of(job)
        if job.revision != expected_revision:
            raise GoalflowError(
                ErrorCode.REVISION_CONFLICT,
                "作业状态已更新，请刷新后再试",
                details={"current_revision": job.revision},
            )

        unchanged = [Job.status == status, Job.revision == expected_revision]
        if status is JobStatus.RUNNING:
            changed = transition(session, job_id, where=unchanged, values={"cancel_requested_at": now}, now=now)
        else:
            changed = transition(
                session,
                job_id,
                where=unchanged,
                values={
                    "status": JobStatus.CANCELLED,
                    "cancel_requested_at": now,
                    "next_attempt_at": None,
                    "finished_at": now,
                },
                now=now,
            )
            append_event(session, job_id=job_id, owner_id=owner_id, event_type=JobEventType.CANCELLED, now=now)
        # 写事务持有库级写锁，刚读到的状态在事务结束前不会被别人改掉。
        assert changed, "写事务内读到的状态不应失配"
        session.refresh(job)
        return view_of(job)


def _add_outbox(session: Session, *, job_id: str, owner_id: str, now: datetime) -> None:
    session.add(
        OutboxEvent(
            id=new_id(),
            owner_id=owner_id,
            job_id=job_id,
            event_type=OUTBOX_DISPATCH_JOB,
            status=OUTBOX_PENDING,
            attempts=0,
            next_attempt_at=now,
            created_at=now,
        )
    )
    session.flush()


def dispatch_outbox(
    database: Database,
    publish: Publisher,
    *,
    now: datetime,
    job_ids: Sequence[str] | None = None,
) -> int:
    """投递到期的 outbox 行，返回成功投递的条数（E16）。

    投递在事务外进行。投递成功但标记失败时，这一行会在下一轮被重投——重复消息无害，
    Worker 领取是条件更新，同一个作业只会被领取一次。本函数不向调用方抛出投递或标记失败：
    API 在业务事务提交之后调用它，业务已经成功，不能因为队列暂时不可用而报错。
    """
    query = (
        select(OutboxEvent.id, OutboxEvent.job_id, OutboxEvent.attempts)
        .where(OutboxEvent.status == OUTBOX_PENDING, OutboxEvent.next_attempt_at <= now)
        .order_by(OutboxEvent.next_attempt_at)
        .limit(_DISPATCH_BATCH)
    )
    if job_ids is not None:
        query = query.where(OutboxEvent.job_id.in_(job_ids))
    with database.read() as session:
        rows = session.execute(query).all()

    sent = 0
    for row in rows:
        try:
            publish(row.job_id)
        except Exception as error:
            _logger.warning("作业投递失败，稍后重试", extra={"job_id": row.job_id, "error_type": type(error).__name__})
            values: dict[str, Any] = {
                "attempts": OutboxEvent.attempts + 1,
                "next_attempt_at": now + outbox_backoff(row.attempts + 1),
            }
        else:
            sent += 1
            values = {"status": OUTBOX_SENT, "sent_at": now, "attempts": OutboxEvent.attempts + 1}
        try:
            with database.write() as session:
                session.execute(
                    update(OutboxEvent)
                    .where(OutboxEvent.id == row.id, OutboxEvent.status == OUTBOX_PENDING)
                    .values(**values)
                    .execution_options(synchronize_session=False)
                )
        except OperationalError:
            _logger.warning("outbox 状态回写失败，下一轮会重投", extra={"job_id": row.job_id})
    return sent


@dataclass(frozen=True)
class RecoveryReport:
    lease_expired: int = 0
    requeued: int = 0
    redispatched: int = 0


def recover(database: Database, *, now: datetime) -> RecoveryReport:
    """恢复扫描（E17）。每一步都是条件更新，重复或并发执行不会重复推进状态。

    1. running 且租约已过期：Worker 多半已经死了，这一次尝试按"可重试的中断"结算。
    2. retry_wait 已到期：回到 queued，并写新的 outbox。
    3. queued 太久没被领取，且期间没有新的 outbox：补一条 outbox（覆盖 Redis 重启丢消息）。
    """
    with database.write() as session:
        expired = session.execute(
            select(Job.id, Job.owner_id, Job.attempts, Job.lease_token, Job.cancel_requested_at).where(
                Job.status == JobStatus.RUNNING, Job.lease_until < now
            )
        ).all()
        lease_expired = 0
        for job in expired:
            settled = settle_attempt(
                session,
                job_id=job.id,
                owner_id=job.owner_id,
                attempts=job.attempts,
                lease_token=job.lease_token,
                cancel_requested=job.cancel_requested_at is not None,
                now=now,
                error_code=ErrorCode.INTERNAL_ERROR,
                error_message=_LEASE_EXPIRED_MESSAGE,
                retryable=True,
                reason="lease_expired",
            )
            lease_expired += settled is not None

        due = session.execute(
            select(Job.id, Job.owner_id).where(Job.status == JobStatus.RETRY_WAIT, Job.next_attempt_at <= now)
        ).all()
        requeued = 0
        for waiting in due:
            if transition(
                session,
                waiting.id,
                where=[Job.status == JobStatus.RETRY_WAIT],
                values={"status": JobStatus.QUEUED, "next_attempt_at": None},
                now=now,
            ):
                _add_outbox(session, job_id=waiting.id, owner_id=waiting.owner_id, now=now)
                append_event(
                    session, job_id=waiting.id, owner_id=waiting.owner_id, event_type=JobEventType.QUEUED, now=now
                )
                requeued += 1

        stale_after = now - STUCK_QUEUED_AFTER
        recent_outbox = exists().where(OutboxEvent.job_id == Job.id, OutboxEvent.created_at > stale_after)
        stuck = session.execute(
            select(Job.id, Job.owner_id).where(
                Job.status == JobStatus.QUEUED, Job.updated_at <= stale_after, ~recent_outbox
            )
        ).all()
        for forgotten in stuck:
            _add_outbox(session, job_id=forgotten.id, owner_id=forgotten.owner_id, now=now)

    report = RecoveryReport(lease_expired=lease_expired, requeued=requeued, redispatched=len(stuck))
    if report != RecoveryReport():
        _logger.info(
            "作业恢复扫描",
            extra={"lease_expired": lease_expired, "requeued": requeued, "redispatched": len(stuck)},
        )
    return report
