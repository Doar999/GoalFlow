"""links 业务测试的共用支撑。与 conftest 分开：测试目录没有 `__init__.py`，跨目录 import 不可靠。

`seed_plan` 的每个实体独立事务提交——goals 与 goal_profiles/plan_versions 之间是循环外键，
ORM 的 unit-of-work 无法保证 flush 顺序，依赖父行的插入必须先提交落库。
与 tests/goals/goals_support.py 同型但独立一份。
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text

from goalflow.auth.service import CurrentUser
from goalflow.contracts.enums import UserRole
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
from goalflow.links import service as links

NOW = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)


def make_user(database, account_identifier: str) -> CurrentUser:
    """第二用户的请求身份（隔离测试用）。"""
    import uuid
    from datetime import timedelta

    user_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat(timespec="microseconds")
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, account_identifier, role, status, timezone, revision, created_at, updated_at)"
                " VALUES (:id, :account, 'user', 'active', 'UTC', 0, :now, :now)"
            ),
            {"id": user_id, "account": account_identifier, "now": now},
        )
    moment = datetime.now(UTC)
    return CurrentUser(
        user_id=user_id,
        account_identifier=account_identifier,
        role=UserRole.USER,
        timezone="UTC",
        session_id=str(uuid.uuid4()),
        session_created_at=moment,
        session_expires_at=moment + timedelta(days=1),
    )


def create_goal(database, user, key: str, title: str = "三个月跑完十公里"):
    return service.create_goal(database, user, title=title, initial_description=None, idempotency_key=key)


def seed_plan(database, user, goal, *, version_no: int = 1, status: str = "draft") -> str:
    """铺一套完整草稿：档案 → 路线集/路线 → 计划版本 → 阶段 → 批次 → 任务/规格/归属。返回 plan_id。"""
    profile_id = f"profile-{goal.id[:8]}-{version_no}"
    route_set_id = f"rset-{goal.id[:8]}-{version_no}"
    route_id = f"route-{goal.id[:8]}-{version_no}"
    plan_id = f"plan-{goal.id[:8]}-{version_no}"
    phase_id = f"phase-{goal.id[:8]}-{version_no}"
    batch_id = f"batch-{goal.id[:8]}-{version_no}"
    task_id = f"task-{goal.id[:8]}-{version_no}"
    spec_id = f"spec-{goal.id[:8]}-{version_no}"

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
    _add_task(database, user, plan_id, batch_id, phase_id, goal.id, task_id, spec_id, "轻松跑 20 分钟")
    return plan_id


def _add_task(
    database,
    user,
    plan_id: str,
    batch_id: str,
    phase_id: str,
    goal_id: str,
    task_id: str,
    spec_id: str,
    title: str,
    *,
    status: str = "proposed",
) -> None:
    with database.write() as session:
        session.add(
            Task(
                id=task_id,
                owner_id=user.user_id,
                goal_id=goal_id,
                execution_status=status,
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
                title=title,
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
                id=f"member-{task_id}",
                owner_id=user.user_id,
                plan_version_id=plan_id,
                task_batch_id=batch_id,
                phase_id=phase_id,
                task_id=task_id,
                task_spec_id=spec_id,
                created_at=NOW,
            )
        )


def add_task_to_plan(database, user, plan_id: str, task_key: str, *, title: str = "间歇跑 30 分钟") -> str:
    """向既有计划补一个任务（复用计划的批次与阶段），返回 task_id。

    先在读事务里取批次与阶段，再独立写事务落任务——外层写事务会占住库级写锁，
    嵌套写事务会等到 busy_timeout 超时（T16 短写事务协议）。
    """
    task_id = f"task-{task_key}"
    spec_id = f"spec-{task_key}"
    with database.read() as session:
        plan = session.scalars(select(PlanVersion).where(PlanVersion.id == plan_id)).one()
        batch = session.scalars(
            select(TaskBatch).where(TaskBatch.plan_version_id == plan_id).order_by(TaskBatch.window_start)
        ).first()
        phase = session.scalars(
            select(PlanPhase).where(PlanPhase.plan_version_id == plan_id).order_by(PlanPhase.rank)
        ).first()
        assert batch is not None and phase is not None
        batch_id, phase_id, goal_id = batch.id, phase.id, plan.goal_id
    _add_task(database, user, plan_id, batch_id, phase_id, goal_id, task_id, spec_id, title)
    return task_id


def make_active_goal(database, user, *, key: str, title: str = "三个月跑完十公里"):
    """走完整合法路径拿到一个 active 目标：创建 → 档案确认 → 计划启用。返回 (goal, plan_id)。"""
    goal = create_goal(database, user, key=f"{key}-create", title=title)
    service.update_profile_draft(
        database,
        user,
        goal.id,
        edits={"result_definition": "10 公里", "time_boundary": "fixed_date"},
        expected_revision=0,
    )
    service.confirm_profile(database, user, goal.id, expected_revision=0, idempotency_key=f"{key}-confirm")
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


def set_task_status(database, task_id: str, status: str) -> None:
    with database.write() as session:
        task = session.scalars(select(Task).where(Task.id == task_id)).one()
        task.execution_status = status
        task.updated_at = NOW


def planning_revision_of(database, owner_id: str) -> int:
    from goalflow.scheduling.service import planning_revision

    return planning_revision(database, owner_id)


def edge_command(predecessor_task_id: str, successor_task_id: str, required_outcome: str = "execution_completed"):
    return links.DependencyEdgeCommand(
        predecessor_task_id=predecessor_task_id,
        successor_task_id=successor_task_id,
        required_outcome=required_outcome,
    )


def make_link(database, user, goal_a, goal_b, edges: list[Any], *, key: str, confirm: bool = True):
    """建立一条（可选确认的）关联，返回 link id。edges 为 DependencyEdgeCommand 列表。"""
    view = links.propose_goal_link(
        database,
        user,
        goal_a.id,
        target_goal_id=goal_b.id,
        dependencies=edges,
        idempotency_key=f"{key}-propose",
    )
    if confirm:
        view = links.confirm_goal_link(database, user, view.id, dependencies=edges, idempotency_key=f"{key}-confirm")
    return view.id
