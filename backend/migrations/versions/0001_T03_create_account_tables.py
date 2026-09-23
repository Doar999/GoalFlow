"""T03 create account tables

建账号四张表：users、password_credentials、sessions、password_reset_tokens。
字段与取舍见 docs/worklog/T03-auth-session.md 决策 C18。

手写迁移，不 import 应用代码：应用里的类型和模型会继续演进，而已合并的迁移必须永远按
当初的样子重放。约束名与 goalflow.db.base.NAMING_CONVENTION 推出的名字保持一致，
由 tests/db/test_models_match_migrations.py 比对。

事件时间列存 ISO 8601 UTC 带微秒的 TEXT，固定 32 个字符（03 第 1 节，T01 B3）。

Revision ID: 0001
Revises:
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ID = 36
_TIMESTAMP = 32
_TOKEN_HASH = 64


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("account_identifier", sa.String(254), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(_TIMESTAMP), nullable=False),
        sa.Column("updated_at", sa.String(_TIMESTAMP), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("account_identifier", name="uq_users_account_identifier"),
        sa.CheckConstraint("role IN ('user', 'admin')", name="ck_users_role"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_users_status"),
    )

    op.create_table(
        "password_credentials",
        sa.Column("user_id", sa.String(_ID), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("hash_scheme", sa.String(32), nullable=False),
        sa.Column("changed_at", sa.String(_TIMESTAMP), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name="pk_password_credentials"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_password_credentials_user_id_users"),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("user_id", sa.String(_ID), nullable=False),
        sa.Column("token_hash", sa.String(_TOKEN_HASH), nullable=False),
        sa.Column("created_at", sa.String(_TIMESTAMP), nullable=False),
        sa.Column("last_seen_at", sa.String(_TIMESTAMP), nullable=False),
        sa.Column("expires_at", sa.String(_TIMESTAMP), nullable=False),
        sa.Column("revoked_at", sa.String(_TIMESTAMP), nullable=True),
        sa.Column("user_agent_summary", sa.String(200), nullable=True),
        sa.Column("ip_prefix", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_sessions_user_id_users"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("user_id", sa.String(_ID), nullable=False),
        sa.Column("token_hash", sa.String(_TOKEN_HASH), nullable=False),
        sa.Column("issued_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.String(_TIMESTAMP), nullable=False),
        sa.Column("expires_at", sa.String(_TIMESTAMP), nullable=False),
        sa.Column("consumed_at", sa.String(_TIMESTAMP), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_password_reset_tokens"),
        sa.UniqueConstraint("token_hash", name="uq_password_reset_tokens_token_hash"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_password_reset_tokens_user_id_users"),
    )
    op.create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_password_reset_tokens_user_id", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("password_credentials")
    op.drop_table("users")
