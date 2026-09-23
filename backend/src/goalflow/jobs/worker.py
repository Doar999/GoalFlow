"""Worker 侧：领取、续租、执行处理函数、提交结果（决策 E12—E15）。

一次 `run_job` 就是一次尝试，顺序固定：

    写事务：条件更新 queued → running，拿到新租约           （领取，03 第 7 节）
    事务外：处理函数计算；后台线程每 20 秒续租一次
    写事务：条件更新 running → succeeded，**同一事务里**调用 commit 写业务结果

提交的条件是"仍是 running、租约仍是我这个、没有被请求取消"。旧 Worker 租约过期后晚返回，
这个条件更新影响 0 行，业务结果不会落库——这就是"旧 Worker 晚返回提交不了"的全部机制。
"""

import logging
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError

from goalflow.contracts.enums import JobEventType, JobStatus
from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.db.session import Database
from goalflow.jobs.handlers import (
    InputStale,
    JobCommit,
    JobContext,
    JobInterrupted,
    JobRegistry,
    RetryableJobError,
)
from goalflow.jobs.handlers import registry as default_registry
from goalflow.jobs.models import Job
from goalflow.jobs.store import (
    HEARTBEAT_INTERVAL,
    LEASE_DURATION,
    JobView,
    append_event,
    new_id,
    refs_to_json,
    rowcount,
    settle_attempt,
    transition,
    utc_now,
    view_of,
)

_STALE_MESSAGE: Final = "输入已被修改，本次结果已作废"
_NO_HANDLER_MESSAGE: Final = "服务暂时无法处理这类作业"
_UNEXPECTED_MESSAGE: Final = "作业执行出错"

_logger: Final = logging.getLogger(__name__)

Clock = Callable[[], datetime]


class _NotCommitted(Exception):  # noqa: N818  内部控制流：提交条件不满足，回滚本事务
    pass


def run_job(
    database: Database,
    job_id: str,
    *,
    registry: JobRegistry = default_registry,
    clock: Clock = utc_now,
    lease_duration: timedelta = LEASE_DURATION,
    heartbeat_interval: timedelta | None = HEARTBEAT_INTERVAL,
) -> JobStatus | None:
    """执行一次尝试。返回这次尝试把作业推进到的状态；没领到或租约已丢失时返回 None。

    `heartbeat_interval=None` 关闭续租线程，只给测试用来精确控制租约过期的时刻。
    """
    lease_token = new_id()
    job = _claim(database, job_id, lease_token, clock, lease_duration)
    if job is None:
        # 重复投递、已被取消或已被别的 Worker 领走，都会走到这里。这是正常情况。
        _logger.debug("作业未领取", extra={"job_id": job_id})
        return None

    context = JobContext(database, job, lease_token, clock)
    handler = registry.get(job.kind)
    if handler is None:
        _logger.error("没有注册处理函数", extra={"job_id": job_id, "kind": job.kind})
        return _settle(database, context, lease_token, clock, ErrorCode.INTERNAL_ERROR, _NO_HANDLER_MESSAGE, False)

    heartbeat = _Heartbeat(database, context, lease_token, clock, lease_duration, heartbeat_interval)
    heartbeat.start()
    try:
        commit = handler(context)
        context.checkpoint()
        return _commit(database, context, lease_token, clock, commit)
    except JobInterrupted:
        return _settle_unfinished(database, context, lease_token, clock)
    except InputStale:
        return _finish_stale(database, context, lease_token, clock)
    except RetryableJobError as error:
        return _settle(database, context, lease_token, clock, error.code, error.message, True)
    except GoalflowError as error:
        if error.code is ErrorCode.INPUT_STALE:
            return _finish_stale(database, context, lease_token, clock)
        return _settle(database, context, lease_token, clock, error.code, error.message, error.retryable)
    except OperationalError as error:
        # 重试后仍拿不到写锁属于可重试错误（E13）；其他数据库错误按内部错误处理。
        busy = getattr(error.orig, "sqlite_errorcode", None) == sqlite3.SQLITE_BUSY
        _logger.warning("作业执行遇到数据库错误", extra={"job_id": job_id, "busy": busy})
        return _settle(database, context, lease_token, clock, ErrorCode.INTERNAL_ERROR, _UNEXPECTED_MESSAGE, busy)
    except Exception as error:
        # 处理函数的异常可能带着用户内容或模型输出，日志只记类型；需要堆栈时开 DEBUG。
        _logger.error("作业执行出错", extra={"job_id": job_id, "kind": job.kind, "error_type": type(error).__name__})
        _logger.debug("作业执行出错的堆栈", exc_info=True, extra={"job_id": job_id})
        return _settle(database, context, lease_token, clock, ErrorCode.INTERNAL_ERROR, _UNEXPECTED_MESSAGE, False)
    finally:
        heartbeat.stop()


