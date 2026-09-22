"""跨组件验收：应用能起来，且仓库里的 OpenAPI 契约与代码没有脱节。

这条验收保护的是工程链路本身——`scripts/api-generate.sh` 产出的
`openapi/goalflow.yaml` 是前端类型的唯一来源，一旦它与代码脱节，
前端会按一份过期契约开发而毫无察觉。
"""

from pathlib import Path

from fastapi.testclient import TestClient

from goalflow.api.app import create_app
from goalflow.tools.export_openapi import render_openapi_yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC_PATH = REPO_ROOT / "openapi" / "goalflow.yaml"


def test_health_endpoint_is_reachable():
    with TestClient(create_app()) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_export_is_deterministic():
    # 不确定的导出会让契约漂移检查变成随机失败，进而被所有人忽略。
    assert render_openapi_yaml() == render_openapi_yaml()


def test_openapi_export_is_utf8_and_lf():
    encoded = render_openapi_yaml().encode("utf-8")

    assert b"\r\n" not in encoded


def test_committed_spec_matches_current_code():
    assert SPEC_PATH.exists(), f"{SPEC_PATH} 不存在，请执行 bash scripts/api-generate.sh"

    committed = SPEC_PATH.read_bytes().decode("utf-8")
    assert committed == render_openapi_yaml(), "openapi/goalflow.yaml 已过期，请执行 bash scripts/api-generate.sh"


def test_error_envelope_is_published_in_the_contract():
    # 前端的错误映射依赖这个 schema 存在于契约里。
    document = create_app().openapi()

    assert "ErrorResponse" in document["components"]["schemas"]
    assert "ErrorCode" in document["components"]["schemas"]
