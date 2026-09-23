"""领取、提交、重试、取消与过期（T07 决策 E12—E15、E19，交接卡第 5 节"作业"）。

"业务结果提交了几次"看 job_results 的行数；"作业最后是什么状态"看 jobs 表。两者必须一致：
作业 succeeded 当且仅当它的业务结果恰好落库一次。
"""

import threading
import time
from collections.abc import Callable
from datetime import timedelta

import pytest
from jobs_support import (
    FakeClock,
    event_sequences,
    event_types,
    job,
    registry_with,
    result_count,
    run,
    submit,
    succeeding,
    write_result,
)
from sqlalchemy import update
from sqlalchemy.orm import Session

from goalflow.contracts.enums import JobStatus
from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.db.session import Database
from goalflow.jobs import (
    InputStale,
    JobCommit,
    JobContext,
    JobOutcome,
    JobRegistry,
    RetryableJobError,
    cancel_job,
    recover,
)
from goalflow.jobs.models import Job


def _wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("等待超时")
        time.sleep(0.01)


def test_successful_attempt_commits_once_and_publishes_events(database: Database, clock: FakeClock, alice: str):
    registry = registry_with()
    job_id = submit(database, registry, alice, now=clock()).job_id

    assert run(database, job_id, registry, clock) is JobStatus.SUCCEEDED

    finished = job(database, job_id)
    assert finished.status == JobStatus.SUCCEEDED
    assert finished.attempts == 1
    assert finished.lease_token is None
    assert finished.result_refs_json is not None and '"type":"probe"' in finished.result_refs_json
    assert result_count(database) == 1
    assert event_types(database, job_id) == ["queued", "started", "completed"]
    assert event_sequences(database, job_id) == [1, 2, 3]


def test_duplicate_delivery_is_claimed_only_once(database: Database, clock: FakeClock, alice: str):
    registry = registry_with()
    job_id = submit(database, registry, alice, now=clock()).job_id

    assert run(database, job_id, registry, clock) is JobStatus.SUCCEEDED
    assert run(database, job_id, registry, clock) is None
    assert result_count(database) == 1


def test_two_workers_racing_for_one_job(database: Database, clock: FakeClock, alice: str):
    def slow(context: JobContext) -> JobCommit:
        time.sleep(0.05)
        return succeeding(context)

    registry = registry_with(slow)
    job_id = submit(database, registry, alice, now=clock()).job_id
    barrier = threading.Barrier(2)
    outcomes: list[JobStatus | None] = []

    def worker() -> None:
        barrier.wait()
        outcomes.append(run(database, job_id, registry, clock))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count(JobStatus.SUCCEEDED) == 1
    assert outcomes.count(None) == 1
    assert result_count(database) == 1


class _SlowFirstAttempt:
    """第一次尝试卡住，直到测试放行；之后的尝试立即完成。"""

    def __init__(self) -> None:
        self.claimed = threading.Event()
        self.release = threading.Event()

    def __call__(self, context: JobContext) -> JobCommit:
        if context.job.attempts == 1:
            self.claimed.set()
            assert self.release.wait(10)
        return succeeding(context)


def _take_over_after_lease_expiry(database: Database, clock: FakeClock, registry: JobRegistry, job_id: str) -> None:
    """模拟旧 Worker 失联：租约过期 → 恢复扫描回收 → 退避到期 → 新 Worker 完成。"""
    clock.advance(timedelta(seconds=61))
    assert recover(database, now=clock()).lease_expired == 1
    clock.advance(timedelta(seconds=30))
    assert recover(database, now=clock()).requeued == 1
    assert run(database, job_id, registry, clock) is JobStatus.SUCCEEDED


def test_old_worker_returning_late_cannot_commit(database: Database, clock: FakeClock, alice: str):
    handler = _SlowFirstAttempt()
    registry = registry_with(handler)
    job_id = submit(database, registry, alice, now=clock()).job_id
    late: list[JobStatus | None] = []
    old_worker = threading.Thread(target=lambda: late.append(run(database, job_id, registry, clock)))
    old_worker.start()
    assert handler.claimed.wait(5)

    _take_over_after_lease_expiry(database, clock, registry, job_id)
    handler.release.set()
    old_worker.join()

    assert late == [None]
    assert job(database, job_id).status == JobStatus.SUCCEEDED
    assert result_count(database) == 1


