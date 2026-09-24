"""goals 业务测试的共用支撑。与 conftest 分开：测试目录没有 `__init__.py`，跨目录 import 不可靠。

`seed_plan` 的每个实体独立事务提交——goals 与 goal_profiles/plan_versions 之间是循环外键，
ORM 的 unit-of-work 无法保证 flush 顺序（SAWarning 已声明忽略这些表的 FK 排序），
依赖父行的插入必须先提交落库。
"""

from datetime import UTC, datetime

from goalflow.contracts.enums import ClosureKind
from goalflow.goals import lifecycle, service
from goalflow.goals.models import (
    GoalProfile,
    PlanPhase,
    PlanTaskMembership,
    PlanVersion,
    Route,
    RouteSet,
    Task,
    TaskBatch,
    TaskSpec,
)

NOW = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)


def create_goal(database, user, key: str = "support-create", title: str = "三个月跑完十公里"):
    return service.create_goal(database, user, title=title, initial_description=None, idempotency_key=key)


def confirm_profile(database, user, goal_id: str, expected_revision: int, key: str):
    return service.confirm_profile(database, user, goal_id, expected_revision=expected_revision, idempotency_key=key)


def seed_plan(database, user, goal, *, version_no: int = 1, status: str = "draft") -> str:
    """铺一套完整草稿：档案 → 路线集/路线 → 计划版本 → 阶段 → 批次 → 任务/规格/归属。"""
    profile_id = f"profile-{goal.id[:8]}-{version_no}"
    route_set_id = f"rset-{goal.id[:8]}-{version_no}"
    route_id = f"route-{goal.id[:8]}-{version_no}"
    plan_id = f"plan-{goal.id[:8]}-{version_no}"
    phase_id = f"phase-{goal.id[:8]}-{version_no}"
    batch_id = f"batch-{goal.id[:8]}-{version_no}"
    task_id = f"task-{goal.id[:8]}-{version_no}"
    spec_id = f"spec-{goal.id[:8]}-{version_no}"

    # 复用已确认档案（make_active_goal 先 confirm 过）；没有才新建。
    from sqlalchemy import select

    with database.write() as session:
        existing = session.scalars(
            select(GoalProfile).where(GoalProfile.goal_id == goal.id).order_by(GoalProfile.version_no.desc())
        ).first()
        if existing is not None:
            profile_id = existing.id
        else:
            session.add(
                GoalProfile(
                    id=profile_id,
                    owner_id=user.user_id,
                    goal_id=goal.id,
                    version_no=version_no,
                    result_definition="10 公里",
                    success_criteria_json="[]",
                    baseline_json="{}",
                    constraints_json="{}",
                    facts_json="{}",
                    confirmed_at=NOW,
                )
            )
    with database.write() as session:
        session.add(
            RouteSet(
                id=route_set_id,
                owner_id=user.user_id,
                goal_id=goal.id,
                profile_id=profile_id,
                planning_revision=0,
                availability_revision=0,
                generation_policy_version="route-policy-v1",
                input_snapshot_json="{}",
                input_hash=f"hash-{version_no}",
                status="current",
                created_at=NOW,
            )
        )
    with database.write() as session:
        session.add(
            Route(
                id=route_id,
                owner_id=user.user_id,
                route_set_id=route_set_id,
                status="current",
                title="渐进跑量",
                approach="每周加 10%",
                difference_keys_json="[]",
                duration_range_json="{}",
                phase_outline_json="[]",
                weekly_minutes=90,
                tradeoffs_json="[]",
                risks_json="[]",
                assumptions_json="[]",
                resources_json="[]",
                derived_metrics_json="{}",
                created_at=NOW,
            )
        )
    with database.write() as session:
        session.add(
            PlanVersion(
                id=plan_id,
                owner_id=user.user_id,
                goal_id=goal.id,
                version_no=version_no,
                profile_id=profile_id,
                route_id=route_id,
                status=status,
                start_date="2026-10-01",
                horizon_end="2026-12-31",
                detailed_through_date="2026-10-07",
                strategy_version="plan-v1",
                created_at=NOW,
            )
        )
    with database.write() as session:
        session.add(
            PlanPhase(
                id=phase_id,
                owner_id=user.user_id,
                plan_version_id=plan_id,
                phase_key="base",
                rank=1,
                title="打基础",
                outcome="连续跑 3 公里",
                exit_criteria_json="[]",
                duration_estimate_json='{"weeks": [3, 4]}',
                created_at=NOW,
            )
        )
    with database.write() as session:
        session.add(
            TaskBatch(
                id=batch_id,
                owner_id=user.user_id,
                plan_version_id=plan_id,
                window_start="2026-10-01",
                window_end="2026-10-07",
                input_progress_revision=0,
                generation_policy_version="batch-v1",
                status="active",
                created_at=NOW,
            )
        )
    with database.write() as session:
        session.add(
            Task(
                id=task_id,
                owner_id=user.user_id,
                goal_id=goal.id,
                execution_status="proposed",
                progress_revision=0,
                revision=0,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    with database.write() as session:
        session.add(
            TaskSpec(
                id=spec_id,
                owner_id=user.user_id,
                task_id=task_id,
                spec_no=1,
                title="轻松跑 20 分钟",
                instructions="conversation pace",
                completion_criteria_json="[]",
                executor="user",
                expected_minutes=20,
                minimum_minutes=15,
                maximum_minutes=30,
                latest_date="2026-10-03",
                can_split=False,
                created_at=NOW,
            )
        )
    with database.write() as session:
        session.add(
            PlanTaskMembership(
                id=f"member-{goal.id[:8]}-{version_no}",
                owner_id=user.user_id,
                plan_version_id=plan_id,
                task_batch_id=batch_id,
                phase_id=phase_id,
                task_id=task_id,
                task_spec_id=spec_id,
                created_at=NOW,
            )
        )
    return plan_id


def make_active_goal(database, user, *, key: str = "support-active", time_boundary: str = "fixed_date"):
    """走完整合法路径拿到一个 active 目标：创建 → 档案确认 → 计划启用。

    返回 (goal, plan_id)。调用后 goal.revision == 2（confirm +1、activate +1）。
    """
    goal = create_goal(database, user, key=f"{key}-create")
    service.update_profile_draft(
        database,
        user,
        goal.id,
        edits={"result_definition": "10 公里", "time_boundary": time_boundary},
        expected_revision=0,
    )
    confirm_profile(database, user, goal.id, expected_revision=0, key=f"{key}-confirm")
    plan_id = seed_plan(database, user, goal)
    lifecycle.activate_plan(
        database,
        user,
        goal.id,
        draft_plan_id=plan_id,
        expected_revision=1,
        planning_revision=None,
        idempotency_key=f"{key}-activate",
    )
    return goal, plan_id


def close_goal(
    database, user, goal_id: str, expected_revision: int, key: str, *, kind: ClosureKind = ClosureKind.STOPPED
):
    return lifecycle.close_goal(
        database,
        user,
        goal_id,
        expected_revision=expected_revision,
        closure_kind=kind,
        note=None,
        criteria_confirmations=[],
        idempotency_key=key,
    )
