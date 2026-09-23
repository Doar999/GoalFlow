"""作业测试的共用辅助。与 conftest 分开的理由同 tests/auth/auth_support.py。

判断"业务结果提交了几次"一律看测试专用表 job_results 的行数——作业状态说成功、而业务
结果其实写了两份，正是本模块要防的 bug。
"""

import argparse
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, table, text
from sqlalchemy.orm import Session

from goalflow.contracts.enums import JobStatus
from goalflow.db.session import Database
from goalflow.idempotency import ResultRef
from goalflow.jobs import JobCommit, JobContext, JobOutcome, JobRegistry, SubmittedJob, run_job, submit_job
from goalflow.jobs.models import Job, JobEvent, OutboxEvent

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "correct horse battery"
KIND = "probe"

PROBE_TABLE_DDL = "CREATE TABLE job_results (id TEXT PRIMARY KEY, job_id TEXT NOT NULL, owner_id TEXT NOT NULL)"


def sqlite_url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path.as_posix()}"


def migrate(url: str) -> None:
    config = Config()
    config.set_main_option("script_location", (BACKEND_ROOT / "migrations").as_posix())
    config.cmd_opts = argparse.Namespace(x=[f"url={url}"])
    command.upgrade(config, "head")


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 23, 8, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


def write_result(session: Session, context: JobContext) -> JobOutcome:
    result_id = str(uuid.uuid4())
    session.execute(
        text("INSERT INTO job_results (id, job_id, owner_id) VALUES (:id, :job_id, :owner_id)"),
        {"id": result_id, "job_id": context.job.id, "owner_id": context.job.owner_id},
    )
    return JobOutcome(result_refs=[ResultRef("probe", result_id)])


def succeeding(context: JobContext) -> JobCommit:
    return lambda session: write_result(session, context)


def registry_with(handler: Callable[[JobContext], JobCommit] = succeeding, kind: str = KIND) -> JobRegistry:
    registry = JobRegistry()
    registry.register(kind, handler)
    return registry


def submit(
    database: Database,
    registry: JobRegistry,
    owner_id: str,
    *,
    now: datetime,
    dedupe_key: str = "input-1",
    input_revision: int | None = None,
) -> SubmittedJob:
    with database.write() as session:
        return submit_job(
            session,
            owner_id=owner_id,
            kind=KIND,
            dedupe_key=dedupe_key,
            input_refs={"goal_id": "g1"},
            input_revision=input_revision,
            now=now,
            registry=registry,
        )


def run(
    database: Database,
    job_id: str,
    registry: JobRegistry,
    clock: Callable[[], datetime],
    *,
    heartbeat_interval: timedelta | None = None,
    lease_duration: timedelta = timedelta(seconds=60),
) -> JobStatus | None:
    """默认关掉续租线程：用例靠推进假时钟精确控制租约何时过期。"""
    return run_job(
        database,
        job_id,
        registry=registry,
        clock=clock,
        heartbeat_interval=heartbeat_interval,
        lease_duration=lease_duration,
    )


def job(database: Database, job_id: str) -> Job:
    with database.read() as session:
        return session.scalars(select(Job).where(Job.id == job_id)).one()


def event_types(database: Database, job_id: str) -> list[str]:
    with database.read() as session:
        return list(
            session.scalars(select(JobEvent.event_type).where(JobEvent.job_id == job_id).order_by(JobEvent.sequence))
        )


def event_sequences(database: Database, job_id: str) -> list[int]:
    with database.read() as session:
        return list(
            session.scalars(select(JobEvent.sequence).where(JobEvent.job_id == job_id).order_by(JobEvent.sequence))
        )


def result_count(database: Database) -> int:
    with database.read() as session:
        return session.scalar(select(func.count()).select_from(table("job_results"))) or 0


def outbox_rows(database: Database, job_id: str) -> list[OutboxEvent]:
    with database.read() as session:
        return list(
            session.scalars(select(OutboxEvent).where(OutboxEvent.job_id == job_id).order_by(OutboxEvent.created_at))
        )


def count(database: Database, model: type[Job] | type[JobEvent] | type[OutboxEvent]) -> int:
    with database.read() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0
