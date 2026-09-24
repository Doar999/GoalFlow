"""model_configs HTTP 层测试：鉴权、幂等键、脱敏与隔离（T14 交接卡第 5 节）。"""

from typing import Any

import pytest

from goalflow.model_configs import service as model_config_service


def _patch_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeModel:
        def invoke(self, prompt: str) -> str:
            return "ok"

        def with_structured_output(self, schema: Any = None) -> Any:
            class _Chain:
                def invoke(self, prompt: str) -> dict[str, bool]:
                    return {"ok": True}

            return _Chain()

        def stream(self, prompt: str) -> Any:
            return iter(["ok"])

    monkeypatch.setattr("goalflow.model_configs.probes.build_chat_model", lambda **kwargs: _FakeModel())


@pytest.fixture
def created(authed_client) -> dict:
    """已创建的一个配置（探测桩就位，/test 可用）。"""
    response = authed_client.post(
        "/api/model-configs",
        json={
            "name": "主力配置",
            "model_provider": "openai",
            "api_mode": "chat_completions",
            "model_id": "gpt-4o-mini",
            "api_key": "sk-test-credential-0001",
        },
        headers={"Idempotency-Key": "api-create-1"},
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestAuthAndContract:
    def test_requires_login(self, client) -> None:
        response = client.get("/api/model-configs")
        assert response.status_code == 401

    def test_create_requires_idempotency_key(self, authed_client) -> None:
        response = authed_client.post(
            "/api/model-configs",
            json={"name": "x", "model_provider": "openai", "api_mode": "chat_completions", "model_id": "gpt-4o-mini"},
        )
        assert response.status_code == 422

    def test_anthropic_with_api_mode_rejected(self, authed_client) -> None:
        response = authed_client.post(
            "/api/model-configs",
            json={
                "name": "x",
                "model_provider": "anthropic",
                "api_mode": "responses",
                "model_id": "claude-sonnet-4",
            },
            headers={"Idempotency-Key": "bad-combo"},
        )
        assert response.status_code == 422

    def test_empty_api_key_rejected(self, authed_client) -> None:
        response = authed_client.post(
            "/api/model-configs",
            json={
                "name": "x",
                "model_provider": "openai",
                "api_mode": "chat_completions",
                "model_id": "gpt-4o-mini",
                "api_key": "",
            },
            headers={"Idempotency-Key": "empty-key"},
        )
        assert response.status_code == 422


class TestResponsesNeverCarryCredential:
    def test_create_response_has_no_credential_material(self, authed_client) -> None:
        response = authed_client.post(
            "/api/model-configs",
            json={
                "name": "脱敏",
                "model_provider": "openai",
                "api_mode": "chat_completions",
                "model_id": "gpt-4o-mini",
                "api_key": "sk-test-credential-visible-000999",
            },
            headers={"Idempotency-Key": "mask-1"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["has_credential"] is True
        # 契约字段里没有凭证材料键；响应文本也查不到明文。
        assert "api_key" not in body
        assert "sk-test-credential-visible-000999" not in response.text

    def test_list_response_has_no_credential_material(self, created, authed_client) -> None:
        response = authed_client.get("/api/model-configs")
        assert response.status_code == 200
        assert "sk-test-credential-0001" not in response.text


class TestIsolationAndLifecycle:
    def test_second_user_sees_nothing_and_cannot_touch(self, created, authed_client, second_client) -> None:
        assert second_client.get("/api/model-configs").json() == []
        response = second_client.patch(
            f"/api/model-configs/{created['id']}",
            json={"expected_revision": 1, "name": "偷改"},
        )
        assert response.status_code == 404

    def test_patch_revision_conflict(self, created, authed_client) -> None:
        response = authed_client.patch(
            f"/api/model-configs/{created['id']}",
            json={"expected_revision": 99, "name": "并发改动"},
        )
        assert response.status_code == 409
        assert response.json()["code"] == "REVISION_CONFLICT"

    def test_patch_empty_api_key_rejected(self, created, authed_client) -> None:
        response = authed_client.patch(
            f"/api/model-configs/{created['id']}",
            json={"expected_revision": 1, "api_key": ""},
        )
        assert response.status_code == 422

    def test_disable_then_reenable(self, created, authed_client) -> None:
        disabled = authed_client.patch(
            f"/api/model-configs/{created['id']}",
            json={"expected_revision": 1, "enabled": False},
        )
        assert disabled.status_code == 200
        assert disabled.json()["enabled"] is False
        # 禁用保留凭证（决策 A12）。
        assert disabled.json()["has_credential"] is True
        reenabled = authed_client.patch(
            f"/api/model-configs/{created['id']}",
            json={"expected_revision": 2, "enabled": True},
        )
        assert reenabled.json()["enabled"] is True

    def test_default_switch_and_delete_lifecycle(self, created, authed_client) -> None:
        defaulted = authed_client.put(
            "/api/model-configs/default",
            json={"config_id": created["id"]},
            headers={"Idempotency-Key": "default-1"},
        )
        assert defaulted.status_code == 200
        assert defaulted.json()["is_default"] is True

        deleted = authed_client.request(
            "DELETE",
            f"/api/model-configs/{created['id']}",
            json={},
            headers={"Idempotency-Key": "delete-1"},
        )
        assert deleted.status_code == 200
        body = deleted.json()
        assert body["enabled"] is False
        assert body["has_credential"] is False
        # 归档后：列表不可见、任何端点按 404 处理（决策 A4）。
        assert authed_client.get("/api/model-configs").json() == []
        assert (
            authed_client.patch(
                f"/api/model-configs/{created['id']}", json={"expected_revision": body["revision"], "name": "x"}
            ).status_code
            == 404
        )
        assert (
            authed_client.post(
                f"/api/model-configs/{created['id']}/test",
                json={},
                headers={"Idempotency-Key": "dead-test"},
            ).status_code
            == 404
        )


class TestProbeEndpoint:
    def test_test_endpoint_reports_capabilities(self, created, authed_client, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_probes(monkeypatch)
        response = authed_client.post(
            f"/api/model-configs/{created['id']}/test",
            json={},
            headers={"Idempotency-Key": "test-1"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["outcome"] == "succeeded"
        assert body["capabilities"] == {
            "basic_generation": "supported",
            "structured_output": "supported",
            "streaming": "supported",
        }
        assert body["error"] is None

    def test_test_endpoint_failure_is_sanitized(self, created, authed_client, monkeypatch: pytest.MonkeyPatch) -> None:
        class AuthenticationError(Exception):
            pass

        # 假密钥用 40+ 字符长 token：命中脱敏正则，又不匹配凭证粗筛的 sk- 样式。
        fake_key = "q" * 48

        class _FailingModel:
            def invoke(self, prompt: str) -> str:
                raise AuthenticationError(f"401 invalid key {fake_key}")

        monkeypatch.setattr("goalflow.model_configs.probes.build_chat_model", lambda **kwargs: _FailingModel())
        response = authed_client.post(
            f"/api/model-configs/{created['id']}/test",
            json={},
            headers={"Idempotency-Key": "test-2"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["outcome"] == "failed"
        assert body["error"]["kind"] == "auth"
        assert fake_key not in body["error"]["message"]
        assert "[已掩码]" in body["error"]["message"]

    def test_rate_limit_rule_matches_decision_a14(self) -> None:
        # 1000 次/15 分钟（几乎无限制的滥用兜底）。
        from goalflow.model_configs.service import TEST_PER_USER

        assert TEST_PER_USER.limit == 1000
        assert model_config_service.TEST_PER_USER.window.total_seconds() == 900
