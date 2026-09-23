"""账号四张表的 ORM 映射。结构以迁移 0001 为准，本文件跟随它。

原始密码、会话令牌、重置令牌都不落库：密码存 Argon2id 哈希，令牌存 HMAC 摘要。
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from goalflow.contracts.enums import UserRole, UserStatus
from goalflow.db.base import Base
from goalflow.db.types import UtcDateTime


def _one_of(column: str, values: type[UserRole] | type[UserStatus]) -> str:
    return f"{column} IN ({', '.join(repr(member.value) for member in values)})"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(_one_of("role", UserRole), name="role"),
        CheckConstraint(_one_of("status", UserStatus), name="status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # 只存规范化后的值（NFKC → 去首尾空白 → casefold），唯一约束建在它上面（决策 C10）。
    account_identifier: Mapped[str] = mapped_column(String(254), unique=True)
    role: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    timezone: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime())


class PasswordCredential(Base):
    """与 users 分表：读用户资料的代码路径永远碰不到密码哈希。"""

    __tablename__ = "password_credentials"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    hash_scheme: Mapped[str] = mapped_column(String(32))
    # 用户改密或重置时才更新；登录时因参数升级而重新哈希不算改密。
    changed_at: Mapped[datetime] = mapped_column(UtcDateTime())


class LoginSession(Base):
    """登录会话。类名避开 SQLAlchemy 的 `Session`，表名沿用设计文档的 `sessions`。"""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime())
    # 绝对过期时间。空闲过期由 last_seen_at 推算，不单独存。
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    user_agent_summary: Mapped[str | None] = mapped_column(String(200))
    # IPv4 存 /24、IPv6 存 /48 网段，不存完整地址。
    ip_prefix: Mapped[str | None] = mapped_column(String(64))


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    issued_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
    # 签发新令牌时，旧的未用令牌把 expires_at 提前到签发时刻，即刻作废。
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime())
    consumed_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
