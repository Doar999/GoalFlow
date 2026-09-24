"""model_configs 模块业务测试：增删改、默认切换、测试落库与状态语义（T14 交接卡第 5 节）。"""

import json
from typing import Any

import pytest
from sqlalchemy import select

from goalflow.contracts.enums import ModelProvider
from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.model_configs import service
from goalflow.model_configs.crypto import CredentialCrypto
from goalflow.model_configs.models import ModelConfig

# 与 conftest 的 deterministic_service_modules 夹具保持一致（无 __init__.py，不跨包 import）。
TEST_MASTER_KEY = "t14-test-master-key-not-for-production-0123456789"

_PROVIDER_OPENAI = ModelProvider.OPENAI
_PROVIDER_ANTHROPIC = ModelProvider.ANTHROPIC


def _create(
    database,
    user,
    *,
    key: str,
    provider: ModelProvider = _PROVIDER_OPENAI,
    api_mode: str | None = "chat_completions",
    api_key: str | None = "sk-test-credential-0001",
    base_url: str | None = None,
    name: str = "主力配置",
) -> service.ModelConfigView:
    return service.create_model_config(
        database,
        user,
        name=name,
        model_provider=provider,
        api_mode=api_mode,
        base_url=base_url,
        model_id="gpt-4o-mini" if provider is _PROVIDER_OPENAI else "claude-sonnet-4",
        api_key=api_key,
        idempotency_key=key,
    )


def _row(database, config_id: str) -> ModelConfig:
    with database.read() as session:
        return session.scalars(select(ModelConfig).where(ModelConfig.id == config_id)).one()


class TestCreate:
    def test_creates_active_config_with_encrypted_credential(self, database, user) -> None:
        view = _create(database, user, key="create-1")
        assert view.revision == 1
        assert view.enabled is True
        assert view.is_default is False
        assert view.has_credential is True
        # 库里查不到明文 Key；密文可被主密钥解回（决策 A5/A10）。
        row = _row(database, view.id)
        assert "sk-test-credential-0001" not in (row.credential_ciphertext or "")
        assert CredentialCrypto(TEST_MASTER_KEY).decrypt(row.credential_ciphertext) == "sk-test-credential-0001"

    def test_keyless_config_is_valid(self, database, user) -> None:
        # 本地无鉴权模型可不填 Key（决策 A11）：has_credential=false 但配置合法。
        view = _create(database, user, key="create-keyless", api_key=None, name="本地 Ollama")
        assert view.has_credential is False

    def test_anthropic_with_api_mode_rejected(self, database, user) -> None:
        with pytest.raises(GoalflowError) as excinfo:
            _create(database, user, key="anthropic-bad", provider=_PROVIDER_ANTHROPIC, api_mode="responses")
        assert excinfo.value.code == ErrorCode.VALIDATION_FAILED

    def test_openai_without_api_mode_rejected(self, database, user) -> None:
        with pytest.raises(GoalflowError) as excinfo:
            _create(database, user, key="openai-bad", api_mode=None)
        assert excinfo.value.code == ErrorCode.VALIDATION_FAILED

    def test_idempotent_replay_returns_same_config(self, database, user) -> None:
        first = _create(database, user, key="same-key")
        second = _create(database, user, key="same-key")
        assert first.id == second.id


class TestListIsolation:
    def test_second_user_sees_nothing(self, database, user) -> None:
        other = type(user)(
            user_id="other-user-id",
            account_identifier="other@local.dev",
            role=user.role,
            timezone="UTC",
            session_id="session-other",
            session_created_at=user.session_created_at,
            session_expires_at=user.session_expires_at,
        )
        _create(database, user, key="mine-1")
        assert service.list_model_configs(database, other) == []
        assert len(service.list_model_configs(database, user)) == 1


