"""幂等存储的外部可观察行为（T07 决策 E4—E8，交接卡第 5 节"幂等"）。

判断业务是否重复执行，看的是 probe_effects 的行数，而不是 run_idempotent 的返回值——
返回值说"重放了"而业务其实又写了一遍，正是要防的那种 bug。
"""

import threading
import time
from datetime import timedelta

import pytest
from idempotency_support import FakeClock, insert_probe_effect, probe_effect_count, record_count, submit
from pydantic import BaseModel

from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.db.session import Database
from goalflow.idempotency import (
    RETENTION,
    IdempotentOutcome,
    IdempotentRequest,
    ResultRef,
    compute_request_hash,
    purge_expired,
    run_idempotent,
)


class Payload(BaseModel):
    title: str
    minutes: int = 30


def _request(
    owner_id: str, *, key: str = "key-1", operation: str = "create_probe", **body: object
) -> IdempotentRequest:
    payload = Payload.model_validate({"title": "学英语", **body})
    return IdempotentRequest.build(owner_id=owner_id, operation=operation, key=key, body=payload)


def test_same_key_and_content_executes_once_and_replays(database: Database, clock: FakeClock, alice: str):
    first = submit(database, _request(alice), now=clock())
    second = submit(database, _request(alice), now=clock())

    assert first.replayed is False
    assert second.replayed is True
    assert second.result == first.result
    assert second.status_code == first.status_code == 201
    assert probe_effect_count(database) == 1


def test_same_key_with_different_content_conflicts(database: Database, clock: FakeClock, alice: str):
    submit(database, _request(alice), now=clock())

    with pytest.raises(GoalflowError) as raised:
        submit(database, _request(alice, title="学日语"), now=clock())

    assert raised.value.code is ErrorCode.IDEMPOTENCY_KEY_CONFLICT
    assert raised.value.details == {}
    assert probe_effect_count(database) == 1


def test_same_key_on_another_operation_conflicts(database: Database, clock: FakeClock, alice: str):
    """决策 E4：唯一约束不含 operation，把 key 误用在另一个操作上要被发现。"""
    submit(database, _request(alice), now=clock())

    with pytest.raises(GoalflowError) as raised:
        submit(database, _request(alice, operation="update_probe"), now=clock())

    assert raised.value.code is ErrorCode.IDEMPOTENCY_KEY_CONFLICT
    assert probe_effect_count(database) == 1


def test_concurrent_submissions_with_one_key_execute_once(database: Database, clock: FakeClock, alice: str):
    """决策 E6：并发请求在 BEGIN IMMEDIATE 的库级写锁上串行，后到的看到记录并重放。"""
    contenders = 8
    barrier = threading.Barrier(contenders)
    outcomes: list[IdempotentOutcome] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    request = _request(alice)

    def attempt() -> None:
        barrier.wait()
        try:
            with database.write() as session:

                def execute() -> tuple[ResultRef, int]:
                    result = insert_probe_effect(session, alice, "effect")
                    # 拉长持锁时间，让其余线程确实在锁上排队，而不是恰好错开。
                    time.sleep(0.05)
                    return result, 201

                outcome = run_idempotent(session, request, execute, now=clock())
            with lock:
                outcomes.append(outcome)
        except BaseException as error:
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt) for _ in range(contenders)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert [outcome.replayed for outcome in outcomes].count(False) == 1
    assert {outcome.result for outcome in outcomes} == {outcomes[0].result}
    assert probe_effect_count(database) == 1
    assert record_count(database) == 1


def test_failed_business_write_leaves_no_record_and_the_key_can_be_retried(
    database: Database, clock: FakeClock, alice: str
):
    with pytest.raises(GoalflowError):
        submit(database, _request(alice), now=clock(), fail=True)

    assert record_count(database) == 0
    assert probe_effect_count(database) == 0

    retried = submit(database, _request(alice), now=clock())

    assert retried.replayed is False
    assert probe_effect_count(database) == 1


def test_keys_are_scoped_per_owner(database: Database, clock: FakeClock, alice: str, bob: str):
    alices = submit(database, _request(alice), now=clock())
    bobs = submit(database, _request(bob), now=clock())

    assert bobs.replayed is False
    assert bobs.result != alices.result
    assert probe_effect_count(database) == 2


def test_record_deduplicates_until_the_retention_period_ends(database: Database, clock: FakeClock, alice: str):
    """决策 E8：过期判断不依赖清理任务有没有跑过。"""
    first = submit(database, _request(alice), now=clock())

    clock.advance(RETENTION - timedelta(microseconds=1))
    assert submit(database, _request(alice), now=clock()).replayed is True

    clock.advance(timedelta(microseconds=1))
    renewed = submit(database, _request(alice, title="过期后换了内容也照常执行"), now=clock())

    assert renewed.replayed is False
    assert renewed.result != first.result
    assert probe_effect_count(database) == 2
    assert record_count(database) == 1


def test_purge_removes_only_expired_records(database: Database, clock: FakeClock, alice: str):
    submit(database, _request(alice, key="old"), now=clock())
    clock.advance(timedelta(days=1))
    submit(database, _request(alice, key="recent"), now=clock())

    clock.advance(RETENTION - timedelta(days=1))

    assert purge_expired(database, now=clock()) == 1
    assert record_count(database) == 1
    assert submit(database, _request(alice, key="recent"), now=clock()).replayed is True


def test_only_successful_responses_are_recorded(database: Database, clock: FakeClock, alice: str):
    with pytest.raises(ValueError, match="状态码"):
        submit(database, _request(alice), now=clock(), status_code=409)

    assert record_count(database) == 0
    assert probe_effect_count(database) == 0


def test_request_hash_ignores_key_order_and_omitted_defaults():
    """决策 E5：摘要基于校验后的模型，而不是原始请求字节。"""
    reordered = Payload.model_validate({"minutes": 30, "title": "学英语"})
    omitted_default = Payload.model_validate({"title": "学英语"})

    assert compute_request_hash("create_probe", body=reordered) == compute_request_hash(
        "create_probe", body=omitted_default
    )


def test_request_hash_covers_operation_path_and_body():
    body = Payload(title="学英语")
    baseline = compute_request_hash("create_probe", body=body, path_params={"goal_id": "g1"})

    assert compute_request_hash("update_probe", body=body, path_params={"goal_id": "g1"}) != baseline
    assert compute_request_hash("create_probe", body=body, path_params={"goal_id": "g2"}) != baseline
    assert compute_request_hash("create_probe", body=Payload(title="学日语"), path_params={"goal_id": "g1"}) != baseline
