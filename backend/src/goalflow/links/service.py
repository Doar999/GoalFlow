"""目标关联与任务依赖的业务命令（T06 PR-2；18-goal-link-design.md 实现基线）。

命令边界（18 号第 1 节）：propose_goal_link → confirm_goal_link 建立关联；
propose_unlink → accept/reject 解除关联。原则：**不存在"关联已解除但依赖边仍在"的
中间状态**——解除是提案加原子应用，不是先改标签再清理。

与数据模型的衔接：

- 依赖边只在确认时建立（03 第 57 行"跨目标依赖仅在已确认关联下建立"），
  propose 阶段只创建 proposed 关联行；confirm 请求携带同一组任务关系并在此事务内校验落库。
- 无环校验在用户全部依赖边上做（含同计划内边与跨目标边）：在图上找到的环就是真环，
  全图检查比"只查涉及的 plan_version"更保守，不会漏报（03 第 57 行要求覆盖其他目标的当前版本）。
- 解除提案的影响分析里 criteria_unsatisfiable 首版结构性为空：产品 13 号第 3 节明确
  首版不支持成果复用，完成标准无法引用前驱产出；字段与接受时的取消路径保留，
  供后续引入产出引用时填充（T06 交接卡决策 A8）。
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from goalflow.auth.service import CurrentUser
from goalflow.contracts.enums import (
    ChangeClass,
    ChangeProposalStatus,
    DependencyOutcome,
    GoalLinkStatus,
    TaskExecutionStatus,
)
from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.db.session import Database
from goalflow.goals.models import (
    Goal,
    PlanMilestone,
    PlanTaskMembership,
    Task,
    TaskSpec,
)
from goalflow.goals.service import _now, _uuid
from goalflow.idempotency import IdempotentRequest, ResultRef, compute_request_hash, run_idempotent
from goalflow.links.models import ChangeProposal, GoalLink, TaskDependency
from goalflow.scheduling.models import UserPlanningState

_ENABLED_LINK_STATUSES = (GoalLinkStatus.PROPOSED.value, GoalLinkStatus.ACTIVE.value)


@dataclass(frozen=True)
class DependencyEdgeCommand:
    """确认关联时携带的一条任务依赖（18 号第 2 节）。"""

    predecessor_task_id: str
    successor_task_id: str
    required_outcome: str


@dataclass(frozen=True)
class GoalLinkView:
    """关联当前状态，路由层从这里组装响应。"""

    id: str
    goal_a_id: str
    goal_b_id: str
    status: str
    confirmed_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class UnlinkImpact:
    """解除提案的影响分析（18 号第 3 节）。条目用 dict 承载，结构随字段说明固定。"""

    unsatisfied_edges: list[dict[str, Any]] = field(default_factory=list)
    criteria_unsatisfiable: list[dict[str, Any]] = field(default_factory=list)
    affected_milestones: list[dict[str, Any]] = field(default_factory=list)
    satisfied_edges: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ChangeProposalView:
    """变更提案视图。input_revision 落后于当前 planning revision 即为 stale。"""

    id: str
    goal_id: str
    change_class: str
    status: str
    input_revision: int
    impact: UnlinkImpact
    reason: str | None
    created_at: datetime
    accepted_at: datetime | None
    applied_at: datetime | None


def _idempotent_request(user: CurrentUser, operation: str, key: str, body: Any = None) -> IdempotentRequest:
    return IdempotentRequest(
        owner_id=user.user_id,
        operation=operation,
        key=key,
        request_hash=compute_request_hash(operation, body=body),
    )


def _get_link(session: Session, user: CurrentUser, link_id: str) -> GoalLink:
    link = session.scalars(
        select(GoalLink).where(GoalLink.id == link_id, GoalLink.owner_id == user.user_id)
    ).one_or_none()
    if link is None:
        raise GoalflowError(ErrorCode.NOT_FOUND, "目标关联不存在")
    return link


def _get_proposal(session: Session, user: CurrentUser, proposal_id: str) -> ChangeProposal:
    proposal = session.scalars(
        select(ChangeProposal).where(ChangeProposal.id == proposal_id, ChangeProposal.owner_id == user.user_id)
    ).one_or_none()
    if proposal is None:
        raise GoalflowError(ErrorCode.NOT_FOUND, "变更提案不存在")
    return proposal


def _get_task(session: Session, user: CurrentUser, task_id: str) -> Task:
    task = session.scalars(select(Task).where(Task.id == task_id, Task.owner_id == user.user_id)).one_or_none()
    if task is None:
        raise GoalflowError(ErrorCode.NOT_FOUND, f"任务 {task_id} 不存在")
    return task


def _link_view(link: GoalLink) -> GoalLinkView:
    return GoalLinkView(
        id=link.id,
        goal_a_id=link.goal_a_id,
        goal_b_id=link.goal_b_id,
        status=link.status,
        confirmed_at=link.confirmed_at,
        created_at=link.created_at,
        updated_at=link.updated_at,
    )


def _impact_of(proposal: ChangeProposal) -> UnlinkImpact:
    raw: dict[str, Any] = _loads_json(proposal.impact_json)
    return UnlinkImpact(
        unsatisfied_edges=raw.get("unsatisfied_edges", []),
        criteria_unsatisfiable=raw.get("criteria_unsatisfiable", []),
        affected_milestones=raw.get("affected_milestones", []),
        satisfied_edges=raw.get("satisfied_edges", []),
    )


def _proposal_view(proposal: ChangeProposal) -> ChangeProposalView:
    return ChangeProposalView(
        id=proposal.id,
        goal_id=proposal.goal_id,
        change_class=proposal.change_class,
        status=proposal.status,
        input_revision=proposal.input_revision,
        impact=_impact_of(proposal),
        reason=proposal.reason,
        created_at=proposal.created_at,
        accepted_at=proposal.accepted_at,
        applied_at=proposal.applied_at,
    )


def _loads_json(raw: str) -> Any:
    return json.loads(raw)


def _dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _get_or_create_planning_state(session: Session, owner_id: str) -> UserPlanningState:
    """与 scheduling.service 的同名函数语义一致；本地副本避免 links → scheduling 模块依赖。"""
    state = session.scalar(select(UserPlanningState).where(UserPlanningState.owner_id == owner_id))
    if state is None:
        now = _now()
        state = UserPlanningState(owner_id=owner_id, revision=0, created_at=now, updated_at=now)
        session.add(state)
        session.flush()
    return state


def _bump_planning_revision(session: Session, owner_id: str) -> None:
    """依赖图变化影响排期输入（后继任务的候选资格），递增协调入口 revision（03 第 4 节）。"""
    state = _get_or_create_planning_state(session, owner_id)
    state.revision += 1
    state.updated_at = _now()


def _cycle_exists(session: Session, owner_id: str, extra_edges: list[tuple[str, str]]) -> bool:
    """在用户全部依赖边 + 候选新边上做 DFS 判环。自依赖边已被 DB CHECK 拦截。"""
    rows = session.execute(
        select(TaskDependency.predecessor_task_id, TaskDependency.successor_task_id).where(
            TaskDependency.owner_id == owner_id
        )
    ).all()
    adjacency: dict[str, list[str]] = {}
    for predecessor, successor in [*rows, *extra_edges]:
        adjacency.setdefault(predecessor, []).append(successor)

    unvisited, visiting, done = 0, 1, 2
    color: dict[str, int] = dict.fromkeys(adjacency, unvisited)

    def visit(node: str) -> bool:
        color[node] = visiting
        for successor in adjacency.get(node, ()):
            state = color.get(successor, unvisited)
            if state == visiting:
                return True
            if state == unvisited and visit(successor):
                return True
        color[node] = done
        return False

    return any(color[node] == unvisited and visit(node) for node in list(adjacency))


def _validate_edges_cross_goal(
    session: Session, user: CurrentUser, goal_id: str, target_goal_id: str, dependencies: list[DependencyEdgeCommand]
) -> list[tuple[str, str]]:
    """校验每条边跨越关联的两个目标，返回候选 (前驱, 后继) 对。"""
    pair = {goal_id, target_goal_id}
    candidate: list[tuple[str, str]] = []
    for edge in dependencies:
        predecessor = _get_task(session, user, edge.predecessor_task_id)
        successor = _get_task(session, user, edge.successor_task_id)
        if predecessor.goal_id == successor.goal_id or {predecessor.goal_id, successor.goal_id} != pair:
            raise GoalflowError(
                ErrorCode.VALIDATION_FAILED,
                "任务依赖必须从其中一个目标的任务指向另一个目标的任务（18 号第 2 节）",
            )
        candidate.append((predecessor.id, successor.id))
    return candidate


def _require_dependency_outcome(edge: DependencyEdgeCommand) -> None:
    if edge.required_outcome not in {member.value for member in DependencyOutcome}:
        raise GoalflowError(ErrorCode.VALIDATION_FAILED, f"未知的依赖满足条件 {edge.required_outcome!r}")


# —— 建立关联 ——


def propose_goal_link(
    db: Database,
    user: CurrentUser,
    goal_id: str,
    *,
    target_goal_id: str,
    dependencies: list[DependencyEdgeCommand],
    idempotency_key: str,
) -> GoalLinkView:
    """提出关联：创建 proposed 关联行。必须携带具体任务关系，确认前不建立依赖边（03 第 57 行）。"""
    request = _idempotent_request(user, "links.propose_goal_link", idempotency_key, body=None)
    with db.write() as session:
        outcome = run_idempotent(
            session,
            request,
            lambda: _do_propose(session, user, goal_id, target_goal_id, dependencies),
            now=_now(),
        )
        link = session.scalars(select(GoalLink).where(GoalLink.id == outcome.result.id)).one()
        return _link_view(link)


def _do_propose(
    session: Session,
    user: CurrentUser,
    goal_id: str,
    target_goal_id: str,
    dependencies: list[DependencyEdgeCommand],
) -> tuple[ResultRef, int]:
    goal = session.scalars(select(Goal).where(Goal.id == goal_id, Goal.owner_id == user.user_id)).one_or_none()
    target = session.scalars(select(Goal).where(Goal.id == target_goal_id, Goal.owner_id == user.user_id)).one_or_none()
    if goal is None or target is None:
        raise GoalflowError(ErrorCode.NOT_FOUND, "目标不存在")
    if goal.id == target.id:
        raise GoalflowError(ErrorCode.VALIDATION_FAILED, "不能与目标自身建立关联")
    if not dependencies:
        # 路由层 Pydantic 已限 min_length=1；这里兜底拒绝"只带两个 goal_id 的相关声明"。
        raise GoalflowError(ErrorCode.VALIDATION_FAILED, "关联必须携带具体的任务依赖关系")

    for edge in dependencies:
        _require_dependency_outcome(edge)
    candidate = _validate_edges_cross_goal(session, user, goal.id, target.id, dependencies)

    goal_a_id, goal_b_id = sorted((goal.id, target.id))
    existing = session.scalars(
        select(GoalLink).where(
            GoalLink.owner_id == user.user_id,
            GoalLink.goal_a_id == goal_a_id,
            GoalLink.goal_b_id == goal_b_id,
            GoalLink.status.in_(_ENABLED_LINK_STATUSES),
        )
    ).first()
    if existing is not None:
        raise GoalflowError(ErrorCode.VALIDATION_FAILED, "这两个目标已存在未失效的关联")

    if _cycle_exists(session, user.user_id, candidate):
        raise GoalflowError(ErrorCode.DEPENDENCY_CYCLE, "这组任务关系会与其他目标的依赖形成环")

    now = _now()
    link = GoalLink(
        id=_uuid(),
        owner_id=user.user_id,
        goal_a_id=goal_a_id,
        goal_b_id=goal_b_id,
        status=GoalLinkStatus.PROPOSED.value,
        created_at=now,
        updated_at=now,
    )
    session.add(link)
    session.flush()
    return ResultRef("goal_link", link.id), 201


def confirm_goal_link(
    db: Database,
    user: CurrentUser,
    link_id: str,
    *,
    dependencies: list[DependencyEdgeCommand],
    idempotency_key: str,
) -> GoalLinkView:
    """确认建立：同一事务内校验同用户/不自关联/目标对不重复/全图无环，并创建依赖边（18 号第 2 节）。"""
    request = _idempotent_request(user, "links.confirm_goal_link", idempotency_key, body=None)
    with db.write() as session:
        outcome = run_idempotent(
            session,
            request,
            lambda: _do_confirm(session, user, link_id, dependencies),
            now=_now(),
        )
        link = session.scalars(select(GoalLink).where(GoalLink.id == outcome.result.id)).one()
        return _link_view(link)


def _do_confirm(
    session: Session, user: CurrentUser, link_id: str, dependencies: list[DependencyEdgeCommand]
) -> tuple[ResultRef, int]:
    link = _get_link(session, user, link_id)
    if link.status != GoalLinkStatus.PROPOSED.value:
        raise GoalflowError(
            ErrorCode.GOAL_STATE_CONFLICT,
            f"关联当前状态为 {link.status}，只有 proposed 关联可以确认",
        )
    if not dependencies:
        raise GoalflowError(ErrorCode.VALIDATION_FAILED, "确认必须携带与提案一致的任务依赖关系")
    for edge in dependencies:
        _require_dependency_outcome(edge)
    candidate = _validate_edges_cross_goal(session, user, link.goal_a_id, link.goal_b_id, dependencies)

    if _cycle_exists(session, user.user_id, candidate):
        raise GoalflowError(ErrorCode.DEPENDENCY_CYCLE, "这组任务关系会与其他目标的依赖形成环")

    now = _now()
    for edge, (predecessor_id, successor_id) in zip(dependencies, candidate, strict=True):
        session.add(
            TaskDependency(
                id=_uuid(),
                owner_id=user.user_id,
                plan_version_id=_plan_version_of(session, user, successor_id),
                predecessor_task_id=predecessor_id,
                successor_task_id=successor_id,
                required_outcome=edge.required_outcome,
                goal_link_id=link.id,
                created_at=now,
            )
        )
    link.status = GoalLinkStatus.ACTIVE.value
    link.confirmed_at = now
    link.updated_at = now
    _bump_planning_revision(session, user.user_id)
    return ResultRef("goal_link", link.id), 200


def _plan_version_of(session: Session, user: CurrentUser, successor_id: str) -> str:
    """依赖边的 plan_version_id 取后继任务所属计划版本（边由后继任务的计划管理，03 第 57 行）。

    前驱任务的归属与存在性已由 _validate_edges_cross_goal 校验；跨目标边的前驱与后继
    本就分属两个计划版本，唯一性键里的 plan_version_id 取后继一方。
    """
    membership = session.scalars(
        select(PlanTaskMembership).where(
            PlanTaskMembership.owner_id == user.user_id,
            PlanTaskMembership.task_id == successor_id,
        )
    ).first()
    if membership is None:
        # 后继任务必须已归属某个计划版本；任务还没进计划的关联在生成计划后才能确认。
        raise GoalflowError(
            ErrorCode.VALIDATION_FAILED,
            f"后继任务 {successor_id} 尚未归属任何计划版本，无法建立依赖边",
        )
    return membership.plan_version_id


# —— 解除关联 ——


def propose_unlink(db: Database, user: CurrentUser, link_id: str, *, idempotency_key: str) -> ChangeProposalView:
    """生成解除提案及影响分析；操作不被未完成依赖阻止（产品 13 号第 4 节）。"""
    request = _idempotent_request(user, "links.propose_unlink", idempotency_key, body=None)
    with db.write() as session:
        outcome = run_idempotent(
            session,
            request,
            lambda: _do_propose_unlink(session, user, link_id),
            now=_now(),
        )
        proposal = session.scalars(select(ChangeProposal).where(ChangeProposal.id == outcome.result.id)).one()
        return _proposal_view(proposal)


def _do_propose_unlink(session: Session, user: CurrentUser, link_id: str) -> tuple[ResultRef, int]:
    link = _get_link(session, user, link_id)
    if link.status != GoalLinkStatus.ACTIVE.value:
        raise GoalflowError(
            ErrorCode.GOAL_STATE_CONFLICT,
            f"关联当前状态为 {link.status}，只有 active 关联可以解除",
        )
    edges = session.scalars(select(TaskDependency).where(TaskDependency.goal_link_id == link.id)).all()
    state = _get_or_create_planning_state(session, user.user_id)

    unsatisfied: list[dict[str, Any]] = []
    satisfied: list[dict[str, Any]] = []
    for edge in edges:
        predecessor = session.scalars(select(Task).where(Task.id == edge.predecessor_task_id)).one()
        successor = session.scalars(select(Task).where(Task.id == edge.successor_task_id)).one()
        entry = {
            "edge_id": edge.id,
            "predecessor_task_id": edge.predecessor_task_id,
            "predecessor_title": _task_title(session, edge.predecessor_task_id),
            "successor_task_id": edge.successor_task_id,
            "successor_title": _task_title(session, edge.successor_task_id),
            "successor_status": successor.execution_status,
            "required_outcome": edge.required_outcome,
        }
        # verification_passed 边在验证记录（T11）落地前视为未满足：不把"未检查"伪装成"已满足"。
        is_satisfied = (
            edge.required_outcome == DependencyOutcome.EXECUTION_COMPLETED.value
            and predecessor.execution_status == TaskExecutionStatus.COMPLETED.value
        )
        (satisfied if is_satisfied else unsatisfied).append(entry)

    impact = UnlinkImpact(
        unsatisfied_edges=unsatisfied,
        # 首版不支持成果复用（产品 13 号第 3 节），完成标准无法引用前驱产出：结构性为空。
        criteria_unsatisfiable=[],
        affected_milestones=_affected_milestones(session, [edge.successor_task_id for edge in edges]),
        satisfied_edges=satisfied,
    )
    now = _now()
    proposal = ChangeProposal(
        id=_uuid(),
        owner_id=user.user_id,
        goal_id=link.goal_a_id,
        base_versions_json=_dumps_json({"goal_link_id": link.id, "goal_pair": [link.goal_a_id, link.goal_b_id]}),
        input_revision=state.revision,
        proposed_patch_json=_dumps_json({"remove_goal_link_id": link.id, "edge_ids": [edge.id for edge in edges]}),
        impact_json=_dumps_json(
            {
                "unsatisfied_edges": impact.unsatisfied_edges,
                "criteria_unsatisfiable": impact.criteria_unsatisfiable,
                "affected_milestones": impact.affected_milestones,
                "satisfied_edges": impact.satisfied_edges,
            }
        ),
        change_class=ChangeClass.CONFIRMATION_REQUIRED.value,
        status=ChangeProposalStatus.PENDING.value,
        created_at=now,
        updated_at=now,
    )
    session.add(proposal)
    session.flush()
    return ResultRef("change_proposal", proposal.id), 201


def _task_title(session: Session, task_id: str) -> str:
    """任务最新规格的标题（task_specs.spec_no 最大者）；无规格时回退任务 id。"""
    spec = session.scalars(
        select(TaskSpec).where(TaskSpec.task_id == task_id).order_by(TaskSpec.spec_no.desc()).limit(1)
    ).first()
    return spec.title if spec is not None else task_id


def _affected_milestones(session: Session, successor_task_ids: list[str]) -> list[dict[str, Any]]:
    """受影响边后继任务所属的里程碑与目标窗口（18 号第 3 节第 4 项）。"""
    if not successor_task_ids:
        return []
    rows = session.execute(
        select(PlanTaskMembership, PlanMilestone)
        .join(PlanMilestone, PlanMilestone.id == PlanTaskMembership.milestone_id)
        .where(PlanTaskMembership.task_id.in_(successor_task_ids))
    ).all()
    milestones: dict[str, dict[str, Any]] = {}
    for membership, milestone in rows:
        entry = milestones.setdefault(
            milestone.id,
            {
                "milestone_id": milestone.id,
                "title": milestone.title,
                "target_window": _loads_json(milestone.target_window_json),
                "task_ids": [],
            },
        )
        entry["task_ids"].append(membership.task_id)
    return list(milestones.values())


def get_change_proposal(db: Database, user: CurrentUser, proposal_id: str) -> ChangeProposalView:
    """读取提案与影响分析（T06 契约决策 A7）。"""
    with db.read() as session:
        proposal = _get_proposal(session, user, proposal_id)
        return _proposal_view(proposal)


def accept_change_proposal(
    db: Database, user: CurrentUser, proposal_id: str, *, idempotency_key: str
) -> ChangeProposalView:
    """接受并在同一事务内原子应用（18 号第 3 节）。

    原子序列：置关联 removed → 删除该关联下依赖边 → 处理 criteria_unsatisfiable 的
    未开始任务（首版结构性为空，路径保留）→ 置提案 applied → 递增 planning revision。
    提案基础版本已变（input_revision 落后）时先在独立事务里把提案置 stale 并提交，
    再抛 INPUT_STALE——stale 标记必须真实落库，不能随异常回滚消失（18 号第 3 节）。
    """
    request = _idempotent_request(user, "links.accept_change_proposal", idempotency_key, body=None)
    stale_details = _mark_proposal_stale_if_outdated(db, user, proposal_id)
    if stale_details is not None:
        raise GoalflowError(ErrorCode.INPUT_STALE, "提案生成后排期输入已变化，请重新生成提案", details=stale_details)
    with db.write() as session:
        outcome = run_idempotent(
            session,
            request,
            lambda: _do_accept(session, user, proposal_id),
            now=_now(),
        )
        proposal = session.scalars(select(ChangeProposal).where(ChangeProposal.id == outcome.result.id)).one()
        return _proposal_view(proposal)


def _mark_proposal_stale_if_outdated(db: Database, user: CurrentUser, proposal_id: str) -> dict[str, int] | None:
    """pending 提案的 input_revision 落后时，独立事务置 stale 并提交，返回给客户端的差异详情。"""
    with db.write() as session:
        proposal = _get_proposal(session, user, proposal_id)
        if proposal.status != ChangeProposalStatus.PENDING.value:
            return None
        state = _get_or_create_planning_state(session, user.user_id)
        if state.revision == proposal.input_revision:
            return None
        proposal.status = ChangeProposalStatus.STALE.value
        proposal.updated_at = _now()
        return {"input_revision": proposal.input_revision, "current_revision": state.revision}


def _do_accept(session: Session, user: CurrentUser, proposal_id: str) -> tuple[ResultRef, int]:
    proposal = _get_proposal(session, user, proposal_id)
    if proposal.status == ChangeProposalStatus.STALE.value:
        raise GoalflowError(ErrorCode.INPUT_STALE, "提案已过期，请重新生成并查看新差异")
    if proposal.status != ChangeProposalStatus.PENDING.value:
        raise GoalflowError(
            ErrorCode.GOAL_STATE_CONFLICT,
            f"提案当前状态为 {proposal.status}，只有 pending 提案可以接受",
        )
    state = _get_or_create_planning_state(session, user.user_id)
    if state.revision != proposal.input_revision:
        # 竞态窗口（预检后 revision 又变了）：本事务会回滚，stale 标记不落库，
        # 客户端看到的仍是 pending——重新生成提案后自然解决。
        raise GoalflowError(
            ErrorCode.INPUT_STALE,
            "提案生成后排期输入已变化，请重新生成提案",
            details={"input_revision": proposal.input_revision, "current_revision": state.revision},
        )

    patch: dict[str, Any] = _loads_json(proposal.proposed_patch_json)
    impact: dict[str, Any] = _loads_json(proposal.impact_json)
    link_id = patch.get("remove_goal_link_id")
    link = session.scalars(
        select(GoalLink).where(GoalLink.id == link_id, GoalLink.owner_id == user.user_id)
    ).one_or_none()
    if link is None:
        raise GoalflowError(ErrorCode.NOT_FOUND, "提案指向的目标关联不存在")
    if link.status != GoalLinkStatus.ACTIVE.value:
        raise GoalflowError(
            ErrorCode.GOAL_STATE_CONFLICT,
            f"关联当前状态为 {link.status}，与提案生成时不一致，请重新生成提案",
        )

    now = _now()
    # 同一事务内原子执行（18 号第 3 节）：任何一步失败，整个事务回滚，两者都不变。
    link.status = GoalLinkStatus.REMOVED.value
    link.updated_at = now
    session.execute(delete(TaskDependency).where(TaskDependency.goal_link_id == link.id))

    # criteria_unsatisfiable 的未开始任务：显式取消并记录原因（18 号第 3 节）。
    # 首版该列表结构性为空；路径保留，供后续引入产出引用后填充。
    for entry in impact.get("criteria_unsatisfiable", []):
        task = session.scalars(select(Task).where(Task.id == entry["task_id"])).one_or_none()
        if task is None or task.execution_status != TaskExecutionStatus.PENDING.value:
            continue  # 进行中与已完成的后继任务不被改写（18 号第 3 节）
        task.execution_status = TaskExecutionStatus.CANCELLED.value
        task.revision += 1
        task.updated_at = now

    proposal.status = ChangeProposalStatus.APPLIED.value
    proposal.accepted_at = now
    proposal.applied_at = now
    proposal.updated_at = now
    _bump_planning_revision(session, user.user_id)
    return ResultRef("change_proposal", proposal.id), 200


def reject_change_proposal(
    db: Database, user: CurrentUser, proposal_id: str, *, idempotency_key: str
) -> ChangeProposalView:
    """拒绝提案：关联与依赖保持原样，问题仍然可见（产品 13 号第 4 节）。"""
    request = _idempotent_request(user, "links.reject_change_proposal", idempotency_key, body=None)
    with db.write() as session:
        outcome = run_idempotent(
            session,
            request,
            lambda: _do_reject(session, user, proposal_id),
            now=_now(),
        )
        proposal = session.scalars(select(ChangeProposal).where(ChangeProposal.id == outcome.result.id)).one()
        return _proposal_view(proposal)


def _do_reject(session: Session, user: CurrentUser, proposal_id: str) -> tuple[ResultRef, int]:
    proposal = _get_proposal(session, user, proposal_id)
    if proposal.status != ChangeProposalStatus.PENDING.value:
        raise GoalflowError(
            ErrorCode.GOAL_STATE_CONFLICT,
            f"提案当前状态为 {proposal.status}，只有 pending 提案可以拒绝",
        )
    proposal.status = ChangeProposalStatus.REJECTED.value
    proposal.updated_at = _now()
    return ResultRef("change_proposal", proposal.id), 200
