"""作业接口（T07 PR-3，交接卡第 5 节"接口"）：查询、SSE 事件流、取消，以及用户隔离。"""

import json
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from jobs_support import PASSWORD, FakeClock, job, registry_with, run, submit

from goalflow.api.app import create_app
from goalflow.api.dependencies import get_auth_service
from goalflow.api.routes.jobs import get_job_stream_settings
from goalflow.auth.service import AuthService
from goalflow.contracts.enums import JobStatus
from goalflow.db.session import Database, get_database
from goalflow.jobs import JobRegistry, StreamSettings

_MISSING_JOB = "00000000-0000-0000-0000-000000000000"

_FAST_STREAM = StreamSettings(
    poll_interval=timedelta(milliseconds=10),
    heartbeat_interval=timedelta(seconds=5),
    max_duration=timedelta(seconds=5),
)


@dataclass
class Account:
    client: TestClient
    user_id: str


@pytest.fixture
def app(database: Database, auth_service: AuthService) -> FastAPI:
    app = create_app()
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    app.dependency_overrides[get_database] = lambda: database
    app.dependency_overrides[get_job_stream_settings] = lambda: _FAST_STREAM
    return app


def _sign_up(app: FastAPI, identifier: str) -> Account:
    client = TestClient(app, raise_server_exceptions=False)
    registered = client.post("/api/auth/register", json={"account_identifier": identifier, "password": PASSWORD})
    assert registered.status_code == 201, registered.text
    return Account(client=client, user_id=client.get("/api/auth/session").json()["user"]["id"])


@pytest.fixture
def alice_http(app: FastAPI) -> Iterator[Account]:
    account = _sign_up(app, "alice")
    with account.client:
        yield account


@pytest.fixture
def bob_http(app: FastAPI) -> Iterator[Account]:
    account = _sign_up(app, "bob")
    with account.client:
        yield account


@pytest.fixture
def registry() -> JobRegistry:
    return registry_with()


def _cancel(account: Account, job_id: str, expected_revision: int, key: str | None = "cancel-1") -> Response:
    headers = {"Idempotency-Key": key} if key is not None else {}
    return account.client.post(
        f"/api/jobs/{job_id}/cancellation", json={"expected_revision": expected_revision}, headers=headers
    )


def _messages(body: str) -> list[dict[str, str]]:
    """把 SSE 文本拆成消息。注释行（心跳）记为 {"comment": ...}。"""
    messages = []
    for block in body.split("\n\n"):
        if not block:
            continue
        fields: dict[str, str] = {}
        for line in block.split("\n"):
            name, _, value = line.partition(": ")
            fields["comment" if name == "" else name] = value
        messages.append(fields)
    return messages


def _events(body: str) -> list[dict[str, str]]:
    return [message for message in _messages(body) if "comment" not in message]


def _without_request_id(response: Response) -> dict[str, object]:
    return {key: value for key, value in response.json().items() if key != "request_id"}


def test_inspect_returns_the_current_state(
    alice_http: Account, database: Database, clock: FakeClock, registry: JobRegistry
):
    job_id = submit(database, registry, alice_http.user_id, now=clock()).job_id

    queued = alice_http.client.get(f"/api/jobs/{job_id}")
    run(database, job_id, registry, clock)
    finished = alice_http.client.get(f"/api/jobs/{job_id}")

    assert queued.status_code == 200
    assert {key: queued.json()[key] for key in ("status", "revision", "attempts", "result_refs", "error_code")} == {
        "status": "queued",
        "revision": 1,
        "attempts": 0,
        "result_refs": [],
        "error_code": None,
    }
    body = finished.json()
    assert body["status"] == "succeeded"
    assert [ref["type"] for ref in body["result_refs"]] == ["probe"]
    assert "owner_id" not in body and "input_refs" not in body


def test_other_users_jobs_look_exactly_like_missing_ones(
    alice_http: Account, bob_http: Account, database: Database, clock: FakeClock, registry: JobRegistry
):
    """E21、PRD Q01：改 URL 里的作业 ID 看不到别人的作业，也分辨不出它是否存在。"""
    job_id = submit(database, registry, alice_http.user_id, now=clock()).job_id
    bob = bob_http.client

    probes = {
        "inspect": (bob.get(f"/api/jobs/{job_id}"), bob.get(f"/api/jobs/{_MISSING_JOB}")),
        "watch": (bob.get(f"/api/jobs/{job_id}/events"), bob.get(f"/api/jobs/{_MISSING_JOB}/events")),
        "cancel": (_cancel(bob_http, job_id, 1, "k-a"), _cancel(bob_http, _MISSING_JOB, 1, "k-b")),
    }

    for name, (someone_elses, missing) in probes.items():
        assert someone_elses.status_code == missing.status_code == 404, name
        assert _without_request_id(someone_elses) == _without_request_id(missing), name
        assert job_id not in someone_elses.text, name
    assert job(database, job_id).status == JobStatus.QUEUED


def test_job_endpoints_require_a_session(app: FastAPI):
    anonymous = TestClient(app, raise_server_exceptions=False)
    cancel = anonymous.post(
        f"/api/jobs/{_MISSING_JOB}/cancellation", json={"expected_revision": 1}, headers={"Idempotency-Key": "k"}
    )

    assert anonymous.get(f"/api/jobs/{_MISSING_JOB}").status_code == 401
    assert anonymous.get(f"/api/jobs/{_MISSING_JOB}/events").status_code == 401
    assert cancel.status_code == 401