def _claim(
    database: Database,
    job_id: str,
    lease_token: str,
    clock: Clock,
    lease_duration: timedelta,
) -> JobView | None:
    with database.write() as session:
        now = clock()
        claimed = transition(
            session,
            job_id,
            where=[Job.status == JobStatus.QUEUED],
            values={
                "status": JobStatus.RUNNING,
                "lease_token": lease_token,
                "lease_until": now + lease_duration,
                "attempts": Job.attempts + 1,
                "next_attempt_at": None,
            },
            now=now,
        )
        if not claimed:
            return None
        job = session.scalars(select(Job).where(Job.id == job_id)).one()
        append_event(
            session,
            job_id=job_id,
            owner_id=job.owner_id,
            event_type=JobEventType.STARTED,
            now=now,
            payload={"attempt": job.attempts},
        )
        return view_of(job)


def _commit(
    database: Database,
    context: JobContext,
    lease_token: str,
    clock: Clock,
    commit: JobCommit,
) -> JobStatus | None:
    job = context.job
    try:
        with database.write() as session:
            now = clock()
            if not transition(
                session,
                job.id,
                where=[
                    Job.status == JobStatus.RUNNING,
                    Job.lease_token == lease_token,
                    Job.cancel_requested_at.is_(None),
                ],
                values={"status": JobStatus.SUCCEEDED, "lease_token": None, "lease_until": None, "finished_at": now},
                now=now,
            ):
                raise _NotCommitted
            # 条件更新已经成功，写锁在手：从这里到事务结束，没有人能取消它或抢走租约。
            # commit 抛出的任何异常都会让整个事务回滚，作业状态随之退回 running，由 run_job 结算。
            outcome = commit(session)
            refs = list(outcome.result_refs)
            session.execute(
                update(Job)
                .where(Job.id == job.id)
                .values(result_refs_json=refs_to_json(refs))
                .execution_options(synchronize_session=False)
            )
            if outcome.awaiting_confirmation:
                append_event(
                    session,
                    job_id=job.id,
                    owner_id=job.owner_id,
                    event_type=JobEventType.AWAITING_CONFIRMATION,
                    now=now,
                )
            append_event(
                session,
                job_id=job.id,
                owner_id=job.owner_id,
                event_type=JobEventType.COMPLETED,
                now=now,
                payload={"result_refs": [{"type": ref.type, "id": ref.id} for ref in refs]},
            )
        return JobStatus.SUCCEEDED
    except _NotCommitted:
        return _settle_unfinished(database, context, lease_token, clock)


