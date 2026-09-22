"""全局异常处理：任何异常都转成统一错误结构，且不泄露内部细节。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from goalflow.api.app import create_app
from goalflow.contracts.errors import ErrorCode, GoalflowError

ENVELOPE_FIELDS = {"code", "message", "request_id", "retryable", "details"}

SECRET_LIKE_INPUT = "sk-should-never-be-echoed"


class _Payload(BaseModel):
    amount: int


def _app_with_probes() -> FastAPI:
    app = create_app()

    @app.post("/api/_probe/echo")
    def _echo(payload: _Payload) -> dict[str, int]:
        return {"amount": payload.amount}

    @app.get("/api/_probe/boom")
    def _boom() -> None:
        raise RuntimeError("内部细节不应外泄")

    @app.get("/api/_probe/conflict")
    def _conflict() -> None:
        raise GoalflowError(ErrorCode.REVISION_CONFLICT, "计划版本已过期", details={"current_revision": 7})

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app_with_probes(), raise_server_exceptions=False)


def test_unknown_path_returns_envelope(client):
    response = client.get("/api/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert set(body) == ENVELOPE_FIELDS
    assert body["code"] == "NOT_FOUND"
    assert body["retryable"] is False


def test_business_error_keeps_code_status_and_details(client):
    response = client.get("/api/_probe/conflict")

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "REVISION_CONFLICT"
    assert body["message"] == "计划版本已过期"
    assert body["details"] == {"current_revision": 7}
    assert body["retryable"] is False


def test_validation_error_reports_fields_without_echoing_input(client):
    response = client.post("/api/_probe/echo", json={"amount": SECRET_LIKE_INPUT})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_FAILED"
    assert body["details"]["fields"], "应当指出是哪个字段出了问题"
    # 原始输入可能是口令或凭证，绝不能原样回显。
    assert SECRET_LIKE_INPUT not in response.text


def test_unhandled_exception_hides_internals(client):
    response = client.get("/api/_probe/boom")

    assert response.status_code == 500
    body = response.json()
    assert set(body) == ENVELOPE_FIELDS
    assert body["code"] == "INTERNAL_ERROR"
    assert body["retryable"] is True
    assert "内部细节不应外泄" not in response.text
    assert "Traceback" not in response.text


def test_every_response_carries_a_request_id(client):
    for path in ("/api/health", "/api/does-not-exist", "/api/_probe/boom"):
        response = client.get(path)
        assert response.headers["X-Request-Id"]


def test_error_body_request_id_matches_header(client):
    response = client.get("/api/does-not-exist")

    assert response.json()["request_id"] == response.headers["X-Request-Id"]


def test_request_ids_are_unique_per_request(client):
    first = client.get("/api/health").headers["X-Request-Id"]
    second = client.get("/api/health").headers["X-Request-Id"]

    assert first != second
