"""目标与计划接口（T04 PR-2）：目标、档案草稿、路线、计划草稿与生命周期命令。

端点清单的出处：

- 05-module-contracts.md 第 2 节：create_goal 到 activate_plan 的接口行；
- 11-route-engine.md 第 8 节与 12-plan-engine.md 第 8 节：路线与计划的作业、读取与修改端点；
- 17-goal-lifecycle-design.md 第 1 节：pause / resume / close / undo_closure / derive 命令，
  激活不设独立端点——activate_plan 在同一事务内完成目标 draft → active（T04 决策 A10）。

四个模型生成类端点（route-generations、route-variants、plan-generations、
plan-change-requests）保持契约桩：生成候选由 T08 的 Agent 图提供后接入
（交接卡决策 A1），其余端点均调用 goals 模块的 Interface。
"""

from datetime import date, datetime
from typing import Annotated, Any, Final, NoReturn

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field

from goalflow.api.dependencies import CurrentUserDep, DatabaseDep, require_idempotency_key
from goalflow.api.routes.jobs import JobResponse
from goalflow.contracts.enums import (
    ClosureKind,
    DependencyCheckStatus,
    GoalDomain,
    GoalKind,
    GoalStatus,
    PlanVersionStatus,
    ProfileDraftReadiness,
    ReviewPeriod,
    RouteSetStatus,
    RouteStatus,
    TaskExecutionStatus,
    TaskExecutor,
)
from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.contracts.http import ErrorResponse, RevisionedRequest, RevisionedResource
from goalflow.goals import lifecycle, service
from goalflow.goals.lifecycle import ActivationOutcome, TransitionView
from goalflow.goals.service import (
    DraftTaskView,
    GoalView,
    PlanDraftView,
    ProfileDraftView,
    ProfileView,
    RouteSetView,
    RouteView,
)

router = APIRouter(prefix="/api/goals", tags=["goals"])

_ERROR_DESCRIPTIONS: Final = {
    401: "未登录或会话失效",
    403: "请求来源不受信任",
    404: "目标或相关资源不存在，或不属于当前用户",
    409: (
        "版本已更新（REVISION_CONFLICT）、Idempotency-Key 已用于另一项请求"
        "（IDEMPOTENCY_KEY_CONFLICT）、目标状态不允许该操作（GOAL_STATE_CONFLICT）"
        "或时间预算冲突（BUDGET_CONFLICT）"
    ),
    422: "请求参数校验未通过",
    500: "接口尚未实现（生成类端点随 T08 Agent 作业接入交付）",
    503: "生成依赖的模型服务暂不可用（MODEL_UNAVAILABLE）",
}