def _settle_unfinished(
    database: Database,
    context: JobContext,
    lease_token: str,
    clock: Clock,
) -> JobStatus | None:
    """没能提交：要么被请求取消（转 cancelled），要么租约已丢失（什么都不做）。"""
    with database.write() as session:
        row = session.execute(
            select(Job.status, Job.lease_token, Job.attempts, Job.cancel_requested_at).where(Job.id == context.job.id)
        ).one()
        if row.status != JobStatus.RUNNING or row.lease_token != lease_token:
            context.mark_lease_lost()
            _logger.info("租约已丢失，放弃本次结果", extra={"job_id": context.job.id})
            return None
        return settle_attempt(
            session,
            job_id=context.job.id,
            owner_id=context.job.owner_id,
            attempts=row.attempts,
            lease_token=lease_token,
            cancel_requested=row.cancel_requested_at is not None,
            now=clock(),
            error_code=ErrorCode.INTERNAL_ERROR,
            # 只有在库里既没取消、也没丢租约时才会用到：检查点误判，按可重试的中断处理。
            error_message=_UNEXPECTED_MESSAGE,
            retryable=True,
            reason="interrupted",
        )


def _settle(
    database: Database,
    context: JobContext,
    lease_token: str,
    clock: Clock,
    code: ErrorCode,
    message: str,
    retryable: bool,
) -> JobStatus | None:
    with database.write() as session:
        row = session.execute(select(Job.attempts, Job.cancel_requested_at).where(Job.id == context.job.id)).one()
        return settle_attempt(
            session,
            job_id=context.job.id,
            owner_id=context.job.owner_id,
            attempts=row.attempts,
            lease_token=lease_token,
            cancel_requested=row.cancel_requested_at is not None,
            now=clock(),
            error_code=code,
            error_message=message,
            retryable=retryable,
            reason="error",
        )


def _finish_stale(
    database: Database,
    context: JobContext,
    lease_token: str,
    clock: Clock,
) -> JobStatus | None:
    with database.write() as session:
        now = clock()
        stale = transition(
            session,
            context.job.id,
            where=[
                Job.status == JobStatus.RUNNING,
                Job.lease_token == lease_token,
                Job.cancel_requested_at.is_(None),
            ],
            values={
                "status": JobStatus.STALE,
                "lease_token": None,
                "lease_until": None,
                "finished_at": now,
                "error_code": ErrorCode.INPUT_STALE,
                "error_message": _STALE_MESSAGE,
            },
            now=now,
        )
        if stale:
            append_event(
                session,
                job_id=context.job.id,
                owner_id=context.job.owner_id,
                event_type=JobEventType.STALE,
                now=now,
            )
    if not stale:
        # 已被请求取消或租约已丢失：取消优先于过期。
        return _settle_unfinished(database, context, lease_token, clock)
    return JobStatus.STALE


class _Heartbeat:
    """后台续租线程（E12）。续租失败（影响 0 行）说明租约已被收回，通知处理函数中止。"""

    def __init__(
        self,
        database: Database,
        context: JobContext,
        lease_token: str,
        clock: Clock,
        lease_duration: timedelta,
        interval: timedelta | None,
    ) -> None:
        self._db = database
        self._context = context
        self._lease_token = lease_token
        self._clock = clock
        self._lease_duration = lease_duration
        self._interval = interval
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._interval is None:
            return
        self._thread = threading.Thread(target=self._run, name=f"lease-{self._context.job.id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        if self._thread is not None:
            self._thread.join()

    def _run(self) -> None:
        assert self._interval is not None
        while not self._stopped.wait(self._interval.total_seconds()):
            try:
                with self._db.write() as session:
                    renewed = rowcount(
                        session.execute(
                            update(Job)
                            .where(
                                Job.id == self._context.job.id,
                                Job.status == JobStatus.RUNNING,
                                Job.lease_token == self._lease_token,
                            )
                            # 续租不改 revision：它不是用户可见的状态变化，不应让客户端的取消请求版本失配。
                            .values(lease_until=self._clock() + self._lease_duration)
                            .execution_options(synchronize_session=False)
                        )
                    )
            except OperationalError:
                _logger.warning("续租失败，下个周期重试", extra={"job_id": self._context.job.id})
                continue
            if renewed != 1:
                self._context.mark_lease_lost()
                return
