"""T05 create scheduling tables

建 user_planning_state、availability_versions、daily_overrides、goal_scheduling_preferences、
task_day_constraints、daily_agendas、agenda_revisions、agenda_items。字段与取舍见
docs/worklog/T05-scheduling.md 决策 A1—A4、A7—A10 与 docs/development/03-data-model.md 第 4 节。

- user_planning_state 以 owner_id 为主键：每用户一个协调入口（03 第 4 节）。
- daily_overrides 的 total/remaining 是两种额度口径，CHECK 只限取值，
  口径不可混用由业务层保证（03 第 4 节）。
- 清除单日任务约束保留 cleared 行、不删行（T05 决策 A8）。
- daily_agendas 与 agenda_revisions 之间存在相互引用（current_revision_id），
  SQLite 允许前向引用，建表顺序按依赖主序排列即可；use_alter 只影响 ORM 元数据排序。
- task_dependencies（T06）不在本迁移；agenda 快照中的依赖结果字段由业务实现预留。

手写迁移，不 import 应用代码。约束名与 goalflow.db.base.NAMING_CONVENTION 推出的名字一致，
CHECK 文本与 goalflow.scheduling.models 的构造逐字一致，由
tests/db/test_models_match_migrations.py 比对。

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ID = 36
_DATE = 10

_OVERRIDE_KINDS = "'total', 'remaining'"
_FOCUS_STATUSES = "'focused', 'normal'"
_CONSTRAINT_KINDS = "'must_do_today', 'locked'"
_CONSTRAINT_STATUSES = "'active', 'cleared'"
_AGENDA_REVISION_STATUSES = "'ready', 'conflicted'"


def upgrade() -> None:
    op.create_table(
        "user_planning_state",
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("owner_id", name="pk_user_planning_state"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_user_planning_state_owner_id_users"),
    )

    op.create_table(
        "availability_versions",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("effective_from", sa.String(_DATE), nullable=False),
        sa.Column("weekly_minutes_json", sa.Text(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_availability_versions"),
        sa.UniqueConstraint("owner_id", "version_no", name="uq_availability_versions_owner_id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_availability_versions_owner_id_users"),
        sa.CheckConstraint(
            "effective_from = date(effective_from)", name="ck_availability_versions_effective_from_format"
        ),
        sa.CheckConstraint("json_valid(weekly_minutes_json)", name="ck_availability_versions_weekly_minutes_json"),
    )
    op.create_index("ix_availability_versions_owner_id", "availability_versions", ["owner_id", "effective_from"])

    op.create_table(
        "daily_overrides",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("local_date", sa.String(_DATE), nullable=False),
        sa.Column("override_kind", sa.String(16), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("measured_at", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_daily_overrides"),
        sa.UniqueConstraint("owner_id", "local_date", name="uq_daily_overrides_owner_id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_daily_overrides_owner_id_users"),
        sa.CheckConstraint(f"override_kind IN ({_OVERRIDE_KINDS})", name="ck_daily_overrides_override_kind"),
        sa.CheckConstraint("local_date = date(local_date)", name="ck_daily_overrides_local_date_format"),
        sa.CheckConstraint("minutes >= 0", name="ck_daily_overrides_minutes"),
    )
    op.create_index("ix_daily_overrides_owner_id", "daily_overrides", ["owner_id", "local_date"])

    op.create_table(
        "goal_scheduling_preferences",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("goal_id", sa.String(_ID), nullable=False),
        sa.Column("focus_status", sa.String(16), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_goal_scheduling_preferences"),
        sa.UniqueConstraint("owner_id", "goal_id", name="uq_goal_scheduling_preferences_owner_id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_goal_scheduling_preferences_owner_id_users"),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], name="fk_goal_scheduling_preferences_goal_id_goals"),
        sa.CheckConstraint(f"focus_status IN ({_FOCUS_STATUSES})", name="ck_goal_scheduling_preferences_focus_status"),
        sa.CheckConstraint("rank >= 1", name="ck_goal_scheduling_preferences_rank"),
    )
    op.create_index(
        "ix_goal_scheduling_preferences_owner_id",
        "goal_scheduling_preferences",
        ["owner_id", "goal_id"],
    )

    op.create_table(
        "task_day_constraints",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("task_id", sa.String(_ID), nullable=False),
        sa.Column("local_date", sa.String(_DATE), nullable=False),
        sa.Column("constraint_kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_task_day_constraints"),
        sa.UniqueConstraint("owner_id", "task_id", "local_date", name="uq_task_day_constraints_owner_id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_task_day_constraints_owner_id_users"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_task_day_constraints_task_id_tasks"),
        sa.CheckConstraint(f"constraint_kind IN ({_CONSTRAINT_KINDS})", name="ck_task_day_constraints_constraint_kind"),
        sa.CheckConstraint(f"status IN ({_CONSTRAINT_STATUSES})", name="ck_task_day_constraints_status"),
        sa.CheckConstraint("local_date = date(local_date)", name="ck_task_day_constraints_local_date_format"),
    )
    op.create_index("ix_task_day_constraints_owner_id", "task_day_constraints", ["owner_id", "local_date", "status"])

    op.create_table(
        "daily_agendas",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("local_date", sa.String(_DATE), nullable=False),
        sa.Column("timezone_snapshot_json", sa.Text(), nullable=False),
        sa.Column("current_revision_id", sa.String(_ID), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_daily_agendas"),
        sa.UniqueConstraint("owner_id", "local_date", name="uq_daily_agendas_owner_id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_daily_agendas_owner_id_users"),
        sa.ForeignKeyConstraint(
            ["current_revision_id"],
            ["agenda_revisions.id"],
            name="fk_daily_agendas_current_revision_id_agenda_revisions",
        ),
        sa.CheckConstraint("local_date = date(local_date)", name="ck_daily_agendas_local_date_format"),
        sa.CheckConstraint("json_valid(timezone_snapshot_json)", name="ck_daily_agendas_timezone_snapshot_json"),
    )

    op.create_table(
        "agenda_revisions",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("agenda_id", sa.String(_ID), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("input_planning_revision", sa.Integer(), nullable=False),
        sa.Column("scheduling_policy_version", sa.String(64), nullable=False),
        sa.Column("capacity_snapshot_json", sa.Text(), nullable=False),
        sa.Column("deferred_json", sa.Text(), nullable=False),
        sa.Column("conflict_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_agenda_revisions"),
        sa.UniqueConstraint("agenda_id", "version_no", name="uq_agenda_revisions_agenda_id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_agenda_revisions_owner_id_users"),
        sa.ForeignKeyConstraint(
            ["agenda_id"], ["daily_agendas.id"], name="fk_agenda_revisions_agenda_id_daily_agendas"
        ),
        sa.CheckConstraint(f"status IN ({_AGENDA_REVISION_STATUSES})", name="ck_agenda_revisions_status"),
        sa.CheckConstraint("input_planning_revision >= 0", name="ck_agenda_revisions_input_planning_revision"),
        sa.CheckConstraint("json_valid(capacity_snapshot_json)", name="ck_agenda_revisions_capacity_snapshot_json"),
        sa.CheckConstraint("json_valid(deferred_json)", name="ck_agenda_revisions_deferred_json"),
        sa.CheckConstraint("json_valid(conflict_json)", name="ck_agenda_revisions_conflict_json"),
    )
    op.create_index("ix_agenda_revisions_owner_id", "agenda_revisions", ["owner_id", "agenda_id", "status"])

    op.create_table(
        "agenda_items",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("agenda_revision_id", sa.String(_ID), nullable=False),
        sa.Column("task_id", sa.String(_ID), nullable=False),
        sa.Column("task_spec_id", sa.String(_ID), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("allocated_minutes", sa.Integer(), nullable=False),
        sa.Column("reason_codes_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_agenda_items"),
        sa.UniqueConstraint("agenda_revision_id", "task_id", name="uq_agenda_items_agenda_revision_id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_agenda_items_owner_id_users"),
        sa.ForeignKeyConstraint(
            ["agenda_revision_id"], ["agenda_revisions.id"], name="fk_agenda_items_agenda_revision_id_agenda_revisions"
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_agenda_items_task_id_tasks"),
        sa.ForeignKeyConstraint(["task_spec_id"], ["task_specs.id"], name="fk_agenda_items_task_spec_id_task_specs"),
        sa.CheckConstraint("rank >= 1", name="ck_agenda_items_rank"),
        sa.CheckConstraint("allocated_minutes > 0", name="ck_agenda_items_allocated_minutes"),
        sa.CheckConstraint("json_valid(reason_codes_json)", name="ck_agenda_items_reason_codes_json"),
    )
    op.create_index("ix_agenda_items_owner_id", "agenda_items", ["owner_id", "task_id"])


def downgrade() -> None:
    op.drop_index("ix_agenda_items_owner_id", table_name="agenda_items")
    op.drop_table("agenda_items")
    op.drop_index("ix_agenda_revisions_owner_id", table_name="agenda_revisions")
    op.drop_table("agenda_revisions")
    op.drop_table("daily_agendas")
    op.drop_index("ix_task_day_constraints_owner_id", table_name="task_day_constraints")
    op.drop_table("task_day_constraints")
    op.drop_index("ix_goal_scheduling_preferences_owner_id", table_name="goal_scheduling_preferences")
    op.drop_table("goal_scheduling_preferences")
    op.drop_index("ix_daily_overrides_owner_id", table_name="daily_overrides")
    op.drop_table("daily_overrides")
    op.drop_index("ix_availability_versions_owner_id", table_name="availability_versions")
    op.drop_table("availability_versions")
    op.drop_table("user_planning_state")
