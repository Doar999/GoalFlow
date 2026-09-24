"""T06 create goal link and dependency tables

建 goal_links、task_dependencies、change_proposals。字段与取舍见
docs/worklog/T06-goal-links.md 决策 A1—A6 与 docs/development/03-data-model.md 第 2、3 节。

- goal_links 规范化目标对：CHECK goal_a_id < goal_b_id 强制规范化方向；
  未失效关联用部分唯一索引表达，removed 行保留归档、解除后可重建（决策 A2）。
- task_dependencies 边唯一 + 禁自依赖边；跨目标边挂 goal_link_id 的约束由业务层
  校验，DB 无法跨表判定两任务的所属目标（决策 A4）。
- change_proposals.change_class 本期只含 confirmation_required / profile_revision_required
  两个取值，其余三个归 T12 扩展 CHECK（决策 A5）。
- fitness 领域禁止 required_outcome=verification_passed 是应用层策略谓词
  （16 号第 2 节），不落 DB CHECK（决策 A3）。

手写迁移，不 import 应用代码。约束名与 goalflow.db.base.NAMING_CONVENTION 推出的名字一致，
CHECK 文本与 goalflow.links.models 的构造逐字一致，由
tests/db/test_models_match_migrations.py 比对。

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ID = 36

_GOAL_LINK_STATUSES = "'proposed', 'active', 'removed'"
_DEPENDENCY_OUTCOMES = "'execution_completed', 'verification_passed'"
_CHANGE_CLASSES = "'confirmation_required', 'profile_revision_required'"
_CHANGE_PROPOSAL_STATUSES = "'pending', 'applied', 'rejected', 'stale'"


def upgrade() -> None:
    op.create_table(
        "goal_links",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("goal_a_id", sa.String(_ID), nullable=False),
        sa.Column("goal_b_id", sa.String(_ID), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("confirmed_at", sa.String(32), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_goal_links"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_goal_links_owner_id_users"),
        sa.ForeignKeyConstraint(["goal_a_id"], ["goals.id"], name="fk_goal_links_goal_a_id_goals"),
        sa.ForeignKeyConstraint(["goal_b_id"], ["goals.id"], name="fk_goal_links_goal_b_id_goals"),
        sa.CheckConstraint(f"status IN ({_GOAL_LINK_STATUSES})", name="ck_goal_links_status"),
        sa.CheckConstraint("goal_a_id < goal_b_id", name="ck_goal_links_canonical_pair"),
    )
    # 未失效的关联对目标对唯一；removed 行保留归档（T06 决策 A2）。
    op.create_index(
        "uq_goal_links_active_pair",
        "goal_links",
        ["goal_a_id", "goal_b_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('proposed', 'active')"),
    )
    op.create_index("ix_goal_links_owner_id", "goal_links", ["owner_id", "status"])

    op.create_table(
        "task_dependencies",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("plan_version_id", sa.String(_ID), nullable=False),
        sa.Column("predecessor_task_id", sa.String(_ID), nullable=False),
        sa.Column("successor_task_id", sa.String(_ID), nullable=False),
        sa.Column("required_outcome", sa.String(32), nullable=False),
        sa.Column("goal_link_id", sa.String(_ID), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_task_dependencies"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_task_dependencies_owner_id_users"),
        sa.ForeignKeyConstraint(
            ["plan_version_id"], ["plan_versions.id"], name="fk_task_dependencies_plan_version_id_plan_versions"
        ),
        sa.ForeignKeyConstraint(
            ["predecessor_task_id"], ["tasks.id"], name="fk_task_dependencies_predecessor_task_id_tasks"
        ),
        sa.ForeignKeyConstraint(
            ["successor_task_id"], ["tasks.id"], name="fk_task_dependencies_successor_task_id_tasks"
        ),
        sa.ForeignKeyConstraint(
            ["goal_link_id"], ["goal_links.id"], name="fk_task_dependencies_goal_link_id_goal_links"
        ),
        sa.CheckConstraint(
            f"required_outcome IN ({_DEPENDENCY_OUTCOMES})", name="ck_task_dependencies_required_outcome"
        ),
        sa.CheckConstraint("predecessor_task_id <> successor_task_id", name="ck_task_dependencies_no_self_edge"),
        sa.UniqueConstraint(
            "plan_version_id",
            "predecessor_task_id",
            "successor_task_id",
            name="uq_task_dependencies_plan_version_id",
        ),
    )
    op.create_index("ix_task_dependencies_goal_link_id", "task_dependencies", ["goal_link_id"])
    op.create_index("ix_task_dependencies_successor_task_id", "task_dependencies", ["successor_task_id"])

    op.create_table(
        "change_proposals",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("goal_id", sa.String(_ID), nullable=False),
        sa.Column("base_versions_json", sa.Text(), nullable=False),
        sa.Column("input_revision", sa.Integer(), nullable=False),
        sa.Column("proposed_patch_json", sa.Text(), nullable=False),
        sa.Column("impact_json", sa.Text(), nullable=False),
        sa.Column("change_class", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("accepted_at", sa.String(32), nullable=True),
        sa.Column("applied_at", sa.String(32), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_change_proposals"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_change_proposals_owner_id_users"),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], name="fk_change_proposals_goal_id_goals"),
        sa.CheckConstraint(f"change_class IN ({_CHANGE_CLASSES})", name="ck_change_proposals_change_class"),
        sa.CheckConstraint(f"status IN ({_CHANGE_PROPOSAL_STATUSES})", name="ck_change_proposals_status"),
        sa.CheckConstraint("json_valid(base_versions_json)", name="ck_change_proposals_base_versions_json"),
        sa.CheckConstraint("json_valid(proposed_patch_json)", name="ck_change_proposals_proposed_patch_json"),
        sa.CheckConstraint("json_valid(impact_json)", name="ck_change_proposals_impact_json"),
    )
    op.create_index("ix_change_proposals_owner_id", "change_proposals", ["owner_id", "goal_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_change_proposals_owner_id", table_name="change_proposals")
    op.drop_table("change_proposals")
    op.drop_index("ix_task_dependencies_successor_task_id", table_name="task_dependencies")
    op.drop_index("ix_task_dependencies_goal_link_id", table_name="task_dependencies")
    op.drop_table("task_dependencies")
    op.drop_index("ix_goal_links_owner_id", table_name="goal_links")
    op.drop_index("uq_goal_links_active_pair", table_name="goal_links")
    op.drop_table("goal_links")