def test_commit_guard_alone_stops_an_old_worker(
    database: Database, clock: FakeClock, alice: str, monkeypatch: pytest.MonkeyPatch
):
    """即使检查点漏掉了租约丢失，提交时的条件更新也必须挡住旧 Worker（E14）。"""
    monkeypatch.setattr(JobContext, "checkpoint", lambda self: None)
    handler = _SlowFirstAttempt()
    registry = registry_with(handler)
    job_id = submit(database, registry, alice, now=clock()).job_id
    late: list[JobStatus | None] = []
    old_worker = threading.Thread(target=lambda: late.append(run(database, job_id, registry, clock)))
    old_worker.start()
    assert handler.claimed.wait(5)

    _take_over_after_lease_expiry(database, clock, registry, job_id)
    handler.release.set()
    old_worker.join()

    assert late == [None]
    assert result_count(database) == 1


def test_old_worker_cannot_commit_while_a_newer_attempt_is_running(
    database: Database, clock: FakeClock, alice: str, monkeypatch: pytest.MonkeyPatch
):
    """作业又回到了 running（新的尝试），状态条件挡不住旧 Worker，只有租约令牌能挡（E14）。"""
    monkeypatch.setattr(JobContext, "checkpoint", lambda self: None)
    claimed = {1: threading.Event(), 2: threading.Event()}
    release = {1: threading.Event(), 2: threading.Event()}

    def attempt_gated(context: JobContext) -> JobCommit:
        claimed[context.job.attempts].set()
        assert release[context.job.attempts].wait(10)
        return succeeding(context)

    registry = registry_with(attempt_gated)
    job_id = submit(database, registry, alice, now=clock()).job_id
    outcomes: dict[int, JobStatus | None] = {}
    old_worker = threading.Thread(target=lambda: outcomes.update({1: run(database, job_id, registry, clock)}))
    old_worker.start()
    assert claimed[1].wait(5)
    clock.advance(timedelta(seconds=61))
    recover(database, now=clock())
    clock.advance(timedelta(seconds=30))
    recover(database, now=clock())
    new_worker = threading.Thread(target=lambda: outcomes.update({2: run(database, job_id, registry, clock)}))
    new_worker.start()
    assert claimed[2].wait(5)

    release[1].set()
    old_worker.join()
    release[2].set()
    new_worker.join()

    assert outcomes == {1: None, 2: JobStatus.SUCCEEDED}
    assert result_count(database) == 1


def test_retryable_errors_back_off_then_fail_after_three_attempts(database: Database, clock: FakeClock, alice: str):
    def unavailable(context: JobContext) -> JobCommit:
        raise RetryableJobError(ErrorCode.MODEL_UNAVAILABLE, "模型服务暂时不可用")

    registry = registry_with(unavailable)
    job_id = submit(database, registry, alice, now=clock()).job_id

    assert run(database, job_id, registry, clock) is JobStatus.RETRY_WAIT
    assert job(database, job_id).next_attempt_at == clock() + timedelta(seconds=30)

    clock.advance(timedelta(seconds=29))
    assert recover(database, now=clock()).requeued == 0
    clock.advance(timedelta(seconds=1))
    assert recover(database, now=clock()).requeued == 1

    assert run(database, job_id, registry, clock) is JobStatus.RETRY_WAIT
    assert job(database, job_id).next_attempt_at == clock() + timedelta(seconds=120)
    clock.advance(timedelta(seconds=120))
    recover(database, now=clock())

    assert run(database, job_id, registry, clock) is JobStatus.FAILED
    failed = job(database, job_id)
    assert failed.attempts == 3
    assert failed.error_code == ErrorCode.MODEL_UNAVAILABLE
    assert failed.error_message == "模型服务暂时不可用"
    assert event_types(database, job_id) == [
        "queued",
        "started",
        "retrying",
        "queued",
        "started",
        "retrying",
        "queued",
        "started",
        "failed",
    ]
    assert event_sequences(database, job_id) == list(range(1, 10))


def test_non_retryable_business_error_fails_immediately(database: Database, clock: FakeClock, alice: str):
    def over_budget(context: JobContext) -> JobCommit:
        raise GoalflowError(ErrorCode.BUDGET_CONFLICT, "本周剩余时间不足")

    registry = registry_with(over_budget)
    job_id = submit(database, registry, alice, now=clock()).job_id

    assert run(database, job_id, registry, clock) is JobStatus.FAILED
    failed = job(database, job_id)
    assert (failed.attempts, failed.error_code, failed.error_message) == (1, "BUDGET_CONFLICT", "本周剩余时间不足")


