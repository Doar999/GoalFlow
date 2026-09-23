"""按交接卡第 9 节的接入方式，从 HTTP 一路走到幂等存储。

这里的探针路由就是后续写接口（T04 起）的样板：身份来自 CurrentUserDep，key 来自
require_idempotency_key，摘要基于校验后的请求模型，业务写入与幂等记录同一个写事务。
"""

from collections.abc import Iterator
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from idempotency_support import PASSWORD, FakeClock, insert_probe_effect, probe_effect_count
from pydantic import BaseModel

from goalflow.api.app import create_app
from goalflow.api.dependencies import CurrentUserDep, get_auth_service, require_idempotency_key
from goalflow.auth.service import AuthService
from goalflow.db.session import Database
from goalflow.idempotency import IdempotentRequest, ResultRef, run_idempotent

_PATH = "/api/_probe/goals/{goal_id}/effects"


class ProbeBody(BaseModel):
    title: str
    minutes: int = 30


def _app(database: Database, auth_service: AuthService, clock: FakeClock) -> FastAPI:
    app = create_app()
    app.dependency_overrides[get_auth_service] = lambda: auth_service

    @app.post(_PATH)
    def create_effect(
        goal_id: str,
        body: ProbeBody,
        user: CurrentUserDep,
        key: Annotated[str, Depends(require_idempotency_key)],
    ) -> JSONResponse:
        request = IdempotentRequest.build(
            owner_id=user.user_id,
            operation="create_probe_effect",
            key=key,
            body=body,
            path_params={"goal_id": goal_id},
        )
        with database.write() as session:

            def execute() -> tuple[ResultRef, int]:
                return insert_probe_effect(session, user.user_id, body.title), 201

            outcome = run_idempotent(session, request, execute, now=clock())
        return JSONResponse({"id": outcome.result.id}, status_code=outcome.status_code)

    return app


def _signed_in(app: FastAPI, identifier: str) -> TestClient:
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/api/auth/register", json={"account_identifier": identifier, "password": PASSWORD})
    assert response.status_code == 201, response.text
    return client


@pytest.fixture
def app(database: Database, auth_service: AuthService, clock: FakeClock) -> FastAPI:
    return _app(database, auth_service, clock)


@pytest.fixture
def alice_client(app: FastAPI) -> Iterator[TestClient]:
    with _signed_in(app, "alice") as client:
        yield client


def _post(client: TestClient, raw_json: str, key: str = "key-1", goal_id: str = "g1"):
    return client.post(
        _PATH.format(goal_id=goal_id),
        content=raw_json.encode("utf-8"),
        headers={"Content-Type": "application/json", "Idempotency-Key": key},
    )


def test_retry_with_reformatted_body_replays_the_original_result(alice_client: TestClient, database: Database):
    first = _post(alice_client, '{"title":"学英语","minutes":30}')
    retried = _post(alice_client, '{ "minutes": 30,\n  "title": "学英语" }')
    omitted_default = _post(alice_client, '{"title": "学英语"}')

    assert first.status_code == retried.status_code == omitted_default.status_code == 201
    assert retried.json() == first.json() == omitted_default.json()
    assert probe_effect_count(database) == 1


def test_reused_key_with_different_content_returns_the_error_contract(alice_client: TestClient, database: Database):
    _post(alice_client, '{"title":"学英语"}')

    changed_body = _post(alice_client, '{"title":"学日语"}')
    changed_path = _post(alice_client, '{"title":"学英语"}', goal_id="g2")

    for response in (changed_body, changed_path):
        assert response.status_code == 409
        body = response.json()
        assert body["code"] == "IDEMPOTENCY_KEY_CONFLICT"
        assert body["retryable"] is False
        assert body["details"] == {}
    assert probe_effect_count(database) == 1


def test_another_user_with_the_same_key_gets_their_own_result(
    app: FastAPI, alice_client: TestClient, database: Database
):
    alices = _post(alice_client, '{"title":"学英语"}')
    with _signed_in(app, "bob") as bob_client:
        bobs = _post(bob_client, '{"title":"学英语"}')

    assert bobs.status_code == 201
    assert bobs.json() != alices.json()
    assert probe_effect_count(database) == 2


def test_missing_key_is_rejected_before_any_write(alice_client: TestClient, database: Database):
    response = alice_client.post(_PATH.format(goal_id="g1"), json={"title": "学英语"})

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_FAILED"
    assert probe_effect_count(database) == 0
