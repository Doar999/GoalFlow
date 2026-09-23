"""提交与去重（T07 决策 E10、E16、E18，交接卡第 5 节"作业"前两条）。"""

import threading

import pytest
from jobs_support import (
    KIND,
    FakeClock,
    count,
    event_types,
    job,
    outbox_rows,
    registry_with,
    submit,
)
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from goalflow.contracts.enums import JobStatus
from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.db.session import Database
from goalflow.jobs import JobRegistry, SubmittedJob, cancel_job, get_job, submit_job
from goalflow.jobs.models import Job, JobEvent, OutboxEvent


def test_submission_writes_job_outbox_and_queued_event_together(database: Database, clock: FakeClock, alice: str):
    submitted = submit(database, registry_with(), alice, now=clock())

    assert submitted.created is True
    assert job(database, submitted.job_id).status == JobStatus.QUEUED
    assert [row.status for row in outbox_rows(database, submitted.job_id)] == ["pending"]
    assert event_types(database, submitted.job_id) == ["queued"]


def test_rolled_back_business_transaction_leaves_no_job(database: Database, clock: FakeClock, alice: str):
    """E16：作业、outbox 与业务写入同生共死。"""

    class BusinessRuleRejectedError(Exception):
        pass

    with pytest.raises(BusinessRuleRejectedError), database.write() as session:
        submit_job(
            session,
            owner_id=alice,
            kind=KIND,
            dedupe_key="input-1",
            input_refs={},
            now=clock(),
            registry=registry_with(),
        )
        raise BusinessRuleRejectedError

    assert count(database, Job) == 0
    assert count(database, OutboxEvent) == 0
    assert count(database, JobEvent) == 0


def test_resubmitting_the_same_input_returns_the_original_job(database: Database, clock: FakeClock, alice: str):
    registry = registry_with()
    first = submit(database, registry, alice, now=clock())
    again = submit(database, registry, alice, now=clock())
    other_input = submit(database, registry, alice, now=clock(), dedupe_key="input-2")

    assert again == SubmittedJob(job_id=first.job_id, created=False)
    assert other_input.created is True
    assert count(database, Job) == 2
    assert count(database, OutboxEvent) == 2


def test_concurrent_submissions_of_one_input_create_one_job(database: Database, clock: FakeClock, alice: str):
    registry = registry_with()
    contenders = 8
    barrier = threading.Barrier(contenders)
    results: list[SubmittedJob] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def attempt() -> None:
        barrier.wait()
        try:
            submitted = submit(database, registry, alice, now=clock())
            with lock:
                results.append(submitted)
        except BaseException as error:
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt) for _ in range(contenders)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert [result.created for result in results].count(True) == 1
    assert {result.job_id for result in results} == {results[0].job_id}
    assert count(database, Job) == 1


@pytest.mark.parametrize("finished_as", [JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.STALE])
def test_a_finished_unsuccessful_job_frees_its_dedupe_key(
    database: Database, clock: FakeClock, alice: str, finished_as: JobStatus
):
    """E10：failed、cancelled、stale 不占去重键，同一输入可以重新提交。"""
    registry = registry_with()
    first = submit(database, registry, alice, now=clock())
    with database.write() as session:
        session.execute(update(Job).where(Job.id == first.job_id).values(status=finished_as))

    retried = submit(database, registry, alice, now=clock())

    assert retried.created is True
    assert retried.job_id != first.job_id


def test_a_succeeded_job_keeps_its_dedupe_key(database: Database, clock: FakeClock, alice: str):
    """相同输入只对应一个有效作业（04 第 8 节）：成功之后再提交，拿到的还是它。"""
    registry = registry_with()
    first = submit(database, registry, alice, now=clock())
    with database.write() as session:
        session.execute(update(Job).where(Job.id == first.job_id).values(status=JobStatus.SUCCEEDED))

    assert submit(database, registry, alice, now=clock()).job_id == first.job_id


def test_the_partial_unique_index_backs_up_the_dedupe_check(database: Database, clock: FakeClock, alice: str):
    """绕过 submit_job 的查询直接插第二行，库级约束也要拦住。"""
    first = submit(database, registry_with(), alice, now=clock())
    original = job(database, first.job_id)

    with pytest.raises(IntegrityError), database.write() as session:
        session.add(
            Job(
                id="00000000-0000-0000-0000-000000000001",
                owner_id=alice,
                kind=KIND,
                dedupe_key=original.dedupe_key,
                input_refs_json="{}",
                status=JobStatus.QUEUED,
                attempts=0,
                revision=1,
                created_at=clock(),
                updated_at=clock(),
            )
        )


def test_dedupe_keys_are_scoped_per_owner(database: Database, clock: FakeClock, alice: str, bob: str):
    registry = registry_with()
    alices = submit(database, registry, alice, now=clock())
    bobs = submit(database, registry, bob, now=clock())

    assert bobs.created is True
    assert bobs.job_id != alices.job_id


def test_unregistered_kind_is_rejected_at_submission(database: Database, clock: FakeClock, alice: str):
    with pytest.raises(ValueError, match="未注册"), database.write() as session:
        submit_job(
            session,
            owner_id=alice,
            kind="nobody-handles-this",
            dedupe_key="input-1",
            input_refs={},
            now=clock(),
            registry=JobRegistry(),
        )
    assert count(database, Job) == 0


def test_other_users_cannot_see_or_cancel_a_job(database: Database, clock: FakeClock, alice: str, bob: str):
    """E21：他人的作业与不存在的作业返回同一个错误。"""
    submitted = submit(database, registry_with(), alice, now=clock())

    for attempt in (
        lambda: get_job(database, owner_id=bob, job_id=submitted.job_id),
        lambda: cancel_job(database, owner_id=bob, job_id=submitted.job_id, expected_revision=1, now=clock()),
        lambda: get_job(database, owner_id=alice, job_id="00000000-0000-0000-0000-000000000000"),
    ):
        with pytest.raises(GoalflowError) as raised:
            attempt()
        assert raised.value.code is ErrorCode.NOT_FOUND
        assert raised.value.details == {}

    assert job(database, submitted.job_id).status == JobStatus.QUEUED