def test_unexpected_errors_fail_without_leaking_the_exception_text(database: Database, clock: FakeClock, alice: str):
    def broken(context: JobContext) -> JobCommit:
        raise RuntimeError("私人目标内容，不应出现在作业记录里")

    registry = registry_with(broken)
    job_id = submit(database, registry, alice, now=clock()).job_id

    assert run(database, job_id, registry, clock) is JobStatus.FAILED
    failed = job(database, job_id)
    assert failed.error_code == ErrorCode.INTERNAL_ERROR
    assert failed.error_message is not None and "私人目标" not in failed.error_message


def test_stale_input_discovered_at_commit_writes_nothing(database: Database, clock: FakeClock, alice: str):
    def stale_by_commit_time(context: JobContext) -> JobCommit:
        def commit(session: Session) -> JobOutcome:
            write_result(session, context)
            raise InputStale

        return commit

    registry = registry_with(stale_by_commit_time)
    job_id = submit(database, registry, alice, now=clock(), input_revision=3).job_id

    assert run(database, job_id, registry, clock) is JobStatus.STALE
    assert job(database, job_id).error_code == ErrorCode.INPUT_STALE
    assert result_count(database) == 0
    assert event_types(database, job_id)[-1] == "stale"


def test_failure_inside_commit_rolls_back_the_business_write(database: Database, clock: FakeClock, alice: str):
    def half_written(context: JobContext) -> JobCommit:
        def commit(session: Session) -> JobOutcome:
            write_result(session, context)
            raise RuntimeError("第二张表写入失败")

        return commit

    registry = registry_with(half_written)
    job_id = submit(database, registry, alice, now=clock()).job_id

    assert run(database, job_id, registry, clock) is JobStatus.FAILED
    assert result_count(database) == 0


def test_cancelling_a_queued_job_prevents_it_from_running(database: Database, clock: FakeClock, alice: str):
    registry = registry_with()
    job_id = submit(database, registry, alice, now=clock()).job_id

    cancelled = cancel_job(database, owner_id=alice, job_id=job_id, expected_revision=1, now=clock())

    assert cancelled.status is JobStatus.CANCELLED
    assert run(database, job_id, registry, clock) is None
    assert result_count(database) == 0
    assert event_types(database, job_id) == ["queued", "cancelled"]


def test_cancel_requested_while_running_is_honoured_at_the_next_checkpoint(
    database: Database, clock: FakeClock, alice: str
):
    def cancelled_midway(context: JobContext) -> JobCommit:
        view = cancel_job(
            database, owner_id=context.job.owner_id, job_id=context.job.id, expected_revision=2, now=clock()
        )
        assert view.status is JobStatus.RUNNING and view.cancel_requested
        context.checkpoint()
        raise AssertionError("检查点应当已经中止")

    registry = registry_with(cancelled_midway)
    job_id = submit(database, registry, alice, now=clock()).job_id

    assert run(database, job_id, registry, clock) is JobStatus.CANCELLED
    assert result_count(database) == 0
    assert event_types(database, job_id)[-1] == "cancelled"


def test_cancel_after_the_last_checkpoint_still_blocks_the_commit(
    database: Database, clock: FakeClock, alice: str, monkeypatch: pytest.MonkeyPatch
):
    """取消与成功提交用条件更新竞争：取消先生效，就不能再发布结果（E14、E15）。"""
    monkeypatch.setattr(JobContext, "checkpoint", lambda self: None)

    def cancelled_before_commit(context: JobContext) -> JobCommit:
        cancel_job(database, owner_id=context.job.owner_id, job_id=context.job.id, expected_revision=2, now=clock())
        return succeeding(context)

    registry = registry_with(cancelled_before_commit)
    job_id = submit(database, registry, alice, now=clock()).job_id

    assert run(database, job_id, registry, clock) is JobStatus.CANCELLED
    assert result_count(database) == 0


