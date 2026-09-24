"""目标模块的确定性业务入口（T04 PR-2）。

事务边界归本模块的 Interface 函数（T03 决策 C3）：每个函数自己开 `read()` / `write()`，
路由只做参数校验与调用。写事务一律短事务，模型调用与预算计算都在事务之外。

模型驱动的生成类操作（澄清、路线生成、计划生成、自然语言修改）不在本模块：
按交接卡决策 A1，生成候选由 T08 的图提供后经 `run_idempotent` 作业接入。

\"等待用户\"不占用 Worker：所有交互进度都在 planning_sessions / goal_profile_drafts，
本模块的任何函数都不会阻塞等待。
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from goalflow.auth.service import CurrentUser
from goalflow.contracts.enums import GoalDomain, GoalKind, GoalStatus, ProfileDraftReadiness
from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.contracts.policies import DEFAULT_REVIEW_PERIOD
from goalflow.db.session import Database
from goalflow.goals.models import (
    Goal,
    GoalProfile,
    GoalProfileDraft,
    PlanMilestone,
    PlanningSession,
    PlanPhase,
    PlanTaskMembership,
    PlanVersion,
    Route,
    RouteSet,
    Task,
    TaskBatch,
    TaskSpec,
)
from goalflow.idempotency import IdempotentRequest, ResultRef, compute_request_hash, run_idempotent

# 尚未确认任何档案时的默认形态。confirm_profile 会按档案时间边界重新推导并写审计；
# 空档期的 kind 只是占位，不参与 completed 判定（D12 的判定对象是活动目标）。
_DRAFT_PLACEHOLDER_KIND = GoalKind.ACHIEVEMENT

# 档案时间边界到目标形态的映射（12-goal-lifecycle.md 第 1 节，已确认）。
_KIND_BY_TIME_BOUNDARY: dict[str, GoalKind] = {
    "fixed_date": GoalKind.ACHIEVEMENT,
    "flexible_period": GoalKind.ACHIEVEMENT,
    "ongoing_maintenance": GoalKind.MAINTENANCE,
}


def _now() -> datetime:
    """模块级时间入口，测试里 monkeypatch 它来驱动撤销窗口等时间判定。"""
    return datetime.now(UTC)


def _loads(raw: str) -> Any:
    return json.loads(raw)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _uuid() -> str:
    import uuid

    return str(uuid.uuid4())


# —— 视图：路由层从这里组装响应模型，模块内部不接触 Pydantic ——


@dataclass(frozen=True)
class GoalView:
    id: str
    title: str
    domain: str
    domain_confidence: float | None
    kind: str
    status: str
    review_period: str
    active_profile_id: str | None
    current_plan_version_id: str | None
    source_goal_id: str | None
    paused_at: datetime | None
    pause_reason: str | None
    closed_at: datetime | None
    closure_kind: str | None
    closure_note: str | None
    revision: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ProfileDraftView:
    goal_id: str
    content: dict[str, Any]
    source_map: dict[str, Any]
    gaps: list[dict[str, Any]]
    assumptions: list[dict[str, Any]]
    contradictions: list[dict[str, Any]]
    readiness: str
    revision: int
    updated_at: datetime


@dataclass(frozen=True)
class ProfileView:
    id: str
    goal_id: str
    version_no: int
    result_definition: str
    success_criteria: list[dict[str, Any]]
    baseline: dict[str, Any]
    constraints: dict[str, Any]
    facts: dict[str, Any]
    confirmed_at: datetime


@dataclass(frozen=True)
class RouteView:
    id: str
    route_set_id: str
    status: str
    based_on_route_id: str | None
    title: str
    approach: str
    difference_keys: list[str]
    duration_range: dict[str, Any]
    phase_outline: list[dict[str, Any]]
    weekly_minutes: int
    tradeoffs: list[dict[str, Any]]
    risks: list[dict[str, Any]]
    assumptions: list[dict[str, Any]]
    required_resources: list[dict[str, Any]]
    derived_metrics: dict[str, Any]
    revision: int = 0
    created_at: datetime | None = None


@dataclass(frozen=True)
class RouteSetView:
    id: str
    goal_id: str
    profile_id: str
    status: str
    invalidated_reason: str | None
    planning_revision: int
    availability_revision: int
    routes: list[RouteView]
    revision: int = 0
    created_at: datetime | None = None


@dataclass(frozen=True)
class RouteSelectionView:
    goal_id: str
    route_id: str
    selected_at: datetime
    revision: int


@dataclass(frozen=True)
class DraftTaskView:
    task_id: str
    spec_no: int
    title: str
    executor: str
    expected_minutes: int
    minimum_minutes: int
    maximum_minutes: int
    can_split: bool
    minimum_session_minutes: int | None
    earliest_date: str | None
    latest_date: str
    execution_status: str
    phase_id: str
    milestone_id: str | None
    batch_window: dict[str, Any]
    revision: int


@dataclass(frozen=True)
class PlanDraftView:
    plan_version_id: str
    goal_id: str
    status: str
    start_date: str
    horizon_end: str | None
    detailed_through_date: str | None
    phases: list[dict[str, Any]]
    milestones: list[dict[str, Any]]
    tasks: list[DraftTaskView]
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    stale: bool = False
    stale_reason: str | None = None
    revision: int = 0


@dataclass(frozen=True)
class ActivationView:
    goal_id: str
    plan_version_id: str
    first_batch_window: dict[str, Any]
    activated_at: datetime


# —— 内部取数辅助：全部带归属校验，仅知道 ID 不构成访问权限 ——


def _get_goal(session: Session, user: CurrentUser, goal_id: str) -> Goal:
    goal = session.scalars(select(Goal).where(Goal.id == goal_id, Goal.owner_id == user.user_id)).one_or_none()
    if goal is None:
        raise GoalflowError(ErrorCode.NOT_FOUND, "目标不存在")
    return goal


def _get_goal_in_plan(session: Session, user: CurrentUser, plan_version_id: str) -> PlanVersion:
    plan = session.scalars(
        select(PlanVersion).where(PlanVersion.id == plan_version_id, PlanVersion.owner_id == user.user_id)
    ).one_or_none()
    if plan is None:
        raise GoalflowError(ErrorCode.NOT_FOUND, "计划不存在")
    return plan


def _goal_view(goal: Goal) -> GoalView:
    return GoalView(
        id=goal.id,
        title=goal.title,
        domain=goal.domain,
        domain_confidence=goal.domain_confidence,
        kind=goal.kind,
        status=goal.status,
        review_period=goal.review_period,
        active_profile_id=goal.active_profile_id,
        current_plan_version_id=goal.current_plan_version_id,
        source_goal_id=goal.source_goal_id,
        paused_at=goal.paused_at,
        pause_reason=goal.pause_reason,
        closed_at=goal.closed_at,
        closure_kind=goal.closure_kind,
        closure_note=goal.closure_note,
        revision=goal.revision,
        created_at=goal.created_at,
        updated_at=goal.updated_at,
    )


def _draft_view(goal_id: str, draft: GoalProfileDraft) -> ProfileDraftView:
    return ProfileDraftView(
        goal_id=goal_id,
        content=_loads(draft.content_json),
        source_map=_loads(draft.source_map_json),
        gaps=_loads(draft.gaps_json),
        assumptions=_loads(draft.assumptions_json),
        contradictions=_loads(draft.contradictions_json),
        readiness=draft.readiness,
        revision=draft.revision,
        updated_at=draft.updated_at,
    )


def _profile_view(profile: GoalProfile) -> ProfileView:
    return ProfileView(
        id=profile.id,
        goal_id=profile.goal_id,
        version_no=profile.version_no,
        result_definition=profile.result_definition,
        success_criteria=_loads(profile.success_criteria_json),
        baseline=_loads(profile.baseline_json),
        constraints=_loads(profile.constraints_json),
        facts=_loads(profile.facts_json),
        confirmed_at=profile.confirmed_at,
    )


def _derive_kind(content: dict[str, Any], current: GoalKind) -> GoalKind:
    """由档案时间边界推导形态（D12 第 1 节）。档案未给时间边界时维持现状。"""
    boundary = content.get("time_boundary")
    if isinstance(boundary, str) and boundary in _KIND_BY_TIME_BOUNDARY:
        return _KIND_BY_TIME_BOUNDARY[boundary]
    return current


# —— 目标与档案 ——


def create_goal(
    db: Database,
    user: CurrentUser,
    *,
    title: str,
    initial_description: str | None,
    idempotency_key: str,
) -> GoalView:
    """创建 draft 目标、初始档案草稿与 planning_session（R02；05 第 2 节 create_goal）。"""
    request = IdempotentRequest(
        owner_id=user.user_id,
        operation="goals.create",
        key=idempotency_key,
        request_hash=compute_request_hash("goals.create", body=None),
    )
    with db.write() as session:
        outcome = run_idempotent(
            session, request, lambda: _insert_goal(session, user, title, initial_description), now=_now()
        )
        goal = session.scalars(select(Goal).where(Goal.id == outcome.result.id)).one()
        return _goal_view(goal)


def _insert_goal(
    session: Session, user: CurrentUser, title: str, initial_description: str | None
) -> tuple[ResultRef, int]:
    now = _now()
    goal_id = _uuid()
    draft_id = _uuid()
    session.add(
        Goal(
            id=goal_id,
            owner_id=user.user_id,
            title=title,
            domain=GoalDomain.GENERAL.value,
            domain_confidence=None,
            kind=_DRAFT_PLACEHOLDER_KIND.value,
            status=GoalStatus.DRAFT.value,
            review_period=DEFAULT_REVIEW_PERIOD.value,
            revision=0,
            created_at=now,
            updated_at=now,
        )
    )
    # 循环外键下 UoW 的插入顺序不可靠，父行先 flush（下同，见 T04 决策 A7 的部分唯一索引注释）。
    session.flush()
    content: dict[str, Any] = {"title": title}
    if initial_description is not None:
        content["initial_description"] = initial_description
    session.add(
        GoalProfileDraft(
            id=draft_id,
            owner_id=user.user_id,
            goal_id=goal_id,
            content_json=_dumps(content),
            source_map_json=_dumps(dict.fromkeys(content, "user")),
            gaps_json=_dumps([]),
            assumptions_json=_dumps([]),
            contradictions_json=_dumps([]),
            readiness=ProfileDraftReadiness.NEEDS_INPUT.value,
            revision=0,
            created_at=now,
            updated_at=now,
        )
    )
    session.add(
        PlanningSession(
            id=_uuid(),
            owner_id=user.user_id,
            goal_id=goal_id,
            state="clarifying",
            profile_draft_id=draft_id,
            revision=0,
            created_at=now,
            updated_at=now,
        )
    )
    return ResultRef("goal", goal_id), 201


def get_goal(db: Database, user: CurrentUser, goal_id: str) -> GoalView:
    with db.read() as session:
        return _goal_view(_get_goal(session, user, goal_id))


def get_profile_draft(db: Database, user: CurrentUser, goal_id: str) -> ProfileDraftView:
    with db.read() as session:
        goal = _get_goal(session, user, goal_id)
        draft = session.scalars(select(GoalProfileDraft).where(GoalProfileDraft.goal_id == goal.id)).one_or_none()
        if draft is None:
            raise GoalflowError(ErrorCode.NOT_FOUND, "档案草稿不存在")
        return _draft_view(goal.id, draft)


def update_profile_draft(
    db: Database,
    user: CurrentUser,
    goal_id: str,
    *,
    edits: dict[str, Any],
    expected_revision: int,
) -> ProfileDraftView:
    """用户直接编辑草稿。模型晚返回时草稿 revision 不匹配的结果会被拒绝（03 第 2 节）。"""
    with db.write() as session:
        goal = _get_goal(session, user, goal_id)
        draft = session.scalars(select(GoalProfileDraft).where(GoalProfileDraft.goal_id == goal.id)).one()
        if draft.revision != expected_revision:
            raise GoalflowError(ErrorCode.REVISION_CONFLICT, "档案草稿已被更新，请刷新后重试")
        content = _loads(draft.content_json)
        source_map = _loads(draft.source_map_json)
        for key, value in edits.items():
            content[key] = value
            source_map[key] = "user_edited"
        draft.content_json = _dumps(content)
        draft.source_map_json = _dumps(source_map)
        draft.revision += 1
        draft.updated_at = _now()
        return _draft_view(goal.id, draft)


def confirm_profile(
    db: Database,
    user: CurrentUser,
    goal_id: str,
    *,
    expected_revision: int,
    idempotency_key: str,
) -> ProfileView:
    """确认档案：创建不可变版本并原子更新 goals 与 planning_session（03 第 2 节）。

    确认前在同一事务里重查阻断项：readiness 为 blocked 时拒绝（CONFIRMATION_REQUIRED）。
    """
    request = IdempotentRequest(
        owner_id=user.user_id,
        operation="goals.confirm_profile",
        key=idempotency_key,
        request_hash=compute_request_hash("goals.confirm_profile", body=None),
    )
    with db.write() as session:
        outcome = run_idempotent(
            session,
            request,
            lambda: _do_confirm_profile(session, user, goal_id, expected_revision),
            now=_now(),
        )
        profile = session.scalars(select(GoalProfile).where(GoalProfile.id == outcome.result.id)).one()
        return _profile_view(profile)


def _do_confirm_profile(
    session: Session, user: CurrentUser, goal_id: str, expected_revision: int
) -> tuple[ResultRef, int]:
    goal = _get_goal(session, user, goal_id)
    draft = session.scalars(select(GoalProfileDraft).where(GoalProfileDraft.goal_id == goal.id)).one()
    if draft.readiness == ProfileDraftReadiness.BLOCKED.value:
        raise GoalflowError(
            ErrorCode.CONFIRMATION_REQUIRED,
            "档案仍存在未解决的矛盾，确认前先处理阻断项",
        )
    if goal.revision != expected_revision:
        raise GoalflowError(ErrorCode.REVISION_CONFLICT, "目标已被更新，请刷新后重试")

    content = _loads(draft.content_json)
    now = _now()
    last_version = session.scalars(
        select(GoalProfile.version_no).where(GoalProfile.goal_id == goal.id).order_by(GoalProfile.version_no.desc())
    ).first()
    profile_id = _uuid()
    session.add(
        GoalProfile(
            id=profile_id,
            owner_id=user.user_id,
            goal_id=goal.id,
            version_no=(last_version or 0) + 1,
            result_definition=str(content.get("result_definition", goal.title)),
            success_criteria_json=_dumps(content.get("success_criteria", [])),
            baseline_json=_dumps(content.get("baseline", {})),
            constraints_json=_dumps(content.get("constraints", {})),
            facts_json=_dumps(content.get("facts", {})),
            confirmed_at=now,
        )
    )
    # 形态由时间边界推导、随档案确认（D12 第 1 节）；领域推断同步落库。
    # 先落档案再改 goals.active_profile_id：外键指向刚插入的行，循环外键下 flush 顺序不可靠。
    session.flush()
    goal.kind = _derive_kind(content, GoalKind(goal.kind)).value
    if isinstance(content.get("domain"), str) and content["domain"] in GoalDomain._value2member_map_:
        goal.domain = content["domain"]
    goal.active_profile_id = profile_id
    goal.revision += 1
    goal.updated_at = now
    planning_session = session.scalars(select(PlanningSession).where(PlanningSession.goal_id == goal.id)).one_or_none()
    if planning_session is not None:
        planning_session.confirmed_profile_id = profile_id
        planning_session.state = "profile_confirmed"
        planning_session.updated_at = now
    return ResultRef("goal_profile", profile_id), 201


# —— 路线（只读与选择；生成作业归 T08）——


def get_current_route_set(db: Database, user: CurrentUser, goal_id: str) -> RouteSetView:
    with db.read() as session:
        goal = _get_goal(session, user, goal_id)
        route_set = session.scalars(
            select(RouteSet)
            .where(RouteSet.goal_id == goal.id, RouteSet.status == "current")
            .order_by(RouteSet.created_at.desc())
        ).first()
        if route_set is None:
            raise GoalflowError(ErrorCode.NOT_FOUND, "当前没有可比较的路线集合")
        routes = session.scalars(select(Route).where(Route.route_set_id == route_set.id)).all()
        return RouteSetView(
            id=route_set.id,
            goal_id=route_set.goal_id,
            profile_id=route_set.profile_id,
            status=route_set.status,
            invalidated_reason=route_set.invalidated_reason,
            planning_revision=route_set.planning_revision,
            availability_revision=route_set.availability_revision,
            routes=[_route_view(route) for route in routes],
            created_at=route_set.created_at,
        )


def _route_view(route: Route) -> RouteView:
    return RouteView(
        id=route.id,
        route_set_id=route.route_set_id,
        status=route.status,
        based_on_route_id=route.based_on_route_id,
        title=route.title,
        approach=route.approach,
        difference_keys=_loads(route.difference_keys_json),
        duration_range=_loads(route.duration_range_json),
        phase_outline=_loads(route.phase_outline_json),
        weekly_minutes=route.weekly_minutes,
        tradeoffs=_loads(route.tradeoffs_json),
        risks=_loads(route.risks_json),
        assumptions=_loads(route.assumptions_json),
        required_resources=_loads(route.resources_json),
        derived_metrics=_loads(route.derived_metrics_json),
        created_at=route.created_at,
    )


def select_route(
    db: Database,
    user: CurrentUser,
    goal_id: str,
    *,
    route_id: str,
    expected_revision: int,
    idempotency_key: str,
) -> RouteSelectionView:
    """记录用户选择；选择动作不创建正式任务（11 号第 7 节）。"""
    request = IdempotentRequest(
        owner_id=user.user_id,
        operation="goals.select_route",
        key=idempotency_key,
        request_hash=compute_request_hash("goals.select_route", body=None),
    )
    with db.write() as session:
        outcome = run_idempotent(
            session,
            request,
            lambda: _do_select_route(session, user, goal_id, route_id, expected_revision),
            now=_now(),
        )
        goal = session.scalars(select(Goal).where(Goal.id == outcome.result.id)).one()
        return RouteSelectionView(goal_id=goal.id, route_id=route_id, selected_at=_now(), revision=goal.revision)


def _do_select_route(
    session: Session, user: CurrentUser, goal_id: str, route_id: str, expected_revision: int
) -> tuple[ResultRef, int]:
    goal = _get_goal(session, user, goal_id)
    if goal.revision != expected_revision:
        raise GoalflowError(ErrorCode.REVISION_CONFLICT, "目标已被更新，请刷新后重试")
    route = session.scalars(
        select(Route).where(Route.id == route_id, Route.owner_id == user.user_id, Route.status == "current")
    ).one_or_none()
    if route is None:
        raise GoalflowError(ErrorCode.NOT_FOUND, "路线不存在或已被替代")
    if route.route_set_id != session.scalar(
        select(RouteSet.id).where(RouteSet.goal_id == goal.id, RouteSet.status == "current")
    ):
        raise GoalflowError(ErrorCode.INPUT_STALE, "路线集合已更新，请重新获取后选择")
    planning_session = session.scalars(select(PlanningSession).where(PlanningSession.goal_id == goal.id)).one_or_none()
    if planning_session is None:
        raise GoalflowError(ErrorCode.NOT_FOUND, "规划会话不存在")
    planning_session.selected_route_id = route.id
    planning_session.state = "route_selected"
    planning_session.updated_at = _now()
    goal.revision += 1
    goal.updated_at = _now()
    return ResultRef("goal", goal.id), 200


# —— 计划草稿（只读、任务编辑与启用见 lifecycle）——


def get_plan_draft(db: Database, user: CurrentUser, goal_id: str) -> PlanDraftView:
    with db.read() as session:
        goal = _get_goal(session, user, goal_id)
        plan = session.scalars(
            select(PlanVersion)
            .where(PlanVersion.goal_id == goal.id, PlanVersion.status == "draft")
            .order_by(PlanVersion.version_no.desc())
        ).first()
        if plan is None:
            raise GoalflowError(ErrorCode.NOT_FOUND, "当前没有计划草稿")
        phases = session.scalars(
            select(PlanPhase).where(PlanPhase.plan_version_id == plan.id).order_by(PlanPhase.rank)
        ).all()
        milestones = session.scalars(
            select(PlanMilestone).where(PlanMilestone.plan_version_id == plan.id).order_by(PlanMilestone.rank)
        ).all()
        tasks = _draft_tasks(session, plan.id)
        return PlanDraftView(
            plan_version_id=plan.id,
            goal_id=plan.goal_id,
            status=plan.status,
            start_date=plan.start_date,
            horizon_end=plan.horizon_end,
            detailed_through_date=plan.detailed_through_date,
            phases=[
                {
                    "phase_key": phase.phase_key,
                    "rank": phase.rank,
                    "title": phase.title,
                    "outcome": phase.outcome,
                    "exit_criteria": _loads(phase.exit_criteria_json),
                    "duration_estimate": _loads(phase.duration_estimate_json),
                }
                for phase in phases
            ],
            milestones=[
                {
                    "milestone_key": milestone.milestone_key,
                    "phase_id": milestone.phase_id,
                    "rank": milestone.rank,
                    "title": milestone.title,
                    "success_criteria": _loads(milestone.success_criteria_json),
                    "target_window": _loads(milestone.target_window_json),
                }
                for milestone in milestones
            ],
            tasks=tasks,
        )


def _draft_tasks(session: Session, plan_version_id: str) -> list[DraftTaskView]:
    """首个有效批次的详细任务。stale 判定依赖 planning revision（T05），接入前恒为 False。"""
    batch = session.scalars(
        select(TaskBatch)
        .where(TaskBatch.plan_version_id == plan_version_id, TaskBatch.status == "active")
        .order_by(TaskBatch.window_start)
    ).first()
    if batch is None:
        return []
    memberships = session.scalars(
        select(PlanTaskMembership).where(PlanTaskMembership.plan_version_id == plan_version_id)
    ).all()
    views: list[DraftTaskView] = []
    window = {"window_start": batch.window_start, "window_end": batch.window_end}
    for membership in memberships:
        spec = session.scalars(select(TaskSpec).where(TaskSpec.id == membership.task_spec_id)).one()
        task = session.scalars(select(Task).where(Task.id == membership.task_id)).one()
        views.append(
            DraftTaskView(
                task_id=task.id,
                spec_no=spec.spec_no,
                title=spec.title,
                executor=spec.executor,
                expected_minutes=spec.expected_minutes,
                minimum_minutes=spec.minimum_minutes,
                maximum_minutes=spec.maximum_minutes,
                can_split=spec.can_split,
                minimum_session_minutes=spec.minimum_session_minutes,
                earliest_date=spec.earliest_date,
                latest_date=spec.latest_date,
                execution_status=task.execution_status,
                phase_id=membership.phase_id,
                milestone_id=membership.milestone_id,
                batch_window=window,
                revision=task.revision,
            )
        )
    return views


def update_draft_task(
    db: Database,
    user: CurrentUser,
    goal_id: str,
    task_id: str,
    *,
    expected_revision: int,
    title: str | None,
    instructions: str | None,
    expected_minutes: int | None,
    minimum_minutes: int | None,
    maximum_minutes: int | None,
) -> DraftTaskView:
    """编辑草稿任务：创建新 task_spec，原版本保留（03 第 3 节）。"""
    with db.write() as session:
        goal = _get_goal(session, user, goal_id)
        plan = session.scalars(
            select(PlanVersion)
            .where(PlanVersion.goal_id == goal.id, PlanVersion.status == "draft")
            .order_by(PlanVersion.version_no.desc())
        ).first()
        if plan is None:
            raise GoalflowError(ErrorCode.NOT_FOUND, "当前没有计划草稿")
        membership = session.scalars(
            select(PlanTaskMembership).where(
                PlanTaskMembership.plan_version_id == plan.id, PlanTaskMembership.task_id == task_id
            )
        ).one_or_none()
        if membership is None:
            raise GoalflowError(ErrorCode.NOT_FOUND, "任务不在当前计划草稿中")
        task = session.scalars(select(Task).where(Task.id == task_id)).one()
        if task.revision != expected_revision:
            raise GoalflowError(ErrorCode.REVISION_CONFLICT, "任务已被更新，请刷新后重试")
        old_spec = session.scalars(select(TaskSpec).where(TaskSpec.id == membership.task_spec_id)).one()

        expected = expected_minutes if expected_minutes is not None else old_spec.expected_minutes
        minimum = minimum_minutes if minimum_minutes is not None else old_spec.minimum_minutes
        maximum = maximum_minutes if maximum_minutes is not None else old_spec.maximum_minutes
        if minimum > expected or expected > maximum:
            raise GoalflowError(ErrorCode.VALIDATION_FAILED, "时间估算需满足 minimum <= expected <= maximum")

        spec_no = session.scalars(
            select(TaskSpec.spec_no).where(TaskSpec.task_id == task_id).order_by(TaskSpec.spec_no.desc())
        ).first()
        now = _now()
        new_spec_id = _uuid()
        session.add(
            TaskSpec(
                id=new_spec_id,
                owner_id=user.user_id,
                task_id=task_id,
                spec_no=(spec_no or 0) + 1,
                title=title if title is not None else old_spec.title,
                instructions=instructions if instructions is not None else old_spec.instructions,
                completion_criteria_json=old_spec.completion_criteria_json,
                executor=old_spec.executor,
                expected_minutes=expected,
                minimum_minutes=minimum,
                maximum_minutes=maximum,
                estimate_confidence=old_spec.estimate_confidence,
                earliest_date=old_spec.earliest_date,
                latest_date=old_spec.latest_date,
                can_split=old_spec.can_split,
                minimum_session_minutes=old_spec.minimum_session_minutes,
                verification_policy=old_spec.verification_policy,
                domain_payload_json=old_spec.domain_payload_json,
                created_at=now,
            )
        )
        # 先落新 spec 再更新归属：循环外键下 UoW 的 UPDATE/INSERT 顺序不可靠，显式 flush。
        session.flush()
        membership.task_spec_id = new_spec_id
        task.revision += 1
        task.updated_at = now
        session.flush()
        return _draft_task_view(session, plan.id, membership, task, new_spec_id)


def _draft_task_view(
    session: Session, plan_version_id: str, membership: PlanTaskMembership, task: Task, spec_id: str
) -> DraftTaskView:
    spec = session.scalars(select(TaskSpec).where(TaskSpec.id == spec_id)).one()
    batch = session.scalars(select(TaskBatch).where(TaskBatch.id == membership.task_batch_id)).one()
    return DraftTaskView(
        task_id=task.id,
        spec_no=spec.spec_no,
        title=spec.title,
        executor=spec.executor,
        expected_minutes=spec.expected_minutes,
        minimum_minutes=spec.minimum_minutes,
        maximum_minutes=spec.maximum_minutes,
        can_split=spec.can_split,
        minimum_session_minutes=spec.minimum_session_minutes,
        earliest_date=spec.earliest_date,
        latest_date=spec.latest_date,
        execution_status=task.execution_status,
        phase_id=membership.phase_id,
        milestone_id=membership.milestone_id,
        batch_window={"window_start": batch.window_start, "window_end": batch.window_end},
        revision=task.revision,
    )
