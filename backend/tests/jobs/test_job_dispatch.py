"""outbox 分发与 Celery 装配（T07 决策 E16、E24）。"""

import time
from datetime import timedelta

from jobs_support import FakeClock, job, outbox_rows, registry_with, result_count, submit

from goalflow.contracts.enums import JobStatus
from goalflow.db.session import Database
from goalflow.jobs import dispatch_outbox
from goalflow.jobs.celery_app import create_celery_app, publisher_for


class _Recorder:
    def __init__(self) -> None:
        self.published: list[str] = []

    def __call__(self, job_id: str) -> None:
        self.published.append(job_id)


def _queue_down(job_id: str) -> None:
    raise ConnectionError("Redis 不可达")


def test_dispatch_publishes_due_rows_and_marks_them_sent(database: Database, clock: FakeClock, alice: str):
    job_id = submit(database, registry_with(), alice, now=clock()).job_id
    publish = _Recorder()

    assert dispatch_outbox(database, publish, now=clock()) == 1
    assert dispatch_outbox(database, publish, now=clock()) == 0

    assert publish.published == [job_id]
    [row] = outbox_rows(database, job_id)
    assert (row.status, row.attempts, row.sent_at) == ("sent", 1, clock())


def test_failed_publish_backs_off_and_never_raises(database: Database, clock: FakeClock, alice: str):
    """API 在业务事务提交之后投递：队列不可用不能让已经成功的请求报错（E16）。"""
    job_id = submit(database, registry_with(), alice, now=clock()).job_id

    assert dispatch_outbox(database, _queue_down, now=clock()) == 0
    [row] = outbox_rows(database, job_id)
    assert (row.status, row.attempts, row.next_attempt_at) == ("pending", 1, clock() + timedelta(seconds=10))

    publish = _Recorder()
    clock.advance(timedelta(seconds=9))
    assert dispatch_outbox(database, publish, now=clock()) == 0
    clock.advance(timedelta(seconds=1))
    assert dispatch_outbox(database, _queue_down, now=clock()) == 0
    [row] = outbox_rows(database, job_id)
    assert row.next_attempt_at == clock() + timedelta(seconds=20)

    clock.advance(timedelta(seconds=20))
    assert dispatch_outbox(database, publish, now=clock()) == 1
    assert publish.published == [job_id]


def test_dispatch_can_target_the_jobs_just_submitted(database: Database, clock: FakeClock, alice: str):
    registry = registry_with()
    mine = submit(database, registry, alice, now=clock(), dedupe_key="mine").job_id
    other = submit(database, registry, alice, now=clock(), dedupe_key="other").job_id
    publish = _Recorder()

    assert dispatch_outbox(database, publish, now=clock(), job_ids=[mine]) == 1

    assert publish.published == [mine]
    assert [row.status for row in outbox_rows(database, other)] == ["pending"]


def test_celery_worker_runs_a_dispatched_job(database: Database, clock: FakeClock, alice: str):
    """E24：用 kombu 的内存传输走一遍真实的 Celery 投递与执行，不依赖 Redis。"""
    from celery.contrib.testing.worker import start_worker

    registry = registry_with()
    app = create_celery_app(broker_url="memory://", database_factory=lambda: database, registry=registry, clock=clock)
    job_id = submit(database, registry, alice, now=clock()).job_id

    with start_worker(app, pool="solo", perform_ping_check=False, shutdown_timeout=30):
        assert dispatch_outbox(database, publisher_for(app), now=clock()) == 1
        deadline = time.monotonic() + 10
        while job(database, job_id).status != JobStatus.SUCCEEDED:
            assert time.monotonic() < deadline, job(database, job_id).status
            time.sleep(0.05)

    assert result_count(database) == 1


def test_beat_schedule_covers_dispatch_recovery_and_idempotency_cleanup():
    app = create_celery_app(broker_url="memory://")
    schedule = {entry["task"]: entry["schedule"] for entry in app.conf.beat_schedule.values()}

    assert schedule == {
        "goalflow.jobs.dispatch_outbox": 10.0,
        "goalflow.jobs.recover": 30.0,
        "goalflow.idempotency.purge_expired": 86400.0,
    }
    assert app.conf.task_acks_late is False
