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
