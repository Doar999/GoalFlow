"""T07 create job tables

建 jobs、job_events、outbox_events。字段与取舍见 docs/worklog/T07-job-execution.md 决策 E9—E19。

- jobs 的去重是**部分**唯一索引（E10）：failed、cancelled、stale 不占去重键，否则同一输入失败
  一次就再也不能重新提交。
- jobs 在 03 的字段之外补了 revision、next_attempt_at、时间戳与 error_message（E11）。
- 状态与事件类型用 CHECK 约束钉住，取值与 goalflow.contracts.enums 的 JobStatus、JobEventType 一致。
- JSON 存 TEXT，用 json_valid() 兜底（03 第 1 节）。

手写迁移，不 import 应用代码。约束名与 goalflow.db.base.NAMING_CONVENTION 推出的名字一致，
由 tests/db/test_models_match_migrations.py 比对。

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ID = 36
_TIMESTAMP = 32

_JOB_STATUSES = "'queued', 'running', 'retry_wait', 'succeeded', 'failed', 'cancelled', 'stale'"
_EVENT_TYPES = (
    "'queued', 'started', 'stage_completed', 'awaiting_confirmation', 'retrying', "
    "'completed', 'failed', 'cancelled', 'stale'"
)


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("dedupe_key", sa.String(200), nullable=False),
        sa.Column("input_refs_json", sa.Text(), nullable=False),
        sa.Column("input_revision", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.String(_ID), nullable=True),
        sa.Column("lease_until", sa.String(_TIMESTAMP), nullable=True),
        sa.Column("cancel_requested_at", sa.String(_TIMESTAMP), nullable=True),
        sa.Column("next_attempt_at", sa.String(_TIMESTAMP), nullable=True),
        sa.Column("result_refs_json", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(_TIMESTAMP), nullable=False),
        sa.Column("updated_at", sa.String(_TIMESTAMP), nullable=False),
        sa.Column("finished_at", sa.String(_TIMESTAMP), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_jobs_owner_id_users"),
        sa.CheckConstraint(f"status IN ({_JOB_STATUSES})", name="ck_jobs_status"),
        sa.CheckConstraint("attempts >= 0", name="ck_jobs_attempts"),
        sa.CheckConstraint("json_valid(input_refs_json)", name="ck_jobs_input_refs_json"),
        sa.CheckConstraint(
            "result_refs_json IS NULL OR json_valid(result_refs_json)",
            name="ck_jobs_result_refs_json",
        ),
    )
    op.create_index(
        "uq_jobs_active_dedupe",
        "jobs",
        ["owner_id", "kind", "dedupe_key"],
        unique=True,
        sqlite_where=sa.text("status IN ('queued', 'running', 'retry_wait', 'succeeded')"),
    )
    op.create_index("ix_jobs_owner_id", "jobs", ["owner_id", "status"])
    # 恢复扫描按状态取候选（E17）。
    op.create_index("ix_jobs_status", "jobs", ["status"])

    op.create_table(
        "job_events",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("job_id", sa.String(_ID), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(_TIMESTAMP), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_job_events"),
        sa.UniqueConstraint("job_id", "sequence", name="uq_job_events_job_id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_job_events_owner_id_users"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name="fk_job_events_job_id_jobs"),
        sa.CheckConstraint(f"event_type IN ({_EVENT_TYPES})", name="ck_job_events_event_type"),
        sa.CheckConstraint("sequence >= 1", name="ck_job_events_sequence"),
        sa.CheckConstraint("json_valid(payload_json)", name="ck_job_events_payload_json"),
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("job_id", sa.String(_ID), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.String(_TIMESTAMP), nullable=False),
        sa.Column("created_at", sa.String(_TIMESTAMP), nullable=False),
        sa.Column("sent_at", sa.String(_TIMESTAMP), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_outbox_events"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_outbox_events_owner_id_users"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name="fk_outbox_events_job_id_jobs"),
        sa.CheckConstraint("event_type IN ('dispatch_job')", name="ck_outbox_events_event_type"),
        sa.CheckConstraint("status IN ('pending', 'sent')", name="ck_outbox_events_status"),
    )
    # 分发扫描：待分发状态 + next_attempt_at（03 第 6 节索引要求）。
    op.create_index("ix_outbox_events_status", "outbox_events", ["status", "next_attempt_at"])
    op.create_index("ix_outbox_events_job_id", "outbox_events", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_outbox_events_job_id", table_name="outbox_events")
    op.drop_index("ix_outbox_events_status", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_table("job_events")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_owner_id", table_name="jobs")
    op.drop_index("uq_jobs_active_dedupe", table_name="jobs")
    op.drop_table("jobs")