class TestUpdate:
    def test_stale_revision_conflicts(self, database, user) -> None:
        view = _create(database, user, key="u-1")
        with pytest.raises(GoalflowError) as excinfo:
            service.update_model_config(
                database, user, view.id, expected_revision=view.revision + 5, fields={"name": "新名字"}
            )
        assert excinfo.value.code == ErrorCode.REVISION_CONFLICT

    def test_name_update_bumps_revision(self, database, user) -> None:
        view = _create(database, user, key="u-2")
        updated = service.update_model_config(database, user, view.id, expected_revision=1, fields={"name": "改名"})
        assert updated.revision == 2
        assert updated.name == "改名"

    def test_absent_api_key_keeps_credential(self, database, user) -> None:
        view = _create(database, user, key="u-3")
        updated = service.update_model_config(database, user, view.id, expected_revision=1, fields={"name": "n"})
        assert updated.has_credential is True
        row = _row(database, view.id)
        assert CredentialCrypto(TEST_MASTER_KEY).decrypt(row.credential_ciphertext) == "sk-test-credential-0001"

    def test_api_key_replacement(self, database, user) -> None:
        view = _create(database, user, key="u-4")
        updated = service.update_model_config(
            database, user, view.id, expected_revision=1, fields={"api_key": "sk-test-credential-0002"}
        )
        assert updated.has_credential is True
        row = _row(database, view.id)
        assert CredentialCrypto(TEST_MASTER_KEY).decrypt(row.credential_ciphertext) == "sk-test-credential-0002"

    def test_base_url_explicit_null_clears(self, database, user) -> None:
        view = _create(database, user, key="u-5", base_url="https://api.example.com/v1")
        assert view.base_url == "https://api.example.com/v1"
        cleared = service.update_model_config(database, user, view.id, expected_revision=1, fields={"base_url": None})
        assert cleared.base_url is None

    def test_private_base_url_rejected_on_update(self, database, user, monkeypatch: pytest.MonkeyPatch) -> None:
        from goalflow.model_configs.outbound import OutboundPolicy

        monkeypatch.setattr(service, "_outbound_policy", lambda: OutboundPolicy("", resolver=lambda host: ["10.0.0.5"]))
        view = _create(database, user, key="u-6")
        with pytest.raises(GoalflowError) as excinfo:
            service.update_model_config(
                database, user, view.id, expected_revision=1, fields={"base_url": "http://models.internal:8000"}
            )
        assert excinfo.value.code == ErrorCode.MODEL_ENDPOINT_NOT_ALLOWED

    def test_disable_keeps_credential_and_reenables(self, database, user) -> None:
        # 禁用与删除是两个操作（决策 A12）：禁用保留凭证、可恢复。
        view = _create(database, user, key="u-7")
        disabled = service.update_model_config(database, user, view.id, expected_revision=1, fields={"enabled": False})
        assert disabled.enabled is False
        assert disabled.has_credential is True
        reenabled = service.update_model_config(database, user, view.id, expected_revision=2, fields={"enabled": True})
        assert reenabled.enabled is True

    def test_unknown_field_rejected(self, database, user) -> None:
        view = _create(database, user, key="u-8")
        with pytest.raises(GoalflowError) as excinfo:
            service.update_model_config(
                database, user, view.id, expected_revision=1, fields={"model_provider": "anthropic"}
            )
        assert excinfo.value.code == ErrorCode.VALIDATION_FAILED


class TestDefault:
    def test_switch_keeps_single_default(self, database, user) -> None:
        first = _create(database, user, key="d-1", name="A")
        second = _create(database, user, key="d-2", name="B")
        service.set_default_model_config(database, user, first.id, idempotency_key="default-a")
        switched = service.set_default_model_config(database, user, second.id, idempotency_key="default-b")
        assert switched.is_default is True
        with database.read() as session:
            defaults = session.scalars(
                select(ModelConfig).where(ModelConfig.owner_id == user.user_id, ModelConfig.is_default.is_(True))
            ).all()
        assert [row.id for row in defaults] == [second.id]

    def test_default_on_disabled_config_rejected(self, database, user) -> None:
        view = _create(database, user, key="d-3")
        service.update_model_config(database, user, view.id, expected_revision=1, fields={"enabled": False})
        with pytest.raises(GoalflowError) as excinfo:
            service.set_default_model_config(database, user, view.id, idempotency_key="default-off")
        assert excinfo.value.code == ErrorCode.GOAL_STATE_CONFLICT


