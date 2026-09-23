"""作业事件的持续读取，供 SSE 接口使用（决策 E20）。

SQLite 没有 LISTEN/NOTIFY，只能轮询。WAL 下读不阻塞写，按 500 毫秒一次读 job_events 的代价很小。
本模块只产出事件与心跳，SSE 的文本格式属于 HTTP 层。

事件与作业状态在同一个事务里写入（E19），而每次轮询在同一个读事务里取事件和作业状态，所以
"读到的作业已是终态"就意味着"终态事件已在本批或更早发出"：发完本批即可结束。客户端带着终态事件
之后的 Last-Event-ID 重连时，同样按作业状态直接结束。
"""

import json
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import select

from goalflow.contracts.enums import TERMINAL_JOB_STATUSES, JobEventType, JobStatus
from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.db.session import Database
from goalflow.jobs.models import Job, JobEvent

_BATCH: Final = 100


@dataclass(frozen=True)
class JobEventView:
    sequence: int
    type: JobEventType
    payload: Mapping[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class Heartbeat:
    """一段时间没有新事件时发出，让代理与浏览器知道连接还活着，也让服务端及时发现客户端已断开。"""


HEARTBEAT: Final = Heartbeat()


@dataclass(frozen=True)
class StreamSettings:
    poll_interval: timedelta = timedelta(milliseconds=500)
    heartbeat_interval: timedelta = timedelta(seconds=15)
    # 单条连接的上限。到点关闭，EventSource 会带着 Last-Event-ID 自动重连，不丢事件。
    max_duration: timedelta = timedelta(minutes=5)


def _poll(database: Database, *, owner_id: str, job_id: str, after: int) -> tuple[list[JobEventView], bool]:
    with database.read() as session:
        status = session.scalar(select(Job.status).where(Job.id == job_id, Job.owner_id == owner_id))
        if status is None:
            raise GoalflowError(ErrorCode.NOT_FOUND, "作业不存在")
        rows = session.scalars(
            select(JobEvent)
            .where(JobEvent.job_id == job_id, JobEvent.owner_id == owner_id, JobEvent.sequence > after)
            .order_by(JobEvent.sequence)
            .limit(_BATCH)
        ).all()
        events = [
            JobEventView(
                sequence=row.sequence,
                type=JobEventType(row.event_type),
                payload=json.loads(row.payload_json),
                created_at=row.created_at,
            )
            for row in rows
        ]
    return events, JobStatus(status) in TERMINAL_JOB_STATUSES


def follow_events(
    database: Database,
    *,
    owner_id: str,
    job_id: str,
    after_sequence: int = 0,
    settings: StreamSettings | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[JobEventView | Heartbeat]:
    """依次产出 sequence 大于 `after_sequence` 的事件，作业结束且事件发完、或到达时长上限时结束。

    调用方应先用 `get_job` 确认归属再开始迭代：生成器第一次被迭代时才执行，HTTP 响应头那时已经发出，
    归属错误就没法再作为普通的错误响应返回了。这里每次查询仍带 owner_id，作为第二道防线。
    """
    settings = settings or StreamSettings()
    started = last_sent = monotonic()
    cursor = after_sequence
    while True:
        events, job_finished = _poll(database, owner_id=owner_id, job_id=job_id, after=cursor)
        for event in events:
            yield event
            cursor = event.sequence
            last_sent = monotonic()
        if len(events) == _BATCH:
            # 还有没读完的事件；终态事件一定排在它们后面，读完再判断。
            continue
        if job_finished:
            return
        now = monotonic()
        if now - started >= settings.max_duration.total_seconds():
            return
        if now - last_sent >= settings.heartbeat_interval.total_seconds():
            yield HEARTBEAT
            last_sent = now
        sleep(settings.poll_interval.total_seconds())
