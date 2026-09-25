"""T08 model-call usage contract.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ID = 36


def upgrade() -> None:
    op.create_table(
        "model_calls",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("job_id", sa.String(_ID), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost", sa.Numeric(14, 6), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_model_calls"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_model_calls_owner_id_users"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name="fk_model_calls_job_id_jobs"),
        sa.CheckConstraint("provider IN ('openai', 'anthropic')", name="ck_model_calls_provider"),
        sa.CheckConstraint("status IN ('succeeded', 'failed')", name="ck_model_calls_status"),
        sa.CheckConstraint("input_tokens IS NULL OR input_tokens >= 0", name="ck_model_calls_input_tokens"),
        sa.CheckConstraint("output_tokens IS NULL OR output_tokens >= 0", name="ck_model_calls_output_tokens"),
        sa.CheckConstraint("estimated_cost IS NULL OR estimated_cost >= 0", name="ck_model_calls_estimated_cost"),
        sa.CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="ck_model_calls_latency_ms"),
    )
    op.create_index("ix_model_calls_owner_job", "model_calls", ["owner_id", "job_id"])


def downgrade() -> None:
    op.drop_index("ix_model_calls_owner_job", table_name="model_calls")
    op.drop_table("model_calls")