def test_cancel_requires_an_idempotency_key(
    alice_http: Account, database: Database, clock: FakeClock, registry: JobRegistry
):
    job_id = submit(database, registry, alice_http.user_id, now=clock()).job_id

    response = _cancel(alice_http, job_id, 1, key=None)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_FAILED"
    assert job(database, job_id).status == JobStatus.QUEUED


def test_cancel_is_idempotent_per_key(alice_http: Account, database: Database, clock: FakeClock, registry: JobRegistry):
    job_id = submit(database, registry, alice_http.user_id, now=clock()).job_id

    first = _cancel(alice_http, job_id, 1)
    replayed = _cancel(alice_http, job_id, 1)
    reused_for_other_content = _cancel(alice_http, job_id, 7)

    assert first.status_code == replayed.status_code == 200
    assert first.json()["status"] == "cancelled"
    assert replayed.json() == first.json()
    assert reused_for_other_content.status_code == 409
    assert reused_for_other_content.json()["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_cancel_with_an_outdated_revision_conflicts(
    alice_http: Account, database: Database, clock: FakeClock, registry: JobRegistry
):
    job_id = submit(database, registry, alice_http.user_id, now=clock()).job_id

    response = _cancel(alice_http, job_id, 5)

    assert response.status_code == 409
    assert response.json()["code"] == "REVISION_CONFLICT"
    assert response.json()["details"] == {"current_revision": 1}
    assert job(database, job_id).status == JobStatus.QUEUED


def test_cancelling_a_succeeded_job_returns_it_unchanged(
    alice_http: Account, database: Database, clock: FakeClock, registry: JobRegistry
):
    job_id = submit(database, registry, alice_http.user_id, now=clock()).job_id
    run(database, job_id, registry, clock)
    before = alice_http.client.get(f"/api/jobs/{job_id}").json()

    response = _cancel(alice_http, job_id, 1)

    assert response.status_code == 200
    assert response.json() == before


def test_event_stream_replays_a_finished_job_and_closes(
    alice_http: Account, database: Database, clock: FakeClock, registry: JobRegistry
):
    job_id = submit(database, registry, alice_http.user_id, now=clock()).job_id
    run(database, job_id, registry, clock)

    started = time.monotonic()
    response = alice_http.client.get(f"/api/jobs/{job_id}/events")

    # 发完终态事件就关闭，而不是等到时长上限（5 秒）才结束。
    assert time.monotonic() - started < _FAST_STREAM.max_duration.total_seconds() / 2
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    events = _events(response.text)
    assert [(event["id"], event["event"]) for event in events] == [
        ("1", "queued"),
        ("2", "started"),
        ("3", "completed"),
    ]
    completed = json.loads(events[-1]["data"])
    assert completed["sequence"] == 3
    assert completed["type"] == "completed"
    assert [ref["type"] for ref in completed["payload"]["result_refs"]] == ["probe"]


def test_event_stream_resumes_after_last_event_id(
    alice_http: Account, database: Database, clock: FakeClock, registry: JobRegistry
):
    job_id = submit(database, registry, alice_http.user_id, now=clock()).job_id
    run(database, job_id, registry, clock)

    resumed = alice_http.client.get(f"/api/jobs/{job_id}/events", headers={"Last-Event-ID": "2"})
    started = time.monotonic()
    after_the_end = alice_http.client.get(f"/api/jobs/{job_id}/events", headers={"Last-Event-ID": "3"})

    # 终态事件早已发过：按作业状态立即结束，不空等到时长上限。
    assert time.monotonic() - started < _FAST_STREAM.max_duration.total_seconds() / 2
    assert [event["event"] for event in _events(resumed.text)] == ["completed"]
    assert after_the_end.status_code == 200
    assert _events(after_the_end.text) == []


def test_event_stream_follows_a_running_job_until_it_finishes(
    alice_http: Account, database: Database, clock: FakeClock, registry: JobRegistry
):
    job_id = submit(database, registry, alice_http.user_id, now=clock()).job_id

    def finish_later() -> None:
        time.sleep(0.2)
        run(database, job_id, registry, clock)

    worker = threading.Thread(target=finish_later)
    worker.start()
    started = time.monotonic()
    response = alice_http.client.get(f"/api/jobs/{job_id}/events")
    worker.join()

    assert time.monotonic() - started < _FAST_STREAM.max_duration.total_seconds() / 2
    assert [event["event"] for event in _events(response.text)] == ["queued", "started", "completed"]


def test_idle_stream_sends_heartbeats_and_ends_without_cancelling_the_job(
    app: FastAPI, alice_http: Account, database: Database, clock: FakeClock, registry: JobRegistry
):
    """E20：连接到时长上限就关闭，客户端重连即可；连接结束不影响作业（04 第 7 节）。"""
    app.dependency_overrides[get_job_stream_settings] = lambda: StreamSettings(
        poll_interval=timedelta(milliseconds=10),
        heartbeat_interval=timedelta(milliseconds=20),
        max_duration=timedelta(milliseconds=200),
    )
    job_id = submit(database, registry, alice_http.user_id, now=clock()).job_id

    response = alice_http.client.get(f"/api/jobs/{job_id}/events")

    assert {"comment": "keep-alive"} in _messages(response.text)
    assert [event["event"] for event in _events(response.text)] == ["queued"]
    assert job(database, job_id).status == JobStatus.QUEUED
    assert run(database, job_id, registry, clock) is JobStatus.SUCCEEDED