def test_cancel_racing_the_commit_has_exactly_one_winner(database: Database, clock: FakeClock, alice: str):
    started = threading.Event()

    def quick(context: JobContext) -> JobCommit:
        started.set()
        return succeeding(context)

    registry = registry_with(quick)
    succeeded = 0
    for round_number in range(15):
        started.clear()
        job_id = submit(database, registry, alice, now=clock(), dedupe_key=f"race-{round_number}").job_id
        worker = threading.Thread(target=run, args=(database, job_id, registry, clock))
        worker.start()
        assert started.wait(5)
        cancel_job(database, owner_id=alice, job_id=job_id, expected_revision=2, now=clock())
        worker.join()

        final = job(database, job_id)
        assert final.status in (JobStatus.SUCCEEDED, JobStatus.CANCELLED)
        if final.status == JobStatus.SUCCEEDED:
            succeeded += 1
            assert final.result_refs_json not in (None, "[]")
        else:
            assert final.result_refs_json is None

    assert result_count(database) == succeeded


def test_cancelling_a_finished_job_keeps_its_result(database: Database, clock: FakeClock, alice: str):
    registry = registry_with()
    job_id = submit(database, registry, alice, now=clock()).job_id
    run(database, job_id, registry, clock)
    finished = job(database, job_id)

    view = cancel_job(database, owner_id=alice, job_id=job_id, expected_revision=1, now=clock())

    assert view.status is JobStatus.SUCCEEDED
    assert view.revision == finished.revision
    assert result_count(database) == 1


def test_cancel_with_an_outdated_revision_conflicts(database: Database, clock: FakeClock, alice: str):
    def checks_conflict(context: JobContext) -> JobCommit:
        with pytest.raises(GoalflowError) as raised:
            cancel_job(database, owner_id=alice, job_id=context.job.id, expected_revision=1, now=clock())
        assert raised.value.code is ErrorCode.REVISION_CONFLICT
        assert raised.value.details == {"current_revision": 2}
        return succeeding(context)

    registry = registry_with(checks_conflict)
    job_id = submit(database, registry, alice, now=clock()).job_id

    assert run(database, job_id, registry, clock) is JobStatus.SUCCEEDED


def test_stage_and_confirmation_events_are_published_in_order(database: Database, clock: FakeClock, alice: str):
    def staged(context: JobContext) -> JobCommit:
        context.stage_completed("routes_drafted")

        def commit(session: Session) -> JobOutcome:
            outcome = write_result(session, context)
            return JobOutcome(result_refs=outcome.result_refs, awaiting_confirmation=True)

        return commit

    registry = registry_with(staged)
    job_id = submit(database, registry, alice, now=clock()).job_id

    run(database, job_id, registry, clock)

    assert event_types(database, job_id) == [
        "queued",
        "started",
        "stage_completed",
        "awaiting_confirmation",
        "completed",
    ]


def test_heartbeat_extends_the_lease_while_the_handler_runs(database: Database, clock: FakeClock, alice: str):
    def long_running(context: JobContext) -> JobCommit:
        clock.advance(timedelta(seconds=45))
        _wait_until(lambda: job(database, context.job.id).lease_until == clock() + timedelta(seconds=60))
        return succeeding(context)

    registry = registry_with(long_running)
    job_id = submit(database, registry, alice, now=clock()).job_id

    outcome = run(database, job_id, registry, clock, heartbeat_interval=timedelta(milliseconds=10))

    assert outcome is JobStatus.SUCCEEDED


def test_heartbeat_notices_a_lost_lease_and_the_attempt_is_abandoned(database: Database, clock: FakeClock, alice: str):
    def loses_lease(context: JobContext) -> JobCommit:
        with database.write() as session:
            session.execute(update(Job).where(Job.id == context.job.id).values(lease_token="someone-else"))
        _wait_until(lambda: context.lease_lost)
        return succeeding(context)

    registry = registry_with(loses_lease)
    job_id = submit(database, registry, alice, now=clock()).job_id

    assert run(database, job_id, registry, clock, heartbeat_interval=timedelta(milliseconds=10)) is None
    assert result_count(database) == 0


def test_a_worker_without_the_handler_fails_the_job(database: Database, clock: FakeClock, alice: str):
    job_id = submit(database, registry_with(), alice, now=clock()).job_id

    assert run(database, job_id, JobRegistry(), clock) is JobStatus.FAILED
    assert job(database, job_id).error_code == ErrorCode.INTERNAL_ERROR
