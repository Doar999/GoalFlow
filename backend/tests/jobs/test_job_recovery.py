"""恢复扫描（T07 决策 E13、E17，交接卡第 5 节"作业"）。"""

import subprocess
import sys
import threading
from datetime import timedelta
from pathlib import Path

from jobs_support import (
    FakeClock,
    event_types,
    job,
    outbox_rows,
    registry_with,
    result_count,
    run,
    sqlite_url,
    submit,
)
from sqlalchemy import update

from goalflow.contracts.enums import JobStatus
from goalflow.contracts.errors import ErrorCode
from goalflow.db.session import Database
from goalflow.jobs import RecoveryReport, dispatch_outbox, recover
from goalflow.jobs.models import Job
from goalflow.jobs.store import utc_now

_HANGING_WORKER = Path(__file__).resolve().parent / "hanging_worker.py"


def test_killed_worker_process_is_recovered_and_the_job_completes_once(db_path: Path, database: Database, alice: str):
    """PRD Q03：Worker 重启后能查到最终状态，无虚假成功。这里真的杀掉一个进程。"""
    registry = registry_with()
    job_id = submit(database, registry, alice, now=utc_now()).job_id
    worker = subprocess.Popen(  # 参数是本测试生成的临时路径与 ID
        [sys.executable, str(_HANGING_WORKER), sqlite_url(db_path), job_id],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert worker.stdout is not None
        assert worker.stdout.readline().strip() == "claimed"
    finally:
        worker.kill()
        worker.wait(timeout=10)

    assert job(database, job_id).status == JobStatus.RUNNING
    after_lease = utc_now() + timedelta(seconds=61)
    assert recover(database, now=after_lease).lease_expired == 1
    after_backoff = after_lease + timedelta(seconds=30)
    assert recover(database, now=after_backoff).requeued == 1

    assert run(database, job_id, registry, lambda: after_backoff) is JobStatus.SUCCEEDED
    assert job(database, job_id).attempts == 2
    assert result_count(database) == 1


def _force_running(database: Database, job_id: str, clock: FakeClock, **values: object) -> None:
    """把作业摆成"某个已失联的 Worker 持有着过期租约"。"""
    with database.write() as session:
        session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                {
                    "status": JobStatus.RUNNING,
                    "lease_token": "lost-worker",
                    "lease_until": clock() - timedelta(seconds=1),
                    **values,
                }
            )
        )


def test_lease_expiry_on_the_last_attempt_fails_the_job(database: Database, clock: FakeClock, alice: str):
    job_id = submit(database, registry_with(), alice, now=clock()).job_id
    _force_running(database, job_id, clock, attempts=3)

    assert recover(database, now=clock()) == RecoveryReport(lease_expired=1)

    failed = job(database, job_id)
    assert (failed.status, failed.error_code, failed.lease_token) == (JobStatus.FAILED, ErrorCode.INTERNAL_ERROR, None)
    assert event_types(database, job_id)[-1] == "failed"


def test_lease_expiry_with_a_pending_cancel_ends_cancelled(database: Database, clock: FakeClock, alice: str):
    job_id = submit(database, registry_with(), alice, now=clock()).job_id
    _force_running(database, job_id, clock, attempts=1, cancel_requested_at=clock())

    recover(database, now=clock())

    assert job(database, job_id).status == JobStatus.CANCELLED


def test_a_live_lease_is_left_alone(database: Database, clock: FakeClock, alice: str):
    job_id = submit(database, registry_with(), alice, now=clock()).job_id
    _force_running(database, job_id, clock, attempts=1, lease_until=clock() + timedelta(seconds=1))

    assert recover(database, now=clock()) == RecoveryReport()
    assert job(database, job_id).status == JobStatus.RUNNING


def test_concurrent_recovery_scans_do_not_double_advance(database: Database, clock: FakeClock, alice: str):
    registry = registry_with()
    expired_id = submit(database, registry, alice, now=clock(), dedupe_key="expired").job_id
    waiting_id = submit(database, registry, alice, now=clock(), dedupe_key="waiting").job_id
    _force_running(database, expired_id, clock, attempts=1)
    with database.write() as session:
        session.execute(
            update(Job)
            .where(Job.id == waiting_id)
            .values(status=JobStatus.RETRY_WAIT, attempts=1, next_attempt_at=clock())
        )

    barrier = threading.Barrier(2)
    reports: list[RecoveryReport] = []

    def scan() -> None:
        barrier.wait()
        reports.append(recover(database, now=clock()))

    threads = [threading.Thread(target=scan) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(report.lease_expired for report in reports) == 1
    assert sum(report.requeued for report in reports) == 1
    assert job(database, expired_id).status == JobStatus.RETRY_WAIT
    assert job(database, waiting_id).status == JobStatus.QUEUED
    # 提交时一条，回到 queued 时一条，不多不少。
    assert len(outbox_rows(database, waiting_id)) == 2


def test_a_queued_job_whose_message_was_lost_is_redispatched(database: Database, clock: FakeClock, alice: str):
    """E17：消息投递成功后在 Redis 里丢了（例如 Redis 重启），作业会一直停在 queued。"""
    job_id = submit(database, registry_with(), alice, now=clock()).job_id
    assert dispatch_outbox(database, lambda job_id: None, now=clock()) == 1

    clock.advance(timedelta(minutes=4, seconds=59))
    assert recover(database, now=clock()).redispatched == 0
    clock.advance(timedelta(seconds=1))
    assert recover(database, now=clock()).redispatched == 1
    assert recover(database, now=clock()).redispatched == 0

    assert [row.status for row in outbox_rows(database, job_id)] == ["sent", "pending"]
