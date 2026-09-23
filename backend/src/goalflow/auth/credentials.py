"""凭证规则：账号标识规范化、密码规则、密码哈希与令牌摘要。纯函数，不碰数据库。

参数取值见 docs/worklog/T03-auth-session.md 决策 C5、C8、C9、C10。
"""

import hashlib
import hmac
import secrets
import unicodedata
from functools import lru_cache
from typing import Final
from zoneinfo import available_timezones

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from goalflow.contracts.errors import ErrorCode, GoalflowError

ACCOUNT_IDENTIFIER_MIN_LENGTH: Final = 3
ACCOUNT_IDENTIFIER_MAX_LENGTH: Final = 254
PASSWORD_MIN_LENGTH: Final = 12
PASSWORD_MAX_LENGTH: Final = 128
DEFAULT_TIMEZONE: Final = "UTC"

HASH_SCHEME: Final = "argon2id"

# OWASP 密码存储速查表给出的 Argon2id 最低推荐配置：19 MiB、2 轮、1 路并行。
# 峰值内存 = memory_cost × 并发登录数，小机器上不宜用 argon2-cffi 默认的 64 MiB。
# 改这里的参数不需要迁移：旧哈希自带参数，登录成功时会按新参数重新哈希。
_HASHER: Final = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, hash_len=32, salt_len=16, type=Type.ID)

# 256 位随机数。熵足够高，库里用 HMAC 摘要即可，不需要慢哈希。
_TOKEN_BYTES: Final = 32
# token_urlsafe(32) 产出 43 个字符。超出这个长度的输入不可能是本系统签发的令牌，
# 直接拒绝，避免拿任意长的外部输入去算摘要。
MAX_TOKEN_LENGTH: Final = 64


def _canonical(raw: str) -> str:
    # casefold 的结果不保证仍是 NFKC 形式，所以前后各做一次 NFKC。
    return unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", raw).strip().casefold())


def _is_forbidden_char(char: str) -> bool:
    # 类别 C* 包括控制字符、格式字符（如零宽空格）、代理项与未分配码位。
    return char.isspace() or unicodedata.category(char).startswith("C")


def try_normalize_account_identifier(raw: str) -> str | None:
    """规范化账号标识；不合规则返回 None。登录用它：格式错误也只能表现为"账号或密码错误"。"""
    identifier = _canonical(raw)
    if not ACCOUNT_IDENTIFIER_MIN_LENGTH <= len(identifier) <= ACCOUNT_IDENTIFIER_MAX_LENGTH:
        return None
    if any(_is_forbidden_char(char) for char in identifier):
        return None
    return identifier


def normalize_account_identifier(raw: str) -> str:
    """注册用：不合规则时明确告诉用户哪里不对。"""
    identifier = try_normalize_account_identifier(raw)
    if identifier is None:
        raise GoalflowError(
            ErrorCode.VALIDATION_FAILED,
            f"账号标识需为 {ACCOUNT_IDENTIFIER_MIN_LENGTH}–{ACCOUNT_IDENTIFIER_MAX_LENGTH} 个字符，"
            "且不能包含空白或控制字符",
            details={
                "field": "account_identifier",
                "min_length": ACCOUNT_IDENTIFIER_MIN_LENGTH,
                "max_length": ACCOUNT_IDENTIFIER_MAX_LENGTH,
            },
        )
    return identifier


def validate_new_password(password: str, account_identifier: str, *, field: str = "password") -> None:
    """长度 12–128，不设字符组成规则，不得与账号标识相同（决策 C9）。

    长度上限同时是资源保护：它在计算哈希之前检查。
    """
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise GoalflowError(
            ErrorCode.VALIDATION_FAILED,
            f"密码长度需为 {PASSWORD_MIN_LENGTH}–{PASSWORD_MAX_LENGTH} 个字符",
            details={"field": field, "min_length": PASSWORD_MIN_LENGTH, "max_length": PASSWORD_MAX_LENGTH},
        )
    if _canonical(password) == account_identifier:
        raise GoalflowError(
            ErrorCode.VALIDATION_FAILED,
            "密码不能与账号标识相同",
            details={"field": field},
        )


@lru_cache(maxsize=1)
def _known_timezones() -> frozenset[str]:
    # 依赖 tzdata 包：Windows 与精简容器镜像没有系统时区库。
    return frozenset(available_timezones())


def validate_timezone(name: str | None) -> str:
    """IANA 时区名，缺省为 UTC。"""
    if name is None:
        return DEFAULT_TIMEZONE
    if name not in _known_timezones():
        raise GoalflowError(
            ErrorCode.VALIDATION_FAILED,
            "时区需为 IANA 时区名，例如 Asia/Shanghai",
            details={"field": "timezone"},
        )
    return name


def hash_password(password: str) -> str:
    """几十毫秒量级的 CPU 与内存开销。**不要在写事务里调用**，见交接卡第 9 节。"""
    return _HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _HASHER.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except (VerificationError, InvalidHashError):
        # 库里的哈希损坏或格式不认识。当作校验失败，不向外暴露区别。
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _HASHER.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


@lru_cache(maxsize=1)
def _decoy_hash() -> str:
    return _HASHER.hash(secrets.token_urlsafe(_TOKEN_BYTES))


def spend_verification_time(password: str) -> None:
    """账号不存在时也做一次同等代价的校验，让响应时间不泄露账号是否存在。"""
    verify_password(_decoy_hash(), password)


def new_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def token_digest(secret: bytes, token: str) -> str:
    """库里保存的是它，不是令牌原文。64 个十六进制字符。"""
    return hmac.new(secret, token.encode("utf-8"), hashlib.sha256).hexdigest()