class TestDelete:
    def test_delete_archives_without_removing_row(self, database, user) -> None:
        # 决策 A4：DELETE 是归档——凭证清空、enabled=0、deleted_at 置位、行保留。
        view = _create(database, user, key="del-1")
        service.set_default_model_config(database, user, view.id, idempotency_key="del-default")
        deleted = service.delete_model_config(database, user, view.id, idempotency_key="del-2")
        assert deleted.enabled is False
        assert deleted.has_credential is False
        assert deleted.is_default is False
        # deleted_at 不在脱敏视图里（响应契约不含它），直接对库断言。
        assert _row(database, view.id).deleted_at is not None
        row = _row(database, view.id)
        assert row.credential_ciphertext is None
        assert row.encryption_key_version is None
        assert row.is_default is False

    def test_deleted_config_is_404_everywhere(self, database, user) -> None:
        view = _create(database, user, key="del-3")
        service.delete_model_config(database, user, view.id, idempotency_key="del-4")
        with pytest.raises(GoalflowError) as excinfo:
            service.update_model_config(database, user, view.id, expected_revision=1, fields={"name": "x"})
        assert excinfo.value.code == ErrorCode.NOT_FOUND
        with pytest.raises(GoalflowError):
            service.delete_model_config(database, user, view.id, idempotency_key="del-5")
        with pytest.raises(GoalflowError):
            service.test_model_config(database, user, view.id)
        assert service.list_model_configs(database, user) == []

    def test_delete_is_idempotent(self, database, user) -> None:
        view = _create(database, user, key="del-6")
        first = service.delete_model_config(database, user, view.id, idempotency_key="same-del")
        second = service.delete_model_config(database, user, view.id, idempotency_key="same-del")
        assert first.id == second.id


class _FakeModel:
    """探测桩：按需在某个探测环节抛错。"""

    def __init__(self, fail_at: str | None = None, exc: Exception | None = None) -> None:
        self._fail_at = fail_at
        self._exc = exc

    def invoke(self, prompt: str) -> Any:
        if self._fail_at == "basic_generation":
            raise self._exc  # type: ignore[misc]
        return "ok"

    def with_structured_output(self, schema: Any) -> Any:
        outer = self

        class _Chain:
            def invoke(self, prompt: str) -> Any:
                if outer._fail_at == "structured_output":
                    raise outer._exc  # type: ignore[misc]
                return {"ok": True}

        return _Chain()

    def stream(self, prompt: str) -> Any:
        if self._fail_at == "streaming":
            raise self._exc  # type: ignore[misc]
        return iter(["ok"])


class TestProbe:
    def _test(self, database, user, key: str, model: Any) -> service.ModelTestView:
        view = _create(database, user, key=key)
        monkey = pytest.MonkeyPatch()
        try:
            monkey.setattr("goalflow.model_configs.probes.build_chat_model", lambda **kwargs: model)
            return service.test_model_config(database, user, view.id)
        finally:
            monkey.undo()

    def test_success_records_capabilities(self, database, user) -> None:
        result = self._test(database, user, "probe-1", _FakeModel())
        assert result.outcome == "succeeded"
        assert result.capabilities == {
            "basic_generation": "supported",
            "structured_output": "supported",
            "streaming": "supported",
        }
        assert result.error_kind is None
        row = _row(database, result.config_id)
        assert row.last_test_at is not None
        assert json.loads(row.capabilities_json)["basic_generation"] == "supported"

    def test_auth_failure_is_classified_and_sanitized(self, database, user) -> None:
        class AuthenticationError(Exception):
            pass

        result = self._test(
            database,
            user,
            "probe-2",
            _FakeModel(fail_at="basic_generation", exc=AuthenticationError("401 sk-ant-real-key-abcdefghijklmnopqrst")),
        )
        assert result.outcome == "failed"
        assert result.capabilities is None
        assert result.error_kind == "auth"
        assert "sk-ant-real-key" not in (result.error_message or "")

    def test_streaming_failure_leaves_basic_succeeded(self, database, user) -> None:
        class ReadTimeoutError(Exception):
            pass

        result = self._test(
            database, user, "probe-3", _FakeModel(fail_at="streaming", exc=ReadTimeoutError("timed out"))
        )
        assert result.outcome == "succeeded"
        assert result.capabilities is not None
        assert result.capabilities["streaming"] == "unsupported"


class TestRateLimit:
    def test_threshold_is_abuse_fallback_level(self) -> None:
        # 决策 A14：1000 次/15 分钟。计数纯内存，直接打满阈值验证 429 行为。
        from goalflow.auth.rate_limit import FixedWindowRateLimiter
        from goalflow.model_configs.service import TEST_PER_USER

        limiter = FixedWindowRateLimiter(clock=service._now)
        for _ in range(TEST_PER_USER.limit):
            limiter.hit(TEST_PER_USER, "user-rl")
        with pytest.raises(GoalflowError) as excinfo:
            limiter.hit(TEST_PER_USER, "user-rl")
        assert excinfo.value.code == ErrorCode.RATE_LIMITED
