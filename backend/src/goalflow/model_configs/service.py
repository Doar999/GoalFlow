"""个人模型配置的业务命令（T14 PR-2；交接卡决策 A1—A14）。

命令边界：

- 创建/设默认/删除走幂等（POST/PUT/DELETE，决策 A9）；修改走 expected_revision 乐观锁；
- 凭证只在创建与修改时经信封加密入库（crypto.py），任何视图只带 has_credential（决策 A5）；
- **写事务里绝不调模型、不发网络请求**（T16 给接手者第 3 条）：/test 先读配置出事务，
  探测在事务外执行，结果再入写事务落库；
- 出站校验在每次调用前执行，不止保存时（决策 A6；outbound.py）。

状态语义（决策 A4/A12）：enabled=0 是用户禁用（凭证保留、可恢复）；deleted_at 非空是
删除归档（凭证清空、不可恢复），归档行保留但所有端点按 404 处理。
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from goalflow.auth.rate_limit import FixedWindowRateLimiter, RateLimitRule
from goalflow.auth.service import CurrentUser
from goalflow.contracts.enums import CapabilityState, ModelApiMode, ModelProvider, ModelTestOutcome
from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.core.config import get_settings
from goalflow.db.session import Database
from goalflow.goals.service import _now, _uuid
from goalflow.idempotency import IdempotentRequest, ResultRef, compute_request_hash, run_idempotent
from goalflow.model_configs.crypto import CredentialCrypto
from goalflow.model_configs.models import ModelConfig
from goalflow.model_configs.outbound import OutboundPolicy
from goalflow.model_configs.probes import run_capability_probes

# 决策 A14：仅作滥用兜底，正常使用不可触达。
TEST_PER_USER: Final[RateLimitRule] = RateLimitRule("model-test:user", 1000, timedelta(minutes=15))
_TEST_LIMITER = FixedWindowRateLimiter(clock=_now)

_CAPABILITY_NAMES: Final[tuple[str, ...]] = ("basic_generation", "structured_output", "streaming")


@dataclass(frozen=True)
class ModelConfigView:
    """配置的脱敏视图，路由层从这里组装响应。凭证材料绝不出现（决策 A5）。"""

    id: str
    name: str
    model_provider: str
    api_mode: str | None
    base_url: str | None
    model_id: str
    revision: int
    enabled: bool
    is_default: bool
    has_credential: bool
    capabilities: dict[str, str] | None
    last_test_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ModelTestView:
    """一次连通性测试的结果（决策 A7：失败也走正常返回，不抛 MODEL_UNAVAILABLE）。"""

    config_id: str
    config_revision: int
    outcome: str
    capabilities: dict[str, str] | None
    error_kind: str | None
    error_message: str
    tested_at: datetime


@dataclass(frozen=True)
class ModelConfigRef:
    """供作业载荷绑定的非敏感模型配置引用。"""

    id: str
    revision: int
    model_provider: str
    api_mode: str | None
    model_id: str


def _crypto() -> CredentialCrypto:
    secret = get_settings().credential_encryption_key
    if secret is None:
        raise GoalflowError(ErrorCode.INTERNAL_ERROR, "部署环境未配置凭证加密主密钥")
    return CredentialCrypto(secret.get_secret_value())


def _outbound_policy() -> OutboundPolicy:
    return OutboundPolicy(get_settings().model_endpoint_allowlist)


def _idempotent_request(user: CurrentUser, operation: str, key: str, body: Any = None) -> IdempotentRequest:
    return IdempotentRequest(
        owner_id=user.user_id,
        operation=operation,
        key=key,
        request_hash=compute_request_hash(operation, body=body),
    )


def _get_active_config(session: Session, user: CurrentUser, config_id: str) -> ModelConfig:
    """取当前用户的未删除配置；归档行一律 404（决策 A4）。禁用行返回 None 由调用方分支。"""
    config = session.scalars(
        select(ModelConfig).where(ModelConfig.id == config_id, ModelConfig.owner_id == user.user_id)
    ).one_or_none()
    if config is None or config.deleted_at is not None:
        raise GoalflowError(ErrorCode.NOT_FOUND, "模型配置不存在")
    return config


def _config_view(config: ModelConfig) -> ModelConfigView:
    capabilities = json.loads(config.capabilities_json) if config.capabilities_json is not None else None
    return ModelConfigView(
        id=config.id,
        name=config.name,
        model_provider=config.model_provider,
        api_mode=config.api_mode,
        base_url=config.base_url,
        model_id=config.model_id,
        revision=config.revision,
        enabled=config.enabled,
        is_default=config.is_default,
        has_credential=config.credential_ciphertext is not None,
        capabilities=capabilities,
        last_test_at=config.last_test_at,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


def get_default_for_job(db: Database, *, owner_id: str) -> ModelConfigRef:
    """新作业只绑定当前默认配置的 ID 与版本；默认切换不改旧作业。"""
    with db.read() as session:
        config = session.scalars(
            select(ModelConfig).where(
                ModelConfig.owner_id == owner_id,
                ModelConfig.is_default.is_(True),
                ModelConfig.enabled.is_(True),
                ModelConfig.deleted_at.is_(None),
            )
        ).one_or_none()
        if config is None:
            raise GoalflowError(ErrorCode.MODEL_UNAVAILABLE, "请先选择可用的默认模型配置")
        return ModelConfigRef(
            id=config.id,
            revision=config.revision,
            model_provider=config.model_provider,
            api_mode=config.api_mode,
            model_id=config.model_id,
        )


def build_model_for_job(
    db: Database,
    *,
    owner_id: str,
    config_id: str,
    expected_revision: int,
    timeout_seconds: float,
    max_retries: int,
    max_tokens: int,
) -> Any:
    """复查归属和版本，在事务外解密与校验地址，再构造本次尝试的模型。"""
    with db.read() as session:
        config = session.scalars(
            select(ModelConfig).where(ModelConfig.id == config_id, ModelConfig.owner_id == owner_id)
        ).one_or_none()
        if (
            config is None
            or config.deleted_at is not None
            or not config.enabled
            or config.revision != expected_revision
        ):
            raise GoalflowError(ErrorCode.INPUT_STALE, "模型配置已变更，请重新发起作业")
        provider = ModelProvider(config.model_provider)
        api_mode = ModelApiMode(config.api_mode) if config.api_mode is not None else None
        model_id = config.model_id
        base_url = config.base_url
        ciphertext = config.credential_ciphertext

    api_key = _crypto().decrypt(ciphertext) if ciphertext is not None else None
    if base_url is not None:
        _outbound_policy().validate(base_url)

    from goalflow.model_configs.probes import build_chat_model

    return build_chat_model(
        model_provider=provider,
        api_mode=api_mode,
        model_id=model_id,
        api_key=api_key,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        max_tokens=max_tokens,
    )


def _validate_provider_api_mode(provider: ModelProvider, api_mode: ModelApiMode | None) -> None:
    """provider/api_mode 合法组合（07 号第 2 节；DB CHECK 兜底同一规则，决策 A3）。"""
    if provider is ModelProvider.OPENAI and api_mode is None:
        raise GoalflowError(
            ErrorCode.VALIDATION_FAILED, "openai 配置必须指定 api_mode（responses 或 chat_completions）"
        )
    if provider is ModelProvider.ANTHROPIC and api_mode is not None:
        raise GoalflowError(ErrorCode.VALIDATION_FAILED, "anthropic 配置不使用 api_mode，请勿提交")


def create_model_config(
    db: Database,
    user: CurrentUser,
    *,
    name: str,
    model_provider: ModelProvider,
    api_mode: ModelApiMode | None,
    base_url: str | None,
    model_id: str,
    api_key: str | None,
    idempotency_key: str,
) -> ModelConfigView:
    """创建配置。api_key 可选（本地无鉴权模型，决策 A11）；有自定义地址先过出站校验（决策 A6）。"""
    _validate_provider_api_mode(model_provider, api_mode)
    if base_url is not None:
        _outbound_policy().validate(base_url)
    request = _idempotent_request(user, "model_configs.create", idempotency_key)
    with db.write() as session:
        outcome = run_idempotent(
            session,
            request,
            lambda: _do_create(session, user, name, model_provider, api_mode, base_url, model_id, api_key),
            now=_now(),
        )
        config = session.scalars(select(ModelConfig).where(ModelConfig.id == outcome.result.id)).one()
        return _config_view(config)


def _do_create(
    session: Session,
    user: CurrentUser,
    name: str,
    model_provider: ModelProvider,
    api_mode: ModelApiMode | None,
    base_url: str | None,
    model_id: str,
    api_key: str | None,
) -> tuple[ResultRef, int]:
    now = _now()
    config_id = _uuid()
    ciphertext, key_version = _crypto().encrypt(api_key) if api_key is not None else (None, None)
    session.add(
        ModelConfig(
            id=config_id,
            owner_id=user.user_id,
            name=name,
            model_provider=model_provider.value,
            api_mode=ModelApiMode(api_mode).value if api_mode is not None else None,
            base_url=base_url,
            model_id=model_id,
            credential_ciphertext=ciphertext,
            encryption_key_version=key_version,
            revision=1,
            enabled=True,
            is_default=False,
            capabilities_json=None,
            last_test_at=None,
            created_at=now,
            updated_at=now,
        )
    )
    session.flush()
    return ResultRef("model_config", config_id), 201


def list_model_configs(db: Database, user: CurrentUser) -> list[ModelConfigView]:
    """列举未删除配置（含禁用，供重新启用；决策 A12）。"""
    with db.read() as session:
        configs = session.scalars(
            select(ModelConfig)
            .where(ModelConfig.owner_id == user.user_id, ModelConfig.deleted_at.is_(None))
            .order_by(ModelConfig.created_at, ModelConfig.id)
        ).all()
        return [_config_view(config) for config in configs]


def update_model_config(
    db: Database,
    user: CurrentUser,
    config_id: str,
    *,
    expected_revision: int,
    fields: dict[str, Any],
) -> ModelConfigView:
    """修改配置。fields 是请求模型 exclude_unset 后的字段（路由层区分"缺省"与"显式 null"）。

    - api_mode 仅 openai 配置可改，修改视为版本变化（决策 A3）；
    - base_url 显式 null 清除自定义地址；非 null 时过出站校验（决策 A6）；
    - api_key 显式提供即替换凭证（路由层已拒空串）；
    - enabled=False 禁用（凭证保留），True 重新启用（决策 A12）。
    """
    allowed = {"name", "api_mode", "base_url", "model_id", "api_key", "enabled"}
    unknown = set(fields) - allowed
    if unknown:
        raise GoalflowError(ErrorCode.VALIDATION_FAILED, f"不支持修改的字段：{sorted(unknown)}")

    with db.write() as session:
        config = _get_active_config(session, user, config_id)
        if config.revision != expected_revision:
            raise GoalflowError(
                ErrorCode.REVISION_CONFLICT,
                "配置已被更新，请刷新后重试",
                details={"current_revision": config.revision},
            )

        provider = ModelProvider(config.model_provider)
        if "api_mode" in fields:
            api_mode_value = fields["api_mode"]
            if provider is ModelProvider.ANTHROPIC and api_mode_value is not None:
                raise GoalflowError(ErrorCode.VALIDATION_FAILED, "anthropic 配置不使用 api_mode，请勿提交")
            if api_mode_value is not None:
                config.api_mode = ModelApiMode(api_mode_value).value
        if "name" in fields and fields["name"] is not None:
            config.name = fields["name"]
        if "model_id" in fields and fields["model_id"] is not None:
            config.model_id = fields["model_id"]
        if "base_url" in fields:
            if fields["base_url"] is None:
                config.base_url = None
            else:
                _outbound_policy().validate(fields["base_url"])
                config.base_url = fields["base_url"]
        if "api_key" in fields and fields["api_key"] is not None:
            ciphertext, key_version = _crypto().encrypt(fields["api_key"])
            config.credential_ciphertext = ciphertext
            config.encryption_key_version = key_version
        if "enabled" in fields and fields["enabled"] is not None:
            config.enabled = bool(fields["enabled"])

        config.revision += 1
        config.updated_at = _now()
        session.flush()
        return _config_view(config)


def set_default_model_config(
    db: Database, user: CurrentUser, config_id: str, *, idempotency_key: str
) -> ModelConfigView:
    """设默认：旧默认与新默认在同一写事务内切换（决策 A2，DB 部分唯一索引兜底）。"""
    request = _idempotent_request(user, "model_configs.set_default", idempotency_key, body=None)
    with db.write() as session:
        outcome = run_idempotent(
            session,
            request,
            lambda: _do_set_default(session, user, config_id),
            now=_now(),
        )
        config = session.scalars(select(ModelConfig).where(ModelConfig.id == outcome.result.id)).one()
        return _config_view(config)


def _do_set_default(session: Session, user: CurrentUser, config_id: str) -> tuple[ResultRef, int]:
    config = _get_active_config(session, user, config_id)
    if not config.enabled:
        # 已禁用/已删除的配置不能设为默认（决策 A12；契约 409）。
        raise GoalflowError(ErrorCode.GOAL_STATE_CONFLICT, "已禁用的配置不能设为默认")
    current_defaults = session.scalars(
        select(ModelConfig).where(ModelConfig.owner_id == user.user_id, ModelConfig.is_default.is_(True))
    ).all()
    now = _now()
    for row in current_defaults:
        if row.id != config.id:
            row.is_default = False
            row.updated_at = now
    # 先 flush 落库"清旧"，再置新默认：部分唯一索引对 (owner_id) 立即生效，
    # ORM 的工作单元不保证 UPDATE 顺序，不 flush 的话两条 UPDATE 可能先新后旧撞索引。
    session.flush()
    config.is_default = True
    config.updated_at = now
    session.flush()
    return ResultRef("model_config", config.id), 200


def delete_model_config(db: Database, user: CurrentUser, config_id: str, *, idempotency_key: str) -> ModelConfigView:
    """归档式删除（决策 A4）：不可恢复；行保留支撑作业非敏感历史。"""
    request = _idempotent_request(user, "model_configs.delete", idempotency_key, body=None)
    with db.write() as session:
        outcome = run_idempotent(
            session,
            request,
            lambda: _do_delete(session, user, config_id),
            now=_now(),
        )
        config = session.scalars(select(ModelConfig).where(ModelConfig.id == outcome.result.id)).one()
        return _config_view(config)


def _do_delete(session: Session, user: CurrentUser, config_id: str) -> tuple[ResultRef, int]:
    config = _get_active_config(session, user, config_id)
    now = _now()
    config.deleted_at = now
    config.enabled = False
    config.credential_ciphertext = None
    config.encryption_key_version = None
    config.is_default = False
    config.updated_at = now
    session.flush()
    return ResultRef("model_config", config.id), 200


def test_model_config(db: Database, user: CurrentUser, config_id: str) -> ModelTestView:
    """连通性测试。频控 → 读配置出事务 → 事务外探测 → 写事务落库（不违反 B1 纪律）。"""
    _TEST_LIMITER.hit(TEST_PER_USER, user.user_id)

    with db.read() as session:
        config = _get_active_config(session, user, config_id)
        if not config.enabled:
            raise GoalflowError(ErrorCode.NOT_FOUND, "模型配置不存在")
        config_revision = config.revision
        provider = ModelProvider(config.model_provider)
        api_mode = ModelApiMode(config.api_mode) if config.api_mode is not None else None
        base_url = config.base_url
        model_id = config.model_id
        ciphertext = config.credential_ciphertext

    api_key = _crypto().decrypt(ciphertext) if ciphertext is not None else None
    if base_url is not None:
        # 出站校验在每次调用前执行（决策 A6）；保存时的校验不能替代。
        _outbound_policy().validate(base_url)

    from goalflow.model_configs.probes import build_chat_model

    model = build_chat_model(
        model_provider=provider,
        api_mode=api_mode,
        model_id=model_id,
        api_key=api_key,
        base_url=base_url,
    )
    probes = run_capability_probes(model)

    basic = probes["basic_generation"]
    outcome = ModelTestOutcome.SUCCEEDED if basic.capability is CapabilityState.SUPPORTED else ModelTestOutcome.FAILED
    capabilities: dict[str, str] | None = (
        {name: probes[name].capability.value for name in _CAPABILITY_NAMES}
        if outcome is ModelTestOutcome.SUCCEEDED
        else None
    )
    tested_at = _now()

    with db.write() as session:
        row = session.scalars(
            select(ModelConfig).where(ModelConfig.id == config_id, ModelConfig.owner_id == user.user_id)
        ).one()
        row.capabilities_json = json.dumps(capabilities, ensure_ascii=False) if capabilities is not None else None
        row.last_test_at = tested_at
        session.flush()

    error_kind: str | None = None
    error_message = ""
    if basic.error_kind is not None:
        error_kind = basic.error_kind.value
        error_message = str(basic.error_message or "")
    return ModelTestView(
        config_id=config_id,
        config_revision=config_revision,
        outcome=outcome.value,
        capabilities=capabilities,
        error_kind=error_kind,
        error_message=error_message,
        tested_at=tested_at,
    )
