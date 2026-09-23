"""T04 create goal and plan tables

建 goals、goal_profile_drafts、goal_profiles、planning_sessions、route_sets、routes、
plan_versions、plan_phases、plan_milestones、task_batches、tasks、task_specs、
plan_task_memberships。字段与取舍见 docs/worklog/T04-goal-plan-versions.md 决策 A2、A7—A12
与 docs/development/03-data-model.md 第 1—3 节。

- 生命周期字段（paused_at、closure_* 、review_period、source_goal_id 等）按 17 号实现基线
  第 6 节；completed 仅对 kind=achievement 开放由 CHECK `completed_requires_achievement` 兜底。
- "一个目标最多一个当前执行版本"与"同一计划、窗口和进度输入只有一个有效批次"用部分唯一索引
  表达（03 第 1 节），不加冗余标记列。
- JSON 存 TEXT，用 json_valid() 兜底；日期列用 `col = date(col)` 校验格式（03 第 1 节）。
- task_dependencies（T06）、排期族表（T05）、change_proposals、goal_links（T06）不在本迁移。
- goals 与 goal_profiles/plan_versions 之间存在相互引用（active_profile_id、current_plan_version_id），
  SQLite 允许前向引用，建表顺序按依赖主序排列即可。

手写迁移，不 import 应用代码。约束名与 goalflow.db.base.NAMING_CONVENTION 推出的名字一致，
CHECK 文本与 goalflow.goals.models 的构造逐字一致，由
tests/db/test_models_match_migrations.py 比对。

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ID = 36
_DATE = 10

_DOMAIN = "'general', 'learning', 'fitness'"
_GOAL_KINDS = "'achievement', 'maintenance'"
_GOAL_STATUSES = "'draft', 'active', 'paused', 'completed', 'stopped'"
_CLOSURE_KINDS = "'completed', 'stopped'"
_REVIEW_PERIODS = "'weekly', 'biweekly', 'monthly'"
_READINESS = "'needs_input', 'review_ready', 'blocked'"
_ROUTE_SET_STATUSES = "'current', 'superseded', 'stale', 'failed'"
_ROUTE_STATUSES = "'current', 'superseded'"
_PLAN_VERSION_STATUSES = "'draft', 'active', 'superseded', 'discarded'"
_TASK_BATCH_STATUSES = "'active', 'superseded', 'failed'"
_TASK_EXECUTION_STATUSES = "'proposed', 'pending', 'in_progress', 'completed', 'cancelled'"
_EXECUTORS = "'user', 'agent', 'collaborative'"


def upgrade() -> None:
    op.create_table(
        "goals",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("domain", sa.String(16), nullable=False),
        sa.Column("domain_confidence", sa.Float(), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("isolation_mode", sa.String(16), nullable=True),
        sa.Column("active_profile_id", sa.String(_ID), nullable=True),
        sa.Column("current_plan_version_id", sa.String(_ID), nullable=True),
        sa.Column("paused_at", sa.String(32), nullable=True),
        sa.Column("pause_reason", sa.String(500), nullable=True),
        sa.Column("closed_at", sa.String(32), nullable=True),
        sa.Column("closure_kind", sa.String(16), nullable=True),
        sa.Column("closure_note", sa.String(2000), nullable=True),
        sa.Column("closure_criteria_snapshot_json", sa.Text(), nullable=True),
        sa.Column("review_period", sa.String(16), nullable=False),
        sa.Column("source_goal_id", sa.String(_ID), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_goals"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_goals_owner_id_users"),
        sa.ForeignKeyConstraint(
            ["active_profile_id"], ["goal_profiles.id"], name="fk_goals_active_profile_id_goal_profiles"
        ),
        sa.ForeignKeyConstraint(
            ["current_plan_version_id"], ["plan_versions.id"], name="fk_goals_current_plan_version_id_plan_versions"
        ),
        sa.ForeignKeyConstraint(["source_goal_id"], ["goals.id"], name="fk_goals_source_goal_id_goals"),
        sa.CheckConstraint(f"domain IN ({_DOMAIN})", name="ck_goals_domain"),
        sa.CheckConstraint(f"kind IN ({_GOAL_KINDS})", name="ck_goals_kind"),
        sa.CheckConstraint(f"status IN ({_GOAL_STATUSES})", name="ck_goals_status"),
        sa.CheckConstraint(f"review_period IN ({_REVIEW_PERIODS})", name="ck_goals_review_period"),
        sa.CheckConstraint(f"closure_kind IN ({_CLOSURE_KINDS})", name="ck_goals_closure_kind"),
        sa.CheckConstraint(
            "(closed_at IS NULL AND closure_kind IS NULL) OR (closed_at IS NOT NULL AND closure_kind IS NOT NULL)",
            name="ck_goals_closure_pair",
        ),
        sa.CheckConstraint(
            "closure_kind IS NULL OR closure_kind = 'stopped' OR kind = 'achievement'",
            name="ck_goals_completed_requires_achievement",
        ),
        sa.CheckConstraint(
            "domain_confidence IS NULL OR (domain_confidence >= 0 AND domain_confidence <= 1)",
            name="ck_goals_domain_confidence",
        ),
        sa.CheckConstraint(
            "closure_criteria_snapshot_json IS NULL OR json_valid(closure_criteria_snapshot_json)",
            name="ck_goals_closure_criteria_snapshot_json",
        ),
    )
    op.create_index("ix_goals_owner_id", "goals", ["owner_id", "status"])
    op.create_index("ix_goals_source_goal_id", "goals", ["source_goal_id"])

    op.create_table(
        "goal_profile_drafts",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("goal_id", sa.String(_ID), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("source_map_json", sa.Text(), nullable=False),
        sa.Column("gaps_json", sa.Text(), nullable=False),
        sa.Column("assumptions_json", sa.Text(), nullable=False),
        sa.Column("contradictions_json", sa.Text(), nullable=False),
        sa.Column("readiness", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_goal_profile_drafts"),
        sa.UniqueConstraint("goal_id", name="uq_goal_profile_drafts_goal_id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_goal_profile_drafts_owner_id_users"),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], name="fk_goal_profile_drafts_goal_id_goals"),
        sa.CheckConstraint(f"readiness IN ({_READINESS})", name="ck_goal_profile_drafts_readiness"),
        sa.CheckConstraint("json_valid(content_json)", name="ck_goal_profile_drafts_content_json"),
        sa.CheckConstraint("json_valid(source_map_json)", name="ck_goal_profile_drafts_source_map_json"),
        sa.CheckConstraint("json_valid(gaps_json)", name="ck_goal_profile_drafts_gaps_json"),
        sa.CheckConstraint("json_valid(assumptions_json)", name="ck_goal_profile_drafts_assumptions_json"),
        sa.CheckConstraint("json_valid(contradictions_json)", name="ck_goal_profile_drafts_contradictions_json"),
    )

    op.create_table(
        "goal_profiles",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("goal_id", sa.String(_ID), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("result_definition", sa.Text(), nullable=False),
        sa.Column("success_criteria_json", sa.Text(), nullable=False),
        sa.Column("baseline_json", sa.Text(), nullable=False),
        sa.Column("constraints_json", sa.Text(), nullable=False),
        sa.Column("facts_json", sa.Text(), nullable=False),
        sa.Column("confirmed_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_goal_profiles"),
        sa.UniqueConstraint("goal_id", "version_no", name="uq_goal_profiles_goal_id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_goal_profiles_owner_id_users"),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], name="fk_goal_profiles_goal_id_goals"),
        sa.CheckConstraint("json_valid(success_criteria_json)", name="ck_goal_profiles_success_criteria_json"),
        sa.CheckConstraint("json_valid(baseline_json)", name="ck_goal_profiles_baseline_json"),
        sa.CheckConstraint("json_valid(constraints_json)", name="ck_goal_profiles_constraints_json"),
        sa.CheckConstraint("json_valid(facts_json)", name="ck_goal_profiles_facts_json"),
    )

    op.create_table(
        "planning_sessions",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("goal_id", sa.String(_ID), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("profile_draft_id", sa.String(_ID), nullable=True),
        sa.Column("confirmed_profile_id", sa.String(_ID), nullable=True),
        sa.Column("selected_route_id", sa.String(_ID), nullable=True),
        sa.Column("draft_plan_id", sa.String(_ID), nullable=True),
        sa.Column("current_job_id", sa.String(_ID), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_planning_sessions"),
        sa.UniqueConstraint("goal_id", name="uq_planning_sessions_goal_id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_planning_sessions_owner_id_users"),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], name="fk_planning_sessions_goal_id_goals"),
        sa.ForeignKeyConstraint(
            ["profile_draft_id"],
            ["goal_profile_drafts.id"],
            name="fk_planning_sessions_profile_draft_id_goal_profile_drafts",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_profile_id"],
            ["goal_profiles.id"],
            name="fk_planning_sessions_confirmed_profile_id_goal_profiles",
        ),
        sa.ForeignKeyConstraint(
            ["selected_route_id"], ["routes.id"], name="fk_planning_sessions_selected_route_id_routes"
        ),
        sa.ForeignKeyConstraint(
            ["draft_plan_id"], ["plan_versions.id"], name="fk_planning_sessions_draft_plan_id_plan_versions"
        ),
        sa.ForeignKeyConstraint(["current_job_id"], ["jobs.id"], name="fk_planning_sessions_current_job_id_jobs"),
    )

    op.create_table(
        "route_sets",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("goal_id", sa.String(_ID), nullable=False),
        sa.Column("profile_id", sa.String(_ID), nullable=False),
        sa.Column("planning_revision", sa.Integer(), nullable=False),
        sa.Column("availability_revision", sa.Integer(), nullable=False),
        sa.Column("generation_policy_version", sa.String(64), nullable=False),
        sa.Column("input_snapshot_json", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("invalidated_reason", sa.String(500), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_route_sets"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_route_sets_owner_id_users"),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], name="fk_route_sets_goal_id_goals"),
        sa.ForeignKeyConstraint(["profile_id"], ["goal_profiles.id"], name="fk_route_sets_profile_id_goal_profiles"),
        sa.CheckConstraint(f"status IN ({_ROUTE_SET_STATUSES})", name="ck_route_sets_status"),
        sa.CheckConstraint("json_valid(input_snapshot_json)", name="ck_route_sets_input_snapshot_json"),
    )
    op.create_index("ix_route_sets_owner_id", "route_sets", ["owner_id", "goal_id", "status"])

    op.create_table(
        "routes",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("route_set_id", sa.String(_ID), nullable=False),
        sa.Column("based_on_route_id", sa.String(_ID), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("approach", sa.Text(), nullable=False),
        sa.Column("difference_keys_json", sa.Text(), nullable=False),
        sa.Column("duration_range_json", sa.Text(), nullable=False),
        sa.Column("phase_outline_json", sa.Text(), nullable=False),
        sa.Column("weekly_minutes", sa.Integer(), nullable=False),
        sa.Column("tradeoffs_json", sa.Text(), nullable=False),
        sa.Column("risks_json", sa.Text(), nullable=False),
        sa.Column("assumptions_json", sa.Text(), nullable=False),
        sa.Column("resources_json", sa.Text(), nullable=False),
        sa.Column("derived_metrics_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_routes"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_routes_owner_id_users"),
        sa.ForeignKeyConstraint(["route_set_id"], ["route_sets.id"], name="fk_routes_route_set_id_route_sets"),
        sa.ForeignKeyConstraint(["based_on_route_id"], ["routes.id"], name="fk_routes_based_on_route_id_routes"),
        sa.CheckConstraint(f"status IN ({_ROUTE_STATUSES})", name="ck_routes_status"),
        sa.CheckConstraint("weekly_minutes >= 0", name="ck_routes_weekly_minutes"),
        sa.CheckConstraint("json_valid(difference_keys_json)", name="ck_routes_difference_keys_json"),
        sa.CheckConstraint("json_valid(duration_range_json)", name="ck_routes_duration_range_json"),
        sa.CheckConstraint("json_valid(phase_outline_json)", name="ck_routes_phase_outline_json"),
        sa.CheckConstraint("json_valid(tradeoffs_json)", name="ck_routes_tradeoffs_json"),
        sa.CheckConstraint("json_valid(risks_json)", name="ck_routes_risks_json"),
        sa.CheckConstraint("json_valid(assumptions_json)", name="ck_routes_assumptions_json"),
        sa.CheckConstraint("json_valid(resources_json)", name="ck_routes_resources_json"),
        sa.CheckConstraint("json_valid(derived_metrics_json)", name="ck_routes_derived_metrics_json"),
    )
    op.create_index("ix_routes_route_set_id", "routes", ["route_set_id", "status"])

    op.create_table(
        "plan_versions",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("goal_id", sa.String(_ID), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.String(_ID), nullable=False),
        sa.Column("route_id", sa.String(_ID), nullable=False),
        sa.Column("based_on_version_id", sa.String(_ID), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("start_date", sa.String(_DATE), nullable=False),
        sa.Column("horizon_end", sa.String(_DATE), nullable=True),
        sa.Column("detailed_through_date", sa.String(_DATE), nullable=True),
        sa.Column("strategy_version", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_plan_versions"),
        sa.UniqueConstraint("goal_id", "version_no", name="uq_plan_versions_goal_id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_plan_versions_owner_id_users"),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], name="fk_plan_versions_goal_id_goals"),
        sa.ForeignKeyConstraint(["profile_id"], ["goal_profiles.id"], name="fk_plan_versions_profile_id_goal_profiles"),
        sa.ForeignKeyConstraint(["route_id"], ["routes.id"], name="fk_plan_versions_route_id_routes"),
        sa.ForeignKeyConstraint(
            ["based_on_version_id"], ["plan_versions.id"], name="fk_plan_versions_based_on_version_id_plan_versions"
        ),
        sa.CheckConstraint(f"status IN ({_PLAN_VERSION_STATUSES})", name="ck_plan_versions_status"),
        sa.CheckConstraint("start_date = date(start_date)", name="ck_plan_versions_start_date_format"),
        sa.CheckConstraint(
            "horizon_end IS NULL OR horizon_end = date(horizon_end)", name="ck_plan_versions_horizon_end_format"
        ),
        sa.CheckConstraint(
            "horizon_end IS NULL OR horizon_end >= start_date",
            name="ck_plan_versions_horizon_end_not_before_start",
        ),
        sa.CheckConstraint(
            "detailed_through_date IS NULL OR detailed_through_date = date(detailed_through_date)",
            name="ck_plan_versions_detailed_through_date_format",
        ),
    )
    # 一个目标最多一个当前执行版本（03 第 1 节部分唯一索引）。
    op.create_index(
        "uq_plan_versions_active_per_goal",
        "plan_versions",
        ["goal_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
    )
    op.create_index("ix_plan_versions_owner_id", "plan_versions", ["owner_id", "status"])

    op.create_table(
        "plan_phases",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("plan_version_id", sa.String(_ID), nullable=False),
        sa.Column("phase_key", sa.String(64), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("exit_criteria_json", sa.Text(), nullable=False),
        sa.Column("duration_estimate_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_plan_phases"),
        sa.UniqueConstraint("plan_version_id", "phase_key", name="uq_plan_phases_plan_version_id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_plan_phases_owner_id_users"),
        sa.ForeignKeyConstraint(
            ["plan_version_id"], ["plan_versions.id"], name="fk_plan_phases_plan_version_id_plan_versions"
        ),
        sa.CheckConstraint("rank >= 1", name="ck_plan_phases_rank"),
        sa.CheckConstraint("json_valid(exit_criteria_json)", name="ck_plan_phases_exit_criteria_json"),
        sa.CheckConstraint("json_valid(duration_estimate_json)", name="ck_plan_phases_duration_estimate_json"),
    )

    op.create_table(
        "plan_milestones",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("plan_version_id", sa.String(_ID), nullable=False),
        sa.Column("phase_id", sa.String(_ID), nullable=False),
        sa.Column("milestone_key", sa.String(64), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("success_criteria_json", sa.Text(), nullable=False),
        sa.Column("target_window_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_plan_milestones"),
        sa.UniqueConstraint("plan_version_id", "milestone_key", name="uq_plan_milestones_plan_version_id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_plan_milestones_owner_id_users"),
        sa.ForeignKeyConstraint(
            ["plan_version_id"], ["plan_versions.id"], name="fk_plan_milestones_plan_version_id_plan_versions"
        ),
        sa.ForeignKeyConstraint(["phase_id"], ["plan_phases.id"], name="fk_plan_milestones_phase_id_plan_phases"),
        sa.CheckConstraint("rank >= 1", name="ck_plan_milestones_rank"),
        sa.CheckConstraint("json_valid(success_criteria_json)", name="ck_plan_milestones_success_criteria_json"),
        sa.CheckConstraint("json_valid(target_window_json)", name="ck_plan_milestones_target_window_json"),
    )

    op.create_table(
        "task_batches",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("plan_version_id", sa.String(_ID), nullable=False),
        sa.Column("window_start", sa.String(_DATE), nullable=False),
        sa.Column("window_end", sa.String(_DATE), nullable=False),
        sa.Column("input_progress_revision", sa.Integer(), nullable=False),
        sa.Column("generation_policy_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_task_batches"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_task_batches_owner_id_users"),
        sa.ForeignKeyConstraint(
            ["plan_version_id"], ["plan_versions.id"], name="fk_task_batches_plan_version_id_plan_versions"
        ),
        sa.CheckConstraint(f"status IN ({_TASK_BATCH_STATUSES})", name="ck_task_batches_status"),
        sa.CheckConstraint("window_start = date(window_start)", name="ck_task_batches_window_start_format"),
        sa.CheckConstraint("window_end = date(window_end)", name="ck_task_batches_window_end_format"),
        sa.CheckConstraint("window_end >= window_start", name="ck_task_batches_window_end_not_before_start"),
    )
    # 同一计划、窗口和进度输入只产生一个有效批次（03 第 3 节）。
    op.create_index(
        "uq_task_batches_active_per_window",
        "task_batches",
        ["plan_version_id", "window_start", "input_progress_revision"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
    )
    op.create_index("ix_task_batches_plan_version_id", "task_batches", ["plan_version_id", "window_start"])

    op.create_table(
        "tasks",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("goal_id", sa.String(_ID), nullable=False),
        sa.Column("execution_status", sa.String(16), nullable=False),
        sa.Column("remaining_minutes_estimate", sa.Integer(), nullable=True),
        sa.Column("progress_revision", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tasks"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_tasks_owner_id_users"),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], name="fk_tasks_goal_id_goals"),
        sa.CheckConstraint(f"execution_status IN ({_TASK_EXECUTION_STATUSES})", name="ck_tasks_execution_status"),
        sa.CheckConstraint(
            "remaining_minutes_estimate IS NULL OR remaining_minutes_estimate >= 0",
            name="ck_tasks_remaining_minutes_estimate",
        ),
    )
    op.create_index("ix_tasks_owner_id", "tasks", ["owner_id", "execution_status"])
    op.create_index("ix_tasks_goal_id", "tasks", ["goal_id", "execution_status"])

    op.create_table(
        "task_specs",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("task_id", sa.String(_ID), nullable=False),
        sa.Column("spec_no", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("completion_criteria_json", sa.Text(), nullable=False),
        sa.Column("executor", sa.String(16), nullable=False),
        sa.Column("expected_minutes", sa.Integer(), nullable=False),
        sa.Column("minimum_minutes", sa.Integer(), nullable=False),
        sa.Column("maximum_minutes", sa.Integer(), nullable=False),
        sa.Column("estimate_confidence", sa.String(16), nullable=True),
        sa.Column("earliest_date", sa.String(_DATE), nullable=True),
        sa.Column("latest_date", sa.String(_DATE), nullable=False),
        sa.Column("can_split", sa.Boolean(), nullable=False),
        sa.Column("minimum_session_minutes", sa.Integer(), nullable=True),
        sa.Column("verification_policy", sa.String(64), nullable=True),
        sa.Column("domain_payload_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_task_specs"),
        sa.UniqueConstraint("task_id", "spec_no", name="uq_task_specs_task_id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_task_specs_owner_id_users"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_task_specs_task_id_tasks"),
        sa.CheckConstraint(f"executor IN ({_EXECUTORS})", name="ck_task_specs_executor"),
        sa.CheckConstraint("expected_minutes >= 0", name="ck_task_specs_expected_minutes"),
        sa.CheckConstraint("minimum_minutes >= 0", name="ck_task_specs_minimum_minutes"),
        sa.CheckConstraint("maximum_minutes >= 0", name="ck_task_specs_maximum_minutes"),
        sa.CheckConstraint("minimum_minutes <= expected_minutes", name="ck_task_specs_minimum_not_above_expected"),
        sa.CheckConstraint("expected_minutes <= maximum_minutes", name="ck_task_specs_expected_not_above_maximum"),
        sa.CheckConstraint(
            "minimum_session_minutes IS NULL OR minimum_session_minutes >= 0",
            name="ck_task_specs_minimum_session_minutes",
        ),
        sa.CheckConstraint(
            "earliest_date IS NULL OR earliest_date = date(earliest_date)",
            name="ck_task_specs_earliest_date_format",
        ),
        sa.CheckConstraint("latest_date = date(latest_date)", name="ck_task_specs_latest_date_format"),
        sa.CheckConstraint(
            "earliest_date IS NULL OR earliest_date <= latest_date",
            name="ck_task_specs_earliest_not_after_latest",
        ),
        sa.CheckConstraint("can_split IN (0, 1)", name="ck_task_specs_can_split"),
        sa.CheckConstraint("json_valid(completion_criteria_json)", name="ck_task_specs_completion_criteria_json"),
        sa.CheckConstraint(
            "domain_payload_json IS NULL OR json_valid(domain_payload_json)",
            name="ck_task_specs_domain_payload_json",
        ),
    )

    op.create_table(
        "plan_task_memberships",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("plan_version_id", sa.String(_ID), nullable=False),
        sa.Column("task_batch_id", sa.String(_ID), nullable=False),
        sa.Column("phase_id", sa.String(_ID), nullable=False),
        sa.Column("milestone_id", sa.String(_ID), nullable=True),
        sa.Column("task_id", sa.String(_ID), nullable=False),
        sa.Column("task_spec_id", sa.String(_ID), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_plan_task_memberships"),
        sa.UniqueConstraint("plan_version_id", "task_id", name="uq_plan_task_memberships_plan_version_id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_plan_task_memberships_owner_id_users"),
        sa.ForeignKeyConstraint(
            ["plan_version_id"],
            ["plan_versions.id"],
            name="fk_plan_task_memberships_plan_version_id_plan_versions",
        ),
        sa.ForeignKeyConstraint(
            ["task_batch_id"], ["task_batches.id"], name="fk_plan_task_memberships_task_batch_id_task_batches"
        ),
        sa.ForeignKeyConstraint(["phase_id"], ["plan_phases.id"], name="fk_plan_task_memberships_phase_id_plan_phases"),
        sa.ForeignKeyConstraint(
            ["milestone_id"], ["plan_milestones.id"], name="fk_plan_task_memberships_milestone_id_plan_milestones"
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_plan_task_memberships_task_id_tasks"),
        sa.ForeignKeyConstraint(
            ["task_spec_id"], ["task_specs.id"], name="fk_plan_task_memberships_task_spec_id_task_specs"
        ),
    )
    op.create_index("ix_plan_task_memberships_task_id", "plan_task_memberships", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_plan_task_memberships_task_id", table_name="plan_task_memberships")
    op.drop_table("plan_task_memberships")
    op.drop_table("task_specs")
    op.drop_index("ix_tasks_goal_id", table_name="tasks")
    op.drop_index("ix_tasks_owner_id", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index("ix_task_batches_plan_version_id", table_name="task_batches")
    op.drop_index("uq_task_batches_active_per_window", table_name="task_batches")
    op.drop_table("task_batches")
    op.drop_table("plan_milestones")
    op.drop_table("plan_phases")
    op.drop_index("ix_plan_versions_owner_id", table_name="plan_versions")
    op.drop_index("uq_plan_versions_active_per_goal", table_name="plan_versions")
    op.drop_table("plan_versions")
    op.drop_index("ix_routes_route_set_id", table_name="routes")
    op.drop_table("routes")
    op.drop_index("ix_route_sets_owner_id", table_name="route_sets")
    op.drop_table("route_sets")
    op.drop_table("planning_sessions")
    op.drop_table("goal_profiles")
    op.drop_table("goal_profile_drafts")
    op.drop_index("ix_goals_source_goal_id", table_name="goals")
    op.drop_index("ix_goals_owner_id", table_name="goals")
    op.drop_table("goals")