def _errors(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    return {code: {"model": ErrorResponse, "description": _ERROR_DESCRIPTIONS[code]} for code in status_codes}


GoalIdPath = Annotated[str, Path(max_length=36, description="目标 ID")]
TaskIdPath = Annotated[str, Path(max_length=36, description="任务 ID")]
IdempotencyKeyDep = Annotated[str, Depends(require_idempotency_key)]


def _generation_stub() -> NoReturn:
    """生成类端点的契约桩：候选由 T08 的 Agent 图提供后接入（交接卡决策 A1）。"""
    raise GoalflowError(
        ErrorCode.INTERNAL_ERROR,
        "该端点随 T08 Agent 生成作业接入交付",
    )


# —— 通用形状 ——


class CriterionConfirmation(BaseModel):
    """结束目标时对单条成功标准的确认。快照整体写入 goals.closure_criteria_snapshot_json（T04 决策 A8）。"""

    criterion_key: str = Field(description="成功标准的标识，来自已确认档案的 success_criteria")
    met: bool = Field(description="该标准是否已达成；允许部分达成时标记完成并记录未达成项（D12 第 3 节）")
    note: str | None = Field(default=None, max_length=2000, description="对未达成项的记录说明")


class GoalResponse(RevisionedResource):
    """目标当前状态。生命周期字段的语义见 12-goal-lifecycle.md（D12）。"""

    id: str
    title: str
    domain: GoalDomain = Field(description="Agent 的内部策略路由，不是用户必填标签")
    domain_confidence: float | None = Field(description="领域推断置信度，0–1；未推断时为空")
    kind: GoalKind = Field(description="目标形态，由档案时间边界推导，随档案确认")
    status: GoalStatus
    review_period: ReviewPeriod = Field(description="维持型目标的回顾周期；达成型同样存储，不参与回顾")
    active_profile_id: str | None
    current_plan_version_id: str | None
    source_goal_id: str | None = Field(description="非空表示本目标由该目标'以此为起点新建'派生而来")
    paused_at: datetime | None
    pause_reason: str | None
    closed_at: datetime | None
    closure_kind: ClosureKind | None
    closure_note: str | None
    created_at: datetime
    updated_at: datetime


class AffectedDependency(BaseModel):
    """暂停目标时受影响的跨目标依赖项。取值由 T06 的依赖图计算，接入前恒为空列表（T04 决策 A12）。"""

    goal_id: str = Field(description="依赖方（后继任务）所属目标")
    task_id: str = Field(description="将被计算态 blocked 的后继任务")


class GoalTransitionResponse(GoalResponse):
    """暂停/恢复响应：目标状态 + 依赖影响。"""

    affected_dependent_tasks: list[AffectedDependency] = Field(
        description="受本次暂停影响、将进入计算态 blocked 的他目标任务；仅在暂停操作中返回内容"
    )
    dependency_check: DependencyCheckStatus = Field(
        description="依赖校验是否已接入。not_wired 时列表为空仅因'未检查'，不代表'无影响'"
    )


class ProfileDraftResponse(RevisionedResource):
    """档案草稿。事实、推断、假设和未知可区分（R02）。"""

    goal_id: str
    content: dict[str, Any] = Field(description="草稿正文，结构由澄清引擎版本决定")
    source_map: dict[str, Any] = Field(description="各字段来源：用户陈述、推断或建议")
    gaps: list[dict[str, Any]] = Field(description="尚缺的关键事实")
    assumptions: list[dict[str, Any]] = Field(description="已采用但未经用户确认的假设")
    contradictions: list[dict[str, Any]] = Field(description="检测到的陈述矛盾")
    readiness: ProfileDraftReadiness
    updated_at: datetime


class ProfileResponse(BaseModel):
    """不可变的已确认档案版本。"""

    id: str
    goal_id: str
    version_no: int = Field(ge=1)
    result_definition: str
    success_criteria: list[dict[str, Any]] = Field(description="成功标准逐条列表，含标识与验证方式")
    baseline: dict[str, Any]
    constraints: dict[str, Any]
    facts: dict[str, Any] = Field(description="用户事实、假设、建议分别带来源与确认状态")
    confirmed_at: datetime


class DerivedMetric(BaseModel):
    """服务端计算的路线派生指标，模型不能覆盖（11 号第 4 节）。"""

    total_estimated_minutes: int = 0
    peak_weekly_minutes: int = 0
    available_weekly_minutes: int = 0
    budget_gap_minutes: int = 0
    recommendation_eligible: bool = False
    conflicts: list[dict[str, Any]] = Field(default_factory=list, description="预算、期限或与其他目标的冲突")


class RouteResponse(RevisionedResource):
    """结构化路线（11 号第 4 节）。"""

    id: str
    route_set_id: str
    status: RouteStatus
    based_on_route_id: str | None = Field(description="非空表示由该路线微调产生的变体")
    title: str
    approach: str
    difference_keys: list[str] = Field(description="与其他候选的差异维度，取自受控枚举（11 号第 5 节）")
    duration_range: dict[str, Any]
    phase_outline: list[dict[str, Any]] = Field(description="每阶段成果、预计周数及每周分钟数")
    weekly_minutes: int = Field(ge=0)
    tradeoffs: list[dict[str, Any]]
    risks: list[dict[str, Any]]
    assumptions: list[dict[str, Any]]
    required_resources: list[dict[str, Any]]
    derived_metrics: DerivedMetric
    created_at: datetime


class RouteSetResponse(RevisionedResource):
    """当前可比较的整组路线与派生冲突（11 号第 8 节）。"""

    id: str
    goal_id: str
    profile_id: str
    status: RouteSetStatus
    invalidated_reason: str | None
    planning_revision: int = Field(description="生成时所处的 planning revision；与当前值不一致即为 stale")
    availability_revision: int = Field(description="生成时所处的可用时间版本")
    routes: list[RouteResponse]
    created_at: datetime


class PlanPhaseResponse(BaseModel):
    phase_key: str
    rank: int = Field(ge=1)
    title: str
    outcome: str
    exit_criteria: list[dict[str, Any]]
    duration_estimate: dict[str, Any]


class PlanMilestoneResponse(BaseModel):
    milestone_key: str
    phase_id: str
    rank: int = Field(ge=1)
    title: str
    success_criteria: list[dict[str, Any]]
    target_window: dict[str, Any]


class DraftTaskResponse(RevisionedResource):
    """计划草稿中的任务。draft 计划的任务为 proposed，不出现在正式待办（R05）。"""

    task_id: str
    spec_no: int = Field(ge=1, description="内容版本号；草稿编辑产生新 spec，原 spec 保留")
    title: str
    executor: TaskExecutor
    expected_minutes: int = Field(ge=0)
    minimum_minutes: int = Field(ge=0)
    maximum_minutes: int = Field(ge=0)
    can_split: bool
    minimum_session_minutes: int | None
    earliest_date: date | None
    latest_date: date = Field(description="非空；由计划生成时推导（05-module-contracts 默认值表）")
    execution_status: TaskExecutionStatus
    phase_id: str
    milestone_id: str | None
    batch_window: dict[str, Any] = Field(description="所属批次的 window_start / window_end")


class PlanDraftResponse(RevisionedResource):
    """计划草稿：完整阶段与里程碑、开始日起七天详细任务、冲突与 stale 状态（R05、12 号第 8 节）。"""

    plan_version_id: str
    goal_id: str
    status: PlanVersionStatus
    start_date: date
    horizon_end: date | None = Field(description="达成型非空且展开不得越过；维持型可空")
    detailed_through_date: date | None = Field(description="详细任务已覆盖到的日期；早于 today+3 天时触发滚动展开")
    phases: list[PlanPhaseResponse]
    milestones: list[PlanMilestoneResponse]
    tasks: list[DraftTaskResponse] = Field(description="首个有效批次的详细任务")
    conflicts: list[dict[str, Any]] = Field(description="预算、期限、开始日容量等结构化冲突")
    stale: bool = Field(description="生成输入的 revision 已变化，结果需重新生成")
    stale_reason: str | None


class RouteSelectionResponse(RevisionedResource):
    """路线选择结果。选择动作不创建正式任务（11 号第 7 节）。"""

    goal_id: str
    route_id: str
    selected_at: datetime


class ActivationResponse(BaseModel):
    """启用结果。幂等：重复请求返回同一结果，不重复创建任务（12 号第 7 节）。"""

    goal_id: str
    plan_version_id: str = Field(description="切换为 active 的计划版本；目标状态同步 draft → active（T04 决策 A10）")
    first_batch_window: dict[str, Any] = Field(description="首个批次的 window_start / window_end")
    activated_at: datetime


# —— 请求体 ——


class CreateGoalRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    initial_description: str | None = Field(
        default=None, max_length=8000, description="自然语言目标描述，作为澄清对话的起点（R02）"
    )


class UpdateProfileDraftRequest(RevisionedRequest):
    edits: dict[str, Any] = Field(description="用户对草稿的直接编辑；基于旧档案的模型预览将标记 stale（05 第 2 节）")


class ConfirmProfileRequest(RevisionedRequest):
    pass


class GenerateRoutesRequest(RevisionedRequest):
    profile_id: str = Field(max_length=36, description="用于生成的已确认档案")


class RouteVariantRequest(RevisionedRequest):
    route_id: str = Field(max_length=36)
    adjustment: dict[str, Any] | None = Field(default=None, description="结构化微调；与 natural_language 二选一")
    natural_language: str | None = Field(default=None, max_length=4000, description="自然语言微调要求")


class SelectRouteRequest(RevisionedRequest):
    route_id: str = Field(max_length=36)


class GeneratePlanRequest(RevisionedRequest):
    route_id: str = Field(max_length=36, description="最终选定的路线")
    start_date: date = Field(description="计划开始日；开始日容量不足时草稿照常保存并返回建议前移日期（12 号第 2 节）")


class UpdateDraftTaskRequest(RevisionedRequest):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    instructions: str | None = Field(default=None, max_length=8000)
    expected_minutes: int | None = Field(default=None, ge=0)
    minimum_minutes: int | None = Field(default=None, ge=0)
    maximum_minutes: int | None = Field(default=None, ge=0)


class PlanChangeRequest(RevisionedRequest):
    request_text: str | None = Field(default=None, max_length=4000, description="自然语言修改")
    structured_patch: dict[str, Any] | None = Field(default=None, description="结构化修改；与 request_text 至少一项")


class ActivatePlanRequest(RevisionedRequest):
    draft_plan_id: str = Field(max_length=36)
    planning_revision: int | None = Field(
        default=None,
        ge=0,
        description="客户端读到的 planning revision；T05 交付前可省略（省略时跳过 stale 判定）",
    )


class PauseGoalRequest(RevisionedRequest):
    reason: str | None = Field(default=None, max_length=500, description="暂停原因；记录但不设自动恢复")


class ResumeGoalRequest(RevisionedRequest):
    pass


class CloseGoalRequest(RevisionedRequest):
    closure_kind: ClosureKind
    note: str | None = Field(default=None, max_length=2000)
    criteria_confirmations: list[CriterionConfirmation] = Field(
        description="成功标准逐条确认；随结束操作写入快照，撤销后可还原（17 号第 4 节）"
    )


class UndoClosureRequest(RevisionedRequest):
    pass


class DeriveGoalRequest(RevisionedRequest):
    pass


# —— 视图 → 响应模型 ——


def _goal_response(view: GoalView) -> GoalResponse:
    return GoalResponse(
        id=view.id,
        title=view.title,
        domain=GoalDomain(view.domain),
        domain_confidence=view.domain_confidence,
        kind=GoalKind(view.kind),
        status=GoalStatus(view.status),
        review_period=ReviewPeriod(view.review_period),
        active_profile_id=view.active_profile_id,
        current_plan_version_id=view.current_plan_version_id,
        source_goal_id=view.source_goal_id,
        paused_at=view.paused_at,
        pause_reason=view.pause_reason,
        closed_at=view.closed_at,
        closure_kind=ClosureKind(view.closure_kind) if view.closure_kind else None,
        closure_note=view.closure_note,
        revision=view.revision,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


def _transition_response(view: TransitionView) -> GoalTransitionResponse:
    goal = _goal_response(view.goal)
    return GoalTransitionResponse(
        **goal.model_dump(),
        affected_dependent_tasks=[
            AffectedDependency(goal_id=item.goal_id, task_id=item.task_id) for item in view.affected_dependent_tasks
        ],
        dependency_check=view.dependency_check,
    )


def _draft_response(view: ProfileDraftView) -> ProfileDraftResponse:
    return ProfileDraftResponse(
        goal_id=view.goal_id,
        content=view.content,
        source_map=view.source_map,
        gaps=view.gaps,
        assumptions=view.assumptions,
        contradictions=view.contradictions,
        readiness=ProfileDraftReadiness(view.readiness),
        revision=view.revision,
        updated_at=view.updated_at,
    )


def _profile_response(view: ProfileView) -> ProfileResponse:
    return ProfileResponse(
        id=view.id,
        goal_id=view.goal_id,
        version_no=view.version_no,
        result_definition=view.result_definition,
        success_criteria=view.success_criteria,
        baseline=view.baseline,
        constraints=view.constraints,
        facts=view.facts,
        confirmed_at=view.confirmed_at,
    )


def _route_response(view: RouteView) -> RouteResponse:
    return RouteResponse(
        id=view.id,
        route_set_id=view.route_set_id,
        status=RouteStatus(view.status),
        based_on_route_id=view.based_on_route_id,
        title=view.title,
        approach=view.approach,
        difference_keys=view.difference_keys,
        duration_range=view.duration_range,
        phase_outline=view.phase_outline,
        weekly_minutes=view.weekly_minutes,
        tradeoffs=view.tradeoffs,
        risks=view.risks,
        assumptions=view.assumptions,
        required_resources=view.required_resources,
        derived_metrics=DerivedMetric(**view.derived_metrics),
        revision=view.revision,
        created_at=view.created_at or datetime.now(),
    )


def _route_set_response(view: RouteSetView) -> RouteSetResponse:
    return RouteSetResponse(
        id=view.id,
        goal_id=view.goal_id,
        profile_id=view.profile_id,
        status=RouteSetStatus(view.status),
        invalidated_reason=view.invalidated_reason,
        planning_revision=view.planning_revision,
        availability_revision=view.availability_revision,
        routes=[_route_response(route) for route in view.routes],
        revision=view.revision,
        created_at=view.created_at or datetime.now(),
    )


def _task_response(view: DraftTaskView) -> DraftTaskResponse:
    return DraftTaskResponse(
        task_id=view.task_id,
        spec_no=view.spec_no,
        title=view.title,
        executor=TaskExecutor(view.executor),
        expected_minutes=view.expected_minutes,
        minimum_minutes=view.minimum_minutes,
        maximum_minutes=view.maximum_minutes,
        can_split=view.can_split,
        minimum_session_minutes=view.minimum_session_minutes,
        earliest_date=date.fromisoformat(view.earliest_date) if view.earliest_date else None,
        latest_date=date.fromisoformat(view.latest_date),
        execution_status=TaskExecutionStatus(view.execution_status),
        phase_id=view.phase_id,
        milestone_id=view.milestone_id,
        batch_window=view.batch_window,
        revision=view.revision,
    )


def _plan_draft_response(view: PlanDraftView) -> PlanDraftResponse:
    return PlanDraftResponse(
        plan_version_id=view.plan_version_id,
        goal_id=view.goal_id,
        status=PlanVersionStatus(view.status),
        start_date=date.fromisoformat(view.start_date),
        horizon_end=date.fromisoformat(view.horizon_end) if view.horizon_end else None,
        detailed_through_date=date.fromisoformat(view.detailed_through_date) if view.detailed_through_date else None,
        phases=[PlanPhaseResponse(**phase) for phase in view.phases],
        milestones=[PlanMilestoneResponse(**milestone) for milestone in view.milestones],
        tasks=[_task_response(task) for task in view.tasks],
        conflicts=view.conflicts,
        stale=view.stale,
        stale_reason=view.stale_reason,
        revision=view.revision,
    )


# —— 端点 ——


@router.post(
    "",
    summary="创建目标",
    description="从自然描述开始创建 draft 目标与 planning_session（R02）。领域由服务端推断。",
    status_code=201,
    responses=_errors(401, 403, 409, 422),
)
def create_goal(
    user: CurrentUserDep,
    db: DatabaseDep,
    idempotency_key: IdempotencyKeyDep,
    request: CreateGoalRequest,
) -> GoalResponse:
    view = service.create_goal(
        db,
        user,
        title=request.title,
        initial_description=request.initial_description,
        idempotency_key=idempotency_key,
    )
    return _goal_response(view)


@router.get(
    "/{goal_id}",
    summary="读取目标",
    responses=_errors(401, 404),
)
def read_goal(user: CurrentUserDep, db: DatabaseDep, goal_id: GoalIdPath) -> GoalResponse:
    return _goal_response(service.get_goal(db, user, goal_id))


@router.get(
    "/{goal_id}/profile-draft",
    summary="读取档案草稿",
    description="返回草稿字段、来源、缺口、假设、矛盾及 readiness（05 第 2 节）。",
    responses=_errors(401, 404),
)
def read_profile_draft(user: CurrentUserDep, db: DatabaseDep, goal_id: GoalIdPath) -> ProfileDraftResponse:
    return _draft_response(service.get_profile_draft(db, user, goal_id))


@router.patch(
    "/{goal_id}/profile-draft",
    summary="编辑档案草稿",
    responses=_errors(401, 403, 404, 409, 422),
)
def update_profile_draft(
    user: CurrentUserDep, db: DatabaseDep, goal_id: GoalIdPath, request: UpdateProfileDraftRequest
) -> ProfileDraftResponse:
    view = service.update_profile_draft(
        db, user, goal_id, edits=request.edits, expected_revision=request.expected_revision
    )
    return _draft_response(view)


@router.post(
    "/{goal_id}/profile-confirmations",
    summary="确认档案",
    description=(
        "创建不可变档案版本，并原子更新 goals.active_profile_id 与 planning_session"
        "（03 第 2 节）。模型晚返回时草稿 revision 不匹配的结果被拒。"
    ),
    status_code=201,
    responses=_errors(401, 403, 404, 409, 422),
)
def confirm_profile(
    user: CurrentUserDep,
    db: DatabaseDep,
    idempotency_key: IdempotencyKeyDep,
    goal_id: GoalIdPath,
    request: ConfirmProfileRequest,
) -> ProfileResponse:
    view = service.confirm_profile(
        db, user, goal_id, expected_revision=request.expected_revision, idempotency_key=idempotency_key
    )
    return _profile_response(view)


@router.post(
    "/{goal_id}/route-generations",
    summary="提交路线生成作业",
    description="从已确认档案生成整组可比较路线（11 号第 3 节）。异步：返回 202 与作业引用。",
    status_code=202,
    responses=_errors(401, 403, 404, 409, 422, 500, 503),
)
def generate_routes(
    _user: CurrentUserDep,
    _idempotency_key: IdempotencyKeyDep,
    goal_id: GoalIdPath,
    request: GenerateRoutesRequest,
) -> JobResponse:
    _generation_stub()


@router.get(
    "/{goal_id}/route-sets/current",
    summary="读取当前路线集合",
    description="返回当前集合、统一比较字段、派生冲突和 stale 状态（11 号第 8 节）。",
    responses=_errors(401, 404),
)
def read_current_routes(user: CurrentUserDep, db: DatabaseDep, goal_id: GoalIdPath) -> RouteSetResponse:
    return _route_set_response(service.get_current_route_set(db, user, goal_id))


@router.post(
    "/{goal_id}/route-variants",
    summary="提交路线微调",
    description="结构化微调创建变体；改变核心方法时返回需重新生成集合的结果（11 号第 6 节）。",
    status_code=202,
    responses=_errors(401, 403, 404, 409, 422, 500, 503),
)
def revise_route(
    _user: CurrentUserDep,
    _idempotency_key: IdempotencyKeyDep,
    goal_id: GoalIdPath,
    request: RouteVariantRequest,
) -> JobResponse:
    _generation_stub()


@router.post(
    "/{goal_id}/route-selection",
    summary="记录路线选择",
    responses=_errors(401, 403, 404, 409, 422),
)
def select_route(
    user: CurrentUserDep,
    db: DatabaseDep,
    idempotency_key: IdempotencyKeyDep,
    goal_id: GoalIdPath,
    request: SelectRouteRequest,
) -> RouteSelectionResponse:
    view = service.select_route(
        db,
        user,
        goal_id,
        route_id=request.route_id,
        expected_revision=request.expected_revision,
        idempotency_key=idempotency_key,
    )
    return RouteSelectionResponse(
        goal_id=view.goal_id, route_id=view.route_id, selected_at=view.selected_at, revision=view.revision
    )


@router.post(
    "/{goal_id}/plan-generations",
    summary="提交计划草稿生成作业",
    description=(
        "由选定路线生成计划草稿与首个七日任务批次（12 号第 2 节）。不启用。"
        "开始日剩余容量容纳不下任何任务时草稿照常保存并返回建议前移日期（12 号第 2 节）。"
    ),
    status_code=202,
    responses=_errors(401, 403, 404, 409, 422, 500, 503),
)
def generate_plan(
    _user: CurrentUserDep,
    _idempotency_key: IdempotencyKeyDep,
    goal_id: GoalIdPath,
    request: GeneratePlanRequest,
) -> JobResponse:
    _generation_stub()


@router.get(
    "/{goal_id}/plan-drafts/current",
    summary="读取计划草稿",
    description="返回阶段、里程碑、七日任务批次、冲突和 stale 状态（05 第 2 节）。",
    responses=_errors(401, 404),
)
def read_plan_draft(user: CurrentUserDep, db: DatabaseDep, goal_id: GoalIdPath) -> PlanDraftResponse:
    return _plan_draft_response(service.get_plan_draft(db, user, goal_id))


@router.patch(
    "/{goal_id}/plan-drafts/current/tasks/{task_id}",
    summary="编辑草稿任务",
    description="创建新 task_spec，保留原版本（03 第 3 节）。",
    responses=_errors(401, 403, 404, 409, 422),
)
def update_draft_task(
    user: CurrentUserDep,
    db: DatabaseDep,
    goal_id: GoalIdPath,
    task_id: TaskIdPath,
    request: UpdateDraftTaskRequest,
) -> DraftTaskResponse:
    view = service.update_draft_task(
        db,
        user,
        goal_id,
        task_id,
        expected_revision=request.expected_revision,
        title=request.title,
        instructions=request.instructions,
        expected_minutes=request.expected_minutes,
        minimum_minutes=request.minimum_minutes,
        maximum_minutes=request.maximum_minutes,
    )
    return _task_response(view)


@router.post(
    "/{goal_id}/plan-change-requests",
    summary="提交计划修改请求",
    description="自然语言或结构化修改；服务端重新计算影响并分类（12 号第 6 节）。",
    status_code=202,
    responses=_errors(401, 403, 404, 409, 422, 500, 503),
)
def request_plan_change(
    _user: CurrentUserDep,
    _idempotency_key: IdempotencyKeyDep,
    goal_id: GoalIdPath,
    request: PlanChangeRequest,
) -> JobResponse:
    _generation_stub()


@router.post(
    "/{goal_id}/activation",
    summary="启用当前计划草稿",
    description=(
        "幂等启用：同一事务内切换计划为 active、目标 draft → active（T04 决策 A10）、"
        "首批 proposed 任务转为 pending，并触发当日安排重算。预算冲突不部分启用。"
    ),
    responses=_errors(401, 403, 404, 409, 422),
)
def activate_plan(
    user: CurrentUserDep,
    db: DatabaseDep,
    idempotency_key: IdempotencyKeyDep,
    goal_id: GoalIdPath,
    request: ActivatePlanRequest,
) -> ActivationResponse:
    outcome: ActivationOutcome = lifecycle.activate_plan(
        db,
        user,
        goal_id,
        draft_plan_id=request.draft_plan_id,
        expected_revision=request.expected_revision,
        planning_revision=request.planning_revision,
        idempotency_key=idempotency_key,
    )
    return ActivationResponse(
        goal_id=outcome.goal.id,
        plan_version_id=outcome.plan_version_id,
        first_batch_window=outcome.batch_window,
        activated_at=outcome.activated_at,
    )


@router.post(
    "/{goal_id}/pause",
    summary="暂停目标",
    description=(
        "active → paused。不改写任何任务状态，周投入需求从共享预算释放；响应携带受影响的跨目标依赖（D12 第 5 节）。"
    ),
    responses=_errors(401, 403, 404, 409, 422),
)
def pause_goal(
    user: CurrentUserDep,
    db: DatabaseDep,
    idempotency_key: IdempotencyKeyDep,
    goal_id: GoalIdPath,
    request: PauseGoalRequest,
) -> GoalTransitionResponse:
    view = lifecycle.pause_goal(
        db,
        user,
        goal_id,
        expected_revision=request.expected_revision,
        reason=request.reason,
        idempotency_key=idempotency_key,
    )
    return _transition_response(view)


@router.post(
    "/{goal_id}/resume",
    summary="恢复目标",
    description=("paused → active。不把积压任务搬到今天；共享预算被占满时返回冲突供取舍（D12 第 5 节）。"),
    responses=_errors(401, 403, 404, 409, 422),
)
def resume_goal(
    user: CurrentUserDep,
    db: DatabaseDep,
    idempotency_key: IdempotencyKeyDep,
    goal_id: GoalIdPath,
    request: ResumeGoalRequest,
) -> GoalTransitionResponse:
    return _transition_response(
        lifecycle.resume_goal(
            db,
            user,
            goal_id,
            expected_revision=request.expected_revision,
            idempotency_key=idempotency_key,
        )
    )


@router.post(
    "/{goal_id}/closure",
    summary="结束目标",
    description=(
        "active → completed / stopped，终态不可逆。completed 仅对达成型开放；"
        "成功标准逐条确认随操作写入快照，撤销后可还原（17 号第 4 节）。"
    ),
    responses=_errors(401, 403, 404, 409, 422),
)
def close_goal(
    user: CurrentUserDep,
    db: DatabaseDep,
    idempotency_key: IdempotencyKeyDep,
    goal_id: GoalIdPath,
    request: CloseGoalRequest,
) -> GoalResponse:
    view = lifecycle.close_goal(
        db,
        user,
        goal_id,
        expected_revision=request.expected_revision,
        closure_kind=request.closure_kind,
        note=request.note,
        criteria_confirmations=[item.model_dump() for item in request.criteria_confirmations],
        idempotency_key=idempotency_key,
    )
    return _goal_response(view)


@router.post(
    "/{goal_id}/closure-undo",
    summary="撤销结束",
    description=("结束操作后 24 小时内可撤销，回到结束前状态（D12 第 6 节）；窗口外返回 GOAL_STATE_CONFLICT。"),
    responses=_errors(401, 403, 404, 409, 422),
)
def undo_closure(
    user: CurrentUserDep,
    db: DatabaseDep,
    idempotency_key: IdempotencyKeyDep,
    goal_id: GoalIdPath,
    request: UndoClosureRequest,
) -> GoalResponse:
    return _goal_response(
        lifecycle.undo_closure(
            db,
            user,
            goal_id,
            expected_revision=request.expected_revision,
            idempotency_key=idempotency_key,
        )
    )


@router.post(
    "/{goal_id}/derivation",
    summary="以此为起点新建",
    description=(
        "复制源目标最新档案内容为新目标的档案草稿，不复制计划、任务与执行记录；源目标保持终态（17 号第 4 节）。"
    ),
    status_code=201,
    responses=_errors(401, 403, 404, 409, 422),
)
def derive_goal(
    user: CurrentUserDep,
    db: DatabaseDep,
    idempotency_key: IdempotencyKeyDep,
    goal_id: GoalIdPath,
    request: DeriveGoalRequest,
) -> GoalResponse:
    return _goal_response(
        lifecycle.derive_goal(
            db,
            user,
            goal_id,
            expected_revision=request.expected_revision,
            idempotency_key=idempotency_key,
        )
    )
