"""配置校验。重点是生产环境不能带着占位密钥启动。"""

import pytest
from pydantic import ValidationError

from goalflow.core.config import PLACEHOLDER_SECRET, Environment, Settings

REAL_SECRET = "b2c4e6a8d0f2b4c6e8a0d2f4b6c8e0a2"


def _production(**overrides):
    values = {
        "env": Environment.PRODUCTION,
        "session_secret": REAL_SECRET,
        "credential_encryption_key": REAL_SECRET,
        "public_origin": "https://goalflow.example.com",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def test_local_defaults_do_not_require_secrets():
    settings = Settings(_env_file=None)

    assert settings.env is Environment.LOCAL
    assert settings.session_secret is None
    assert settings.allow_registration is True


def test_production_accepts_real_secrets():
    assert _production().env is Environment.PRODUCTION


@pytest.mark.parametrize("missing", ["session_secret", "credential_encryption_key"])
def test_production_rejects_missing_secret(missing):
    with pytest.raises(ValidationError, match=missing.upper()):
        _production(**{missing: None})


@pytest.mark.parametrize("field", ["session_secret", "credential_encryption_key"])
def test_production_rejects_placeholder_secret(field):
    # .env.example 的占位值上了生产，等于会话可伪造、已存凭证可解密。
    with pytest.raises(ValidationError, match=field.upper()):
        _production(**{field: PLACEHOLDER_SECRET})


def test_secrets_are_not_exposed_in_repr():
    settings = _production()

    assert REAL_SECRET not in repr(settings)
    assert REAL_SECRET not in str(settings)
    assert settings.session_secret is not None
    assert settings.session_secret.get_secret_value() == REAL_SECRET


@pytest.mark.parametrize("origin", ["", "http://goalflow.example.com"])
def test_production_requires_https_public_origin(origin):
    # 生产 Cookie 带 Secure 与 __Host- 前缀，只在 https 下生效；缺了它等于关掉 CSRF 防护。
    with pytest.raises(ValidationError, match="GOALFLOW_PUBLIC_ORIGIN"):
        _production(public_origin=origin)


@pytest.mark.parametrize(
    "origin",
    ["goalflow.example.com", "https://goalflow.example.com/", "https://goalflow.example.com/app", "ftp://x.example"],
)
def test_public_origin_must_be_a_bare_origin(origin):
    # Origin 请求头是逐字比较的，带路径或末尾斜杠的配置永远匹配不上，等于拒绝所有写请求。
    with pytest.raises(ValidationError, match="GOALFLOW_PUBLIC_ORIGIN"):
        Settings(public_origin=origin, _env_file=None)


def test_local_accepts_http_origin_and_empty_origin():
    assert Settings(public_origin="http://localhost:5173", _env_file=None).public_origin == "http://localhost:5173"
    assert Settings(_env_file=None).public_origin == ""
