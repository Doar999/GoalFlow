"""目标生命周期命令（T04 PR-2；17-goal-lifecycle-design.md 实现基线）。

原则：状态转换是显式命令，不是任何统计量的副作用；终态单向；暂停只改变调度参与度，
不改写执行事实。**没有任何路径可以由任务完成率、验证结果或回顾结论自动写入 completed。**

与相邻工作包的两个接缝（交接卡决策 A11、A12），均已随 T05/T06 落地：

- 预算检查：`resolve_shared_budget` 已接入 T05 的排期模块——resume 检查仍是提示层，
  排期计算的冲突原因码是权威层（A11）。
- 暂停影响：`compute_pause_impact` 已接入 T06 的 task_dependencies 真实查询，
  WIRED 恒成立，空列表即"确无影响"（A12）。

`activate_goal` 是模块内部函数：用户面的唯一激活操作是 `activate_plan`
（12 号第 7 节），同一事务内完成目标 draft → active（交接卡决策 A10）。
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from goalflow.auth.service import CurrentUser
from goalflow.contracts.enums import (
    ClosureKind,
    DependencyCheckStatus,
    GoalKind,
    GoalLinkStatus,
    GoalStatus,
    PlanVersionStatus,
    TaskBatchStatus,
    TaskExecutionStatus,
)
from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.contracts.policies import CLOSURE_POLICY_VERSION, CLOSURE_UNDO_WINDOW
from goalflow.db.session import Database
from goalflow.goals.models import (
    Goal,
    GoalProfile,
    GoalProfileDraft,
    PlanningSession,
    PlanTaskMembership,
    PlanVersion,
    Task,
    TaskBatch,
    TaskSpec,
)
from goalflow.goals.service import GoalView, _get_goal, _goal_view, _now, _uuid
from goalflow.idempotency import IdempotentRequest, ResultRef, compute_request_hash, run_idempotent
from goalflow.links.models import GoalLink, TaskDependency


@dataclass(frozen=True)
class SharedBudget:
    """resume 时的共享周预算快照（T05 交付前接缝返回 None，语义为"暂不可用"）。"""

    weekly_capacity_minutes: int
    total_demand_minutes: int

    @property
    def over_by_minutes(self) -> int:
        return max(0, self.total_demand_minutes - self.weekly_capacity_minutes)


@dataclass(frozen=True)
class AffectedDependency:
    """暂停目标时受影响的跨目标依赖项（T06 接入后填充真实数据）。"""

    goal_id: str
    task_id: str
    task_title: str


@dataclass(frozen=True)
class TransitionView:
    goal: GoalView
    affected_dependent_tasks: list[AffectedDependency] = field(default_factory=list)
    dependency_check: DependencyCheckStatus = DependencyCheckStatus.NOT_WIRED


def compute_pause_impact(session: Session, goal_id: str, owner_id: str) -> list[AffectedDependency]:
    """暂停影响的真实计算（T06 回填 T04 决策 A12 接缝）。

    查找"前驱任务属于被暂停目标"的生效依赖边（同计划内边无 link，或跨目标边挂在
    active 关联下；proposed / removed 关联的边不计）：这些边的后继任务在暂停期间
    会因前置无法推进而进入计算态 blocked。进行中与已完成的后继任务同样列出——
    它们是"受影响"事实，如何处理由用户决定。
    """
    rows = session.scalars(
        select(TaskDependency)
        .join(Task, Task.id == TaskDependency.predecessor_task_id)
        .outerjoin(GoalLink, GoalLink.id == TaskDependency.goal_link_id)
        .where(
            TaskDependency.owner_id == owner_id,
            Task.goal_id == goal_id,
            or_(TaskDependency.goal_link_id.is_(None), GoalLink.status == GoalLinkStatus.ACTIVE.value),
        )
    ).all()
    successor_ids = sorted({edge.successor_task_id for edge in rows})
    if not successor_ids:
        return []
    affected: list[AffectedDependency] = []
    for task, spec in session.execute(
        select(Task, TaskSpec).join(TaskSpec, TaskSpec.task_id == Task.id).where(Task.id.in_(successor_ids))
    ).all():
        affected.append(AffectedDependency(goal_id=task.goal_id, task_id=task.id, task_title=spec.title))
    return affected


def resolve_shared_budget(database: Database, user_id: str) -> SharedBudget | None:
    """共享周预算的接缝（A11）：计算委托给 T05 的排期模块，本函数只做形状转换。

    延迟导入避免 goals ↔ scheduling 的模块级依赖（scheduling 引用 goals 的表）。
    用户没有活动目标时返回 None，resume 照常完成状态转换。
    """
    from goalflow.scheduling.service import resolve_shared_budget as _resolve

    snapshot = _resolve(database, user_id)
    if snapshot is None:
        return None
    return SharedBudget(
        weekly_capacity_minutes=snapshot["weekly_capacity_minutes"],
        total_demand_minutes=snapshot["total_demand_minutes"],
    )


def _require_revision(goal: Goal, expected_revision: int) -> None:
    if goal.revision != expected_revision:
        raise GoalflowError(ErrorCode.REVISION_CONFLICT, "目标已被更新，请刷新后重试")


def _require_status(goal: Goal, allowed: set[GoalStatus]) -> GoalStatus:
    current = GoalStatus(goal.status)
    if current not in allowed:
        raise GoalflowError(
            ErrorCode.GOAL_STATE_CONFLICT,
            f"目标当前状态为 {current.value}，不允许该操作",
        )
    return current


def _idempotent_request(user: CurrentUser, operation: str, key: str) -> IdempotentRequest:
    return IdempotentRequest(
        owner_id=user.user_id,
        operation=operation,
        key=key,
        request_hash=compute_request_hash(operation, body=None),
    )


# —— 激活（用户面入口是 activate_plan，见 12 号第 7 节）——


@dataclass(frozen=True)
class ActivationOutcome:
    goal: GoalView
    plan_version_id: str
    batch_window: dict[str, Any]
    activated_at: datetime


def activate_plan(
    db: Database,
    user: CurrentUser,
    goal_id: str,
    *,
    draft_plan_id: str,
    expected_revision: int,
    planning_revision: int | None,
    idempotency_key: str,
) -> ActivationOutcome:
    """幂等启用计划草稿：同一事务内切换计划 active、目标 draft → active（A10）、
    首批 proposed 任务转 pending。预算与跨目标约束的最终校验随 T05/T06 补齐。

    `planning_revision` 为 None 表示调用方无法提供（planning state 归 T05），
    此时跳过 stale 判定；T05 落地后该参数必填并强校验。
    """
    request = _idempotent_request(user, "goals.activate_plan", idempotency_key)
    with db.write() as session:
        outcome = run_idempotent(
            session,
            request,
            lambda: _do_activate_plan(session, user, goal_id, draft_plan_id, expected_revision, planning_revision),
            now=_now(),
        )
        goal = session.scalars(select(Goal).where(Goal.id == outcome.result.id)).one()
        return ActivationOutcome(
            goal=_goal_view(goal),
            plan_version_id=goal.current_plan_version_id or "",
            batch_window=_active_batch_window(session, goal.current_plan_version_id),
            activated_at=_now(),
        )


def _active_batch_window(session: Session, plan_version_id: str | None) -> dict[str, Any]:
    if plan_version_id is None:
        return {}
    batch = session.scalars(
        select(TaskBatch)
        .where(TaskBatch.plan_version_id == plan_version_id, TaskBatch.status == TaskBatchStatus.ACTIVE.value)
        .order_by(TaskBatch.window_start)
    ).first()
    if batch is None:
        return {}
    return {"window_start": batch.window_start, "window_end": batch.window_end}


def _do_activate_plan(
    session: Session,
    user: CurrentUser,
    goal_id: str,
    draft_plan_id: str,
    expected_revision: int,
    planning_revision: int | None,
) -> tuple[ResultRef, int]:
    goal = _get_goal(session, user, goal_id)
    _require_revision(goal, expected_revision)
    _require_status(goal, {GoalStatus.DRAFT, GoalStatus.ACTIVE})
    plan = session.scalars(
        select(PlanVersion).where(
            PlanVersion.id == draft_plan_id,
            PlanVersion.owner_id == user.user_id,
            PlanVersion.goal_id == goal.id,
        )
    ).one_or_none()
    if plan is None:
        raise GoalflowError(ErrorCode.NOT_FOUND, "计划草稿不存在")
    if plan.status != PlanVersionStatus.DRAFT.value:
        raise GoalflowError(ErrorCode.GOAL_STATE_CONFLICT, "只有 draft 计划可以启用")

    # 同一目标旧的活动版本让位（03 第 1 节：一个目标最多一个当前执行版本）。
    for old in session.scalars(
        select(PlanVersion).where(PlanVersion.goal_id == goal.id, PlanVersion.status == PlanVersionStatus.ACTIVE.value)
    ).all():
        old.status = PlanVersionStatus.SUPERSEDED.value

    plan.status = PlanVersionStatus.ACTIVE.value
    goal.current_plan_version_id = plan.id
    if goal.status == GoalStatus.DRAFT.value:
        # 同一事务内完成目标 draft → active（A10）；不存在"计划已启用而目标仍为 draft"的中间态。
        goal.status = GoalStatus.ACTIVE.value
    goal.revision += 1
    goal.updated_at = _now()

    # 首批 proposed 任务切换为 pending（R05：草稿不进入正式待办，启用即进入）。
    batch = session.scalars(
        select(TaskBatch)
        .where(TaskBatch.plan_version_id == plan.id, TaskBatch.status == TaskBatchStatus.ACTIVE.value)
        .order_by(TaskBatch.window_start)
    ).first()
    if batch is not None:
        memberships = session.scalars(
            select(PlanTaskMembership).where(PlanTaskMembership.plan_version_id == plan.id)
        ).all()
        for membership in memberships:
            task = session.scalars(select(Task).where(Task.id == membership.task_id)).one()
            if task.execution_status == TaskExecutionStatus.PROPOSED.value:
                task.execution_status = TaskExecutionStatus.PENDING.value
    return ResultRef("goal", goal.id), 200


# —— 暂停 / 恢复 ——


def pause_goal(
    db: Database,
    user: CurrentUser,
    goal_id: str,
    *,
    expected_revision: int,
    reason: str | None,
    idempotency_key: str,
) -> TransitionView:
    """active → paused。不改写任何任务状态；释放周投入由排期器按 revision 递增重算（D12 第 5 节）。"""
    request = _idempotent_request(user, "goals.pause", idempotency_key)
    with db.write() as session:
        outcome = run_idempotent(
            session,
            request,
            lambda: _do_pause(session, user, goal_id, expected_revision, reason),
            now=_now(),
        )
        goal = session.scalars(select(Goal).where(Goal.id == outcome.result.id)).one()
        affected = compute_pause_impact(session, goal.id, user.user_id)
        return TransitionView(
            goal=_goal_view(goal),
            affected_dependent_tasks=affected,
            # T06 回填后依赖校验已真实执行：空列表即"确无影响"（T04 决策 A12 兑现）。
            dependency_check=DependencyCheckStatus.WIRED,
        )


def _do_pause(
    session: Session, user: CurrentUser, goal_id: str, expected_revision: int, reason: str | None
) -> tuple[ResultRef, int]:
    goal = _get_goal(session, user, goal_id)
    _require_revision(goal, expected_revision)
    _require_status(goal, {GoalStatus.ACTIVE})
    now = _now()
    goal.status = GoalStatus.PAUSED.value
    goal.paused_at = now
    goal.pause_reason = reason
    goal.revision += 1
    goal.updated_at = now
    # 不触碰任何 tasks 行的执行状态——进行中的任务保持进行中（D12 第 5 节）。
    return ResultRef("goal", goal.id), 200


def resume_goal(
    db: Database,
    user: CurrentUser,
    goal_id: str,
    *,
    expected_revision: int,
    idempotency_key: str,
) -> TransitionView:
    """paused → active。共享预算被占满时返回 BUDGET_CONFLICT 供取舍（D12 第 5 节）。

    预算快照在事务外取得（T16：写事务里不做业务计算；快照随后在事务内作参考）。
    接缝已接入 T05：共享周预算被活动目标占满时返回 BUDGET_CONFLICT 供取舍（A11）。
    """
    request = _idempotent_request(user, "goals.resume", idempotency_key)
    # 预算快照在事务外取得（T16：写事务里不做业务计算；快照随后在事务内作参考）。
    budget = resolve_shared_budget(db, user.user_id)
    with db.write() as session:
        outcome = run_idempotent(
            session,
            request,
            lambda: _do_resume(session, user, goal_id, expected_revision, budget),
            now=_now(),
        )
        goal = session.scalars(select(Goal).where(Goal.id == outcome.result.id)).one()
        return TransitionView(goal=_goal_view(goal))


def _do_resume(
    session: Session,
    user: CurrentUser,
    goal_id: str,
    expected_revision: int,
    budget: SharedBudget | None,
) -> tuple[ResultRef, int]:
    goal = _get_goal(session, user, goal_id)
    _require_revision(goal, expected_revision)
    _require_status(goal, {GoalStatus.PAUSED})
    if budget is not None and budget.over_by_minutes > 0:
        raise GoalflowError(
            ErrorCode.BUDGET_CONFLICT,
            "共享周预算已被其他目标占满，恢复前请先取舍",
            details={"over_by_minutes": budget.over_by_minutes},
        )
    now = _now()
    goal.status = GoalStatus.ACTIVE.value
    # 暂停字段随恢复清空：它们描述"当前这次暂停"，历史在审计与日志里。
    goal.paused_at = None
    goal.pause_reason = None
    goal.revision += 1
    goal.updated_at = now
    return ResultRef("goal", goal.id), 200


# —— 结束 / 撤销 / 派生 ——


def close_goal(
    db: Database,
    user: CurrentUser,
    goal_id: str,
    *,
    expected_revision: int,
    closure_kind: ClosureKind,
    note: str | None,
    criteria_confirmations: list[dict[str, Any]],
    idempotency_key: str,
) -> GoalView:
    """active → completed / stopped；paused 只能 stopped。终态不可逆，撤销窗口内可补偿（D12 第 6 节）。"""
    request = _idempotent_request(user, "goals.close", idempotency_key)
    with db.write() as session:
        outcome = run_idempotent(
            session,
            request,
            lambda: _do_close(session, user, goal_id, expected_revision, closure_kind, note, criteria_confirmations),
            now=_now(),
        )
        goal = session.scalars(select(Goal).where(Goal.id == outcome.result.id)).one()
        return _goal_view(goal)


def _do_close(
    session: Session,
    user: CurrentUser,
    goal_id: str,
    expected_revision: int,
    closure_kind: ClosureKind,
    note: str | None,
    criteria_confirmations: list[dict[str, Any]],
) -> tuple[ResultRef, int]:
    goal = _get_goal(session, user, goal_id)
    _require_revision(goal, expected_revision)
    current = _require_status(goal, {GoalStatus.ACTIVE, GoalStatus.PAUSED})
    if closure_kind == ClosureKind.COMPLETED:
        # completed 仅对达成型开放，且只允许从 active 进入（17 号第 2 节）。
        if goal.kind != GoalKind.ACHIEVEMENT.value:
            raise GoalflowError(ErrorCode.GOAL_STATE_CONFLICT, "维持型目标不会完成，只能标记不再需要（stopped）")
        if current is GoalStatus.PAUSED:
            raise GoalflowError(ErrorCode.GOAL_STATE_CONFLICT, "已暂停的目标须先恢复再标记完成")
    snapshot = {
        "policy_version": CLOSURE_POLICY_VERSION,
        "pre_closure_status": current.value,
        "criteria": criteria_confirmations,
    }
    now = _now()
    goal.status = GoalStatus.COMPLETED.value if closure_kind == ClosureKind.COMPLETED else GoalStatus.STOPPED.value
    goal.closed_at = now
    goal.closure_kind = closure_kind.value
    goal.closure_note = note
    goal.closure_criteria_snapshot_json = json.dumps(snapshot, ensure_ascii=False)
    goal.revision += 1
    goal.updated_at = now
    return ResultRef("goal", goal.id), 200


def _closure_snapshot(goal: Goal) -> dict[str, Any]:
    if goal.closure_criteria_snapshot_json is None:
        # 库级约束保证 closure_kind 与快照同生共死；到这里为空说明数据被绕过应用写入。
        raise GoalflowError(ErrorCode.INTERNAL_ERROR, "结束快照缺失，无法执行撤销")
    snapshot: dict[str, Any] = json.loads(goal.closure_criteria_snapshot_json)
    return snapshot


def undo_closure(
    db: Database,
    user: CurrentUser,
    goal_id: str,
    *,
    expected_revision: int,
    idempotency_key: str,
) -> GoalView:
    """撤销窗口内的补偿操作：恢复结束前状态并保留结束记录的审计痕迹（17 号第 4 节）。"""
    request = _idempotent_request(user, "goals.undo_closure", idempotency_key)
    with db.write() as session:
        outcome = run_idempotent(
            session,
            request,
            lambda: _do_undo_closure(session, user, goal_id, expected_revision),
            now=_now(),
        )
        goal = session.scalars(select(Goal).where(Goal.id == outcome.result.id)).one()
        return _goal_view(goal)


def _do_undo_closure(
    session: Session, user: CurrentUser, goal_id: str, expected_revision: int
) -> tuple[ResultRef, int]:
    goal = _get_goal(session, user, goal_id)
    _require_revision(goal, expected_revision)
    _require_status(goal, {GoalStatus.COMPLETED, GoalStatus.STOPPED})
    closed_at = goal.closed_at
    if closed_at is None or _now() - closed_at >= CLOSURE_UNDO_WINDOW:
        raise GoalflowError(
            ErrorCode.GOAL_STATE_CONFLICT,
            f"已超过 {int(CLOSURE_UNDO_WINDOW.total_seconds() // 3600)} 小时撤销窗口；可以'以此为起点新建'",
        )
    snapshot = _closure_snapshot(goal)
    goal.status = snapshot.get("pre_closure_status", GoalStatus.ACTIVE.value)
    goal.closed_at = None
    goal.closure_kind = None
    goal.closure_note = None
    goal.closure_criteria_snapshot_json = None
    goal.revision += 1
    goal.updated_at = _now()
    return ResultRef("goal", goal.id), 200


def derive_goal(
    db: Database,
    user: CurrentUser,
    goal_id: str,
    *,
    expected_revision: int,
    idempotency_key: str,
) -> GoalView:
    """以此为起点新建：复制源目标最新档案内容为草稿；不复制计划、任务与执行记录（17 号第 4 节）。"""
    request = _idempotent_request(user, "goals.derive", idempotency_key)
    with db.write() as session:
        outcome = run_idempotent(
            session,
            request,
            lambda: _do_derive(session, user, goal_id, expected_revision),
            now=_now(),
        )
        goal = session.scalars(select(Goal).where(Goal.id == outcome.result.id)).one()
        return _goal_view(goal)


def _do_derive(session: Session, user: CurrentUser, goal_id: str, expected_revision: int) -> tuple[ResultRef, int]:
    source = _get_goal(session, user, goal_id)
    _require_revision(source, expected_revision)
    _require_status(source, {GoalStatus.COMPLETED, GoalStatus.STOPPED})

    now = _now()
    new_goal_id = _uuid()
    draft_id = _uuid()
    session.add(
        Goal(
            id=new_goal_id,
            owner_id=user.user_id,
            title=source.title,
            domain=source.domain,
            domain_confidence=source.domain_confidence,
            kind=source.kind,
            status=GoalStatus.DRAFT.value,
            review_period=source.review_period,
            source_goal_id=source.id,
            revision=0,
            created_at=now,
            updated_at=now,
        )
    )
    # 循环外键下 UoW 的插入顺序不可靠：goal 先落，draft 与会话才能引用它。
    session.flush()
    content: dict[str, Any] = {"derived_from_goal_id": source.id}
    profile = None
    if source.active_profile_id is not None:
        profile = session.scalars(select(GoalProfile).where(GoalProfile.id == source.active_profile_id)).one_or_none()
    if profile is not None:
        content.update(
            {
                "time_boundary": _time_boundary_by_kind(GoalKind(source.kind)),
                "domain": source.domain,
                "result_definition": profile.result_definition,
                "success_criteria": json.loads(profile.success_criteria_json),
                "baseline": json.loads(profile.baseline_json),
                "constraints": json.loads(profile.constraints_json),
                "facts": json.loads(profile.facts_json),
            }
        )
    source_map = dict.fromkeys(content, "derived")
    session.add(
        GoalProfileDraft(
            id=draft_id,
            owner_id=user.user_id,
            goal_id=new_goal_id,
            content_json=json.dumps(content, ensure_ascii=False),
            source_map_json=json.dumps(source_map, ensure_ascii=False),
            gaps_json="[]",
            assumptions_json="[]",
            contradictions_json="[]",
            readiness="needs_input",
            revision=0,
            created_at=now,
            updated_at=now,
        )
    )
    session.add(
        PlanningSession(
            id=_uuid(),
            owner_id=user.user_id,
            goal_id=new_goal_id,
            state="derived",
            profile_draft_id=draft_id,
            revision=0,
            created_at=now,
            updated_at=now,
        )
    )
    # 源目标保持终态，不动任何字段；派生关系由新目标的 source_goal_id 与审计承接。
    return ResultRef("goal", new_goal_id), 201


def _time_boundary_by_kind(kind: GoalKind) -> str:
    return "ongoing_maintenance" if kind is GoalKind.MAINTENANCE else "fixed_date"
