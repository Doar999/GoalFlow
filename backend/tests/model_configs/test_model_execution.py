"""作业侧配置引用与模型构造的回归场景。"""

from typing import Any

import pytest

from goalflow.contracts.enums import ModelApiMode, ModelProvider
from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.model_configs import probes, service
from goalflow.model_configs.outbound import OutboundPolicy


def _create(
    database: Any,
    user: Any,
    *,
    key: str,
    api_mode: ModelApiMode = ModelApiMode.CHAT_COMPLETIONS,
    base_url: str | None = None,
) -> service.ModelConfigView:
    return service.create_model_config(
        database,
        user,
        name="作业模型",
        model_provider=ModelProvider.OPENAI,
        api_mode=api_mode,
        base_url=base_url,
        model_id="gpt-test-model",
        api_key="sk-test-credential-0001",
        idempotency_key=key,
    )


@pytest.mark.parametrize(
    ("provider", "mode", "expected_responses"),
    [
        (ModelProvider.OPENAI, ModelApiMode.RESPONSES, True),
        (ModelProvider.OPENAI, ModelApiMode.CHAT_COMPLETIONS, False),
        (ModelProvider.ANTHROPIC, None, None),
    ],
)
def test_builder_honors_api_mode(
    monkeypatch: pytest.MonkeyPatch,
    provider: ModelProvider,
    mode: ModelApiMode | None,
    expected_responses: bool | None,
) -> None:
    import langchain.chat_models

    received: dict[str, Any] = {}
    marker = object()

    def fake_init_chat_model(**kwargs: Any) -> object:
        received.update(kwargs)
        return marker

    monkeypatch.setattr(langchain.chat_models, "init_chat_model", fake_init_chat_model)
    result = probes.build_chat_model(
        model_provider=provider,
        api_mode=mode,
        model_id="model-x",
        api_key=None,
        base_url=None,
        timeout_seconds=7,
        max_retries=0,
        max_tokens=64,
    )
    assert result is marker
    assert received["model_provider"] == provider.value
    assert received["timeout"] == 7
    assert received["max_retries"] == 0
    assert received["max_tokens"] == 64
    if expected_responses is None:
        assert "use_responses_api" not in received
    else:
        assert received["use_responses_api"] is expected_responses


def test_job_ref_is_private_and_resolution_is_owner_scoped(
    database: Any, user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _create(database, user, key="job-1")
    service.set_default_model_config(database, user, config.id, idempotency_key="default-job-1")
    ref = service.get_default_for_job(database, owner_id=user.user_id)
    assert (ref.id, ref.revision, ref.model_provider, ref.api_mode) == (
        config.id,
        config.revision,
        "openai",
        "chat_completions",
    )
    assert "credential" not in repr(ref).lower()

    received: dict[str, Any] = {}
    marker = object()

    def fake_build_chat_model(**kwargs: Any) -> object:
        received.update(kwargs)
        return marker

    monkeypatch.setattr(probes, "build_chat_model", fake_build_chat_model)
    result = service.build_model_for_job(
        database,
        owner_id=user.user_id,
        config_id=ref.id,
        expected_revision=ref.revision,
        timeout_seconds=30,
        max_retries=0,
        max_tokens=512,
    )
    assert result is marker
    assert received["api_mode"] is ModelApiMode.CHAT_COMPLETIONS
    assert received["api_key"] == "sk-test-credential-0001"
    assert received["max_retries"] == 0

    with pytest.raises(GoalflowError) as excinfo:
        service.build_model_for_job(
            database,
            owner_id="another-user",
            config_id=ref.id,
            expected_revision=ref.revision,
            timeout_seconds=30,
            max_retries=0,
            max_tokens=512,
        )
    assert excinfo.value.code is ErrorCode.INPUT_STALE


def test_job_resolution_rejects_changed_disabled_and_deleted_config(database: Any, user: Any) -> None:
    config = _create(database, user, key="job-2")
    original_revision = config.revision
    updated = service.update_model_config(
        database, user, config.id, expected_revision=original_revision, fields={"model_id": "new-model"}
    )

    def resolve(revision: int) -> None:
        service.build_model_for_job(
            database,
            owner_id=user.user_id,
            config_id=config.id,
            expected_revision=revision,
            timeout_seconds=30,
            max_retries=0,
            max_tokens=512,
        )

    with pytest.raises(GoalflowError) as changed:
        resolve(original_revision)
    assert changed.value.code is ErrorCode.INPUT_STALE

    disabled = service.update_model_config(
        database, user, config.id, expected_revision=updated.revision, fields={"enabled": False}
    )
    with pytest.raises(GoalflowError) as inactive:
        resolve(disabled.revision)
    assert inactive.value.code is ErrorCode.INPUT_STALE

    service.delete_model_config(database, user, config.id, idempotency_key="delete-job-2")
    with pytest.raises(GoalflowError) as deleted:
        resolve(disabled.revision)
    assert deleted.value.code is ErrorCode.INPUT_STALE


def test_job_resolution_rechecks_outbound_address(database: Any, user: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _create(database, user, key="job-3", base_url="https://models.example.test")
    monkeypatch.setattr(
        service,
        "_outbound_policy",
        lambda: OutboundPolicy("", resolver=lambda host: ["10.1.2.3"]),
    )
    with pytest.raises(GoalflowError) as rejected:
        service.build_model_for_job(
            database,
            owner_id=user.user_id,
            config_id=config.id,
            expected_revision=config.revision,
            timeout_seconds=30,
            max_retries=0,
            max_tokens=512,
        )
    assert rejected.value.code is ErrorCode.MODEL_ENDPOINT_NOT_ALLOWED


def test_test_call_passes_saved_api_mode(database: Any, user: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _create(database, user, key="job-4", api_mode=ModelApiMode.RESPONSES)
    received: dict[str, Any] = {}

    def fake_build_chat_model(**kwargs: Any) -> object:
        received.update(kwargs)
        return object()

    monkeypatch.setattr(probes, "build_chat_model", fake_build_chat_model)
    monkeypatch.setattr(
        service,
        "run_capability_probes",
        lambda model: {
            name: probes.ProbeResult.ok() for name in ("basic_generation", "structured_output", "streaming")
        },
    )
    result = service.test_model_config(database, user, config.id)
    assert result.outcome == "succeeded"
    assert received["api_mode"] is ModelApiMode.RESPONSES
