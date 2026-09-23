"""作业处理函数面向的 Interface：注册表、运行上下文、处理函数可抛的异常。

一个处理函数长这样（T08 起的业务模块照此写）：

    @registry.handler("route_generation")
    def generate_routes(ctx: JobContext) -> JobCommit:
        snapshot = read_inputs(ctx.job.input_refs)        # 读事务，取输入快照
        candidate = call_model(snapshot)                   # 事务外：调模型、做计算
        ctx.stage_completed("routes_drafted")
        ctx.checkpoint()                                   # 被取消或租约丢失时在这里中止

        def commit(session: Session) -> JobOutcome:       # 由作业模块在写事务里调用
            if current_revision(session) != ctx.job.input_revision:
                raise InputStale()                         # 作业转 stale，业务结果不落库
            route_set_id = save_routes(session, candidate)
            return JobOutcome(result_refs=[ResultRef("route_set", route_set_id)])

        return commit

处理函数本体在事务外运行，可能被执行多次（重试、旧 Worker 晚返回），所以它**不能写业务表**；
所有业务写入都放进返回的 `commit`。作业模块保证 `commit` 与"作业转为 succeeded"在同一个写事务里，
且只有仍持有有效租约、未被取消时才会调用它（决策 E14）。
"""

import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from goalflow.contracts.enums import JobEventType, JobStatus
from goalflow.contracts.errors import ErrorCode
from goalflow.db.session import Database
from goalflow.idempotency import ResultRef
from goalflow.jobs.models import Job
from goalflow.jobs.store import MAX_KIND_LENGTH, JobView, append_event

_MAX_STAGE_LENGTH: Final = 64


class RetryableJobError(Exception):
    """原样重试有意义的失败：模型超时、供应商暂时不可用等（E13）。

    `message` 面向用户，不要放异常原文、凭证或模型输出。
    """

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class InputStale(Exception):  # noqa: N818  名字取自错误码 INPUT_STALE，读作一个状态而不是一个错误
    """输入在作业运行期间已被修改。作业转为 stale，不写业务结果（E14）。"""


class JobInterrupted(Exception):  # noqa: N818  这是控制流信号，不是错误
    """作业被取消或租约已丢失，处理函数应立即停止。不要在处理函数里捕获它。"""


@dataclass(frozen=True)
class JobOutcome:
    result_refs: Sequence[ResultRef] = ()
    # 结果需要用户确认才会生效时置 True，事件流里会先发 awaiting_confirmation 再发 completed。
    awaiting_confirmation: bool = False


JobCommit = Callable[[Session], JobOutcome]


class JobContext:
    """一次尝试的运行上下文。由 Worker 构造，处理函数只读 `job`、调用两个方法。"""

    def __init__(
        self,
        database: Database,
        job: JobView,
        lease_token: str,
        clock: Callable[[], datetime],
    ) -> None:
        self._db = database
        self._lease_token = lease_token
        self._clock = clock
        self._lease_lost = threading.Event()
        self.job = job

    @property
    def lease_lost(self) -> bool:
        return self._lease_lost.is_set()

    def mark_lease_lost(self) -> None:
        """由续租线程调用。之后的检查点都会中止。"""
        self._lease_lost.set()

    def checkpoint(self) -> None:
        """在长步骤之间调用：已被取消或租约已丢失时抛 JobInterrupted。"""
        if self.lease_lost:
            raise JobInterrupted("租约已丢失")
        with self._db.read() as session:
            row = session.execute(
                select(Job.status, Job.lease_token, Job.cancel_requested_at).where(Job.id == self.job.id)
            ).one()
        if row.status != JobStatus.RUNNING or row.lease_token != self._lease_token:
            self.mark_lease_lost()
            raise JobInterrupted("租约已丢失")
        if row.cancel_requested_at is not None:
            raise JobInterrupted("作业已被取消")

    def stage_completed(self, stage: str) -> None:
        """发布一条阶段完成事件（E19）。租约已丢失时不写，并抛 JobInterrupted。"""
        if not 0 < len(stage) <= _MAX_STAGE_LENGTH:
            raise ValueError(f"stage 名长度须在 1—{_MAX_STAGE_LENGTH} 之间")
        with self._db.write() as session:
            owner_id = session.scalar(
                select(Job.owner_id).where(
                    Job.id == self.job.id,
                    Job.status == JobStatus.RUNNING,
                    Job.lease_token == self._lease_token,
                )
            )
            if owner_id is None:
                self.mark_lease_lost()
                raise JobInterrupted("租约已丢失")
            append_event(
                session,
                job_id=self.job.id,
                owner_id=owner_id,
                event_type=JobEventType.STAGE_COMPLETED,
                now=self._clock(),
                payload={"stage": stage},
            )


JobHandler = Callable[[JobContext], JobCommit]


@dataclass
class JobRegistry:
    """作业种类 → 处理函数。各业务模块在自己的包里注册（E18）。

    API 进程与 Worker 进程都要 import 注册处理函数的模块：API 在提交时据此拒绝未知的 kind，
    Worker 据此找到处理函数。Worker 侧的模块清单见 `celery_app.HANDLER_MODULES`。
    """

    _handlers: dict[str, JobHandler] = field(default_factory=dict)

    def register(self, kind: str, handler: JobHandler) -> None:
        if not 0 < len(kind) <= MAX_KIND_LENGTH:
            raise ValueError(f"kind 长度须在 1—{MAX_KIND_LENGTH} 之间")
        if kind in self._handlers and self._handlers[kind] is not handler:
            raise ValueError(f"作业种类 {kind!r} 已注册了另一个处理函数")
        self._handlers[kind] = handler

    def handler(self, kind: str) -> Callable[[JobHandler], JobHandler]:
        def decorate(function: JobHandler) -> JobHandler:
            self.register(kind, function)
            return function

        return decorate

    def get(self, kind: str) -> JobHandler | None:
        return self._handlers.get(kind)

    def __contains__(self, kind: object) -> bool:
        return kind in self._handlers

    def __iter__(self) -> Iterator[str]:
        return iter(self._handlers)


# 进程级默认注册表。测试用自己构造的 JobRegistry，不要往这里注册假处理函数。
registry: Final = JobRegistry()
