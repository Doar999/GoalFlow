"""写操作的 Idempotency-Key 依赖。见 01-contracts-and-ownership.md 第 5 节。"""

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from goalflow.api.app import create_app
from goalflow.api.dependencies import require_idempotency_key
from goalflow.contracts.http import IDEMPOTENCY_KEY_MAX_LENGTH


def _app_with_write_probe() -> FastAPI:
    app = create_app()

    @app.post("/api/_probe/write")
    def _write(key: str = Depends(require_idempotency_key)) -> dict[str, str]:
        return {"key": key}

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app_with_write_probe())


def test_missing_key_is_rejected(client):
    response = client.post("/api/_probe/write")

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_FAILED"


def test_valid_key_passes_through_trimmed(client):
    response = client.post("/api/_probe/write", headers={"Idempotency-Key": " job-create-1 "})

    assert response.status_code == 200
    assert response.json() == {"key": "job-create-1"}


@pytest.mark.parametrize(
    "key",
    [
        "",
        "   ",
        "has space",
        "a" * (IDEMPOTENCY_KEY_MAX_LENGTH + 1),
    ],
)
def test_malformed_key_is_rejected(client, key):
    response = client.post("/api/_probe/write", headers={"Idempotency-Key": key})

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_FAILED"
