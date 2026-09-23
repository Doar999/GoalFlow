"""T07 create idempotency_requests

建通用幂等存储表。字段与取舍见 docs/worklog/T07-job-execution.md 决策 E4—E8、E25。

- 唯一约束建在 (owner_id, request_key) 上，operation 只是普通列（E4）：同一个 key 用在另一个
  操作上，要能被识别成冲突，而不是被当成两次独立请求。
- 只记录成功的请求：业务失败时整个写事务回滚，记录跟着消失（E6），因此状态码限定在 2xx。
- 结果引用拆成 result_type、result_id 两列而不是一段 JSON（E25）：它是核心关联，
  按 03 第 1 节保持独立字段。

手写迁移，不 import 应用代码。约束名与 goalflow.db.base.NAMING_CONVENTION 推出的名字一致，
由 tests/db/test_models_match_migrations.py 比对。

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ID = 36
_TIMESTAMP = 32


def upgrade() -> None:
    op.create_table(
        "idempotency_requests",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        # 与 goalflow.contracts.http.IDEMPOTENCY_KEY_MAX_LENGTH 一致。
        sa.Column("request_key", sa.String(200), nullable=False),
        # SHA-256 十六进制摘要。
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("result_type", sa.String(32), nullable=False),
        sa.Column("result_id", sa.String(_ID), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(_TIMESTAMP), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_idempotency_requests"),
        sa.UniqueConstraint("owner_id", "request_key", name="uq_idempotency_requests_owner_id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_idempotency_requests_owner_id_users"),
        sa.CheckConstraint(
            "response_status BETWEEN 200 AND 299",
            name="ck_idempotency_requests_response_status",
        ),
    )
    # 过期清理按 created_at 扫描。
    op.create_index("ix_idempotency_requests_created_at", "idempotency_requests", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_idempotency_requests_created_at", table_name="idempotency_requests")
    op.drop_table("idempotency_requests")
