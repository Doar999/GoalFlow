"""凭证规则：账号标识规范化、密码规则、时区、哈希与令牌摘要（决策 C5、C8、C9、C10）。"""

import pytest

from goalflow.auth import credentials
from goalflow.contracts.errors import ErrorCode, GoalflowError


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Alice", "alice"),
        ("  alice@Example.COM  ", "alice@example.com"),
        # 全角字母经 NFKC 折叠为半角——否则 "ａｌｉｃｅ" 能注册出一个看起来一样的第二个账号。
        ("ａｌｉｃｅ", "alice"),
        ("Straße", "strasse"),
    ],
)
def test_identifier_variants_collapse_to_one_canonical_form(raw, expected):
    assert credentials.normalize_account_identifier(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "ab",
        "a" * 255,
        "alice smith",
        "alice​smith",  # 零宽空格：肉眼不可见，能伪造出看起来相同的标识
        "alice\x00",
    ],
)
def test_identifier_rejects_bad_shapes(raw):
    with pytest.raises(GoalflowError) as caught:
        credentials.normalize_account_identifier(raw)

    assert caught.value.code is ErrorCode.VALIDATION_FAILED
    assert caught.value.details["field"] == "account_identifier"
    assert credentials.try_normalize_account_identifier(raw) is None


@pytest.mark.parametrize("password", ["x" * 11, "x" * 129])
def test_password_length_bounds(password):
    with pytest.raises(GoalflowError) as caught:
        credentials.validate_new_password(password, "alice")

    assert caught.value.code is ErrorCode.VALIDATION_FAILED


def test_password_length_edges_are_accepted():
    credentials.validate_new_password("x" * 12, "alice")
    credentials.validate_new_password("x" * 128, "alice")


def test_password_has_no_composition_rules():
    credentials.validate_new_password("all lowercase words here", "alice")


def test_password_may_not_equal_identifier_under_normalization():
    with pytest.raises(GoalflowError, match="不能与账号标识相同"):
        credentials.validate_new_password("  Alice.Example.Org ", "alice.example.org")


def test_timezone_defaults_to_utc_and_rejects_unknown_names():
    assert credentials.validate_timezone(None) == "UTC"
    assert credentials.validate_timezone("Asia/Shanghai") == "Asia/Shanghai"
    with pytest.raises(GoalflowError) as caught:
        credentials.validate_timezone("Mars/Olympus")
    assert caught.value.details == {"field": "timezone"}


def test_hash_is_argon2id_with_configured_parameters_and_verifies():
    hashed = credentials.hash_password("correct horse battery")

    assert hashed.startswith("$argon2id$")
    assert "m=19456,t=2,p=1" in hashed
    assert credentials.verify_password(hashed, "correct horse battery")
    assert not credentials.verify_password(hashed, "correct horse batterY")
    assert not credentials.password_needs_rehash(hashed)


def test_corrupt_stored_hash_fails_closed():
    assert credentials.verify_password("not-a-hash", "anything") is False
    assert credentials.password_needs_rehash("not-a-hash") is True


def test_weaker_legacy_hash_is_flagged_for_rehash():
    from argon2 import PasswordHasher

    legacy = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash("correct horse battery")

    assert credentials.verify_password(legacy, "correct horse battery")
    assert credentials.password_needs_rehash(legacy)


def test_token_digest_is_keyed_and_fixed_width():
    token = credentials.new_token()

    digest = credentials.token_digest(b"secret-a", token)
    assert len(digest) == 64
    assert token not in digest
    # 换密钥摘要就变：泄露的库文件无法在不知道密钥的情况下离线比对令牌。
    assert credentials.token_digest(b"secret-b", token) != digest
    assert len(token) <= credentials.MAX_TOKEN_LENGTH
