"""时间预算与排期接口（T05 PR-2）：偏好、额度、单日约束与每日安排。

端点清单的出处：

- 13-scheduling-engine.md 第 8 节：scheduling-preferences、task-constraints、
  agendas/{date}、generation、availability 五个端点；
- 05-module-contracts.md 第 2 节：read_today、ensure_agenda、update_goal_priorities、
  constrain_today_task、update_availability、override_today 的接口行。

全部端点调用 scheduling 模块的 Interface；generation 为异步作业（决策 A10），
计算与落库在 `scheduling.jobs.generate_agenda`。
"""

from datetime import date, datetime
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field

import goalflow.scheduling.jobs  # noqa: F401  注册 agenda_generation 处理函数（提交时校验 kind）
from goalflow.api.dependencies import (
    CurrentUserDep,
    DatabaseDep,
    JobPublisherDep,
    require_idempotency_key,
)
from goalflow.api.routes.jobs import JobResponse, _job_response
from goalflow.contracts.enums import (
    AgendaRevisionStatus,
    CapacityBasis,
    DailyOverrideKind,
    GoalFocusStatus,
    SchedulingReasonCode,
    TaskDayConstraintKind,
)
from goalflow.contracts.http import ErrorResponse, RevisionedRequest, RevisionedResource
from goalflow.scheduling import service as scheduling_service

router = APIRouter(prefix="/api", tags=["scheduling"])

_ERROR_DESCRIPTIONS: Final = {
    401: "未登录或会话失效",
    403: "请求来源不受信任",
    404: "日程、任务、目标或相关资源不存在，或不属于当前用户",
    409: "版本已更新（REVISION_CONFLICT）或时间预算冲突（BUDGET_CONFLICT）",
    422: "请求参数校验未通过",
    500: "生成当日安排失败（INTERNAL_ERROR）",
}


def _errors(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    return {code: {"model": ErrorResponse, "description": _ERROR_DESCRIPTIONS[code]} for code in status_codes}


LocalDatePath = Annotated[date, Path(description="用户本地日期，格式 YYYY-MM-DD")]
TaskIdPath = Annotated[str, Path(max_length=36, description="任务 ID")]
IdempotencyKeyDep = Annotated[str, Depends(require_idempotency_key)]


# —— 通用形状 ——


class PreferenceInput(BaseModel):
    """单个目标的调度偏好（03 第 4 节 goal_scheduling_preferences）。"""

    goal_id: str = Field(max_length=36, description="活动目标 ID")
    focus_status: GoalFocusStatus = Field(description="focus 只提升弹性任务排序权重")
    rank: int = Field(ge=1, description="目标顺序，数值小者优先；只参与弹性任务取舍")


class SchedulingPreferencesResponse(BaseModel):
    """偏好保存结果：新的排期协调入口版本（05-module-contracts update_goal_priorities）。"""

    planning_revision: int = Field(ge=0, description="更新后的 planning revision；变化触发受影响安排重算")
    preferences: list[PreferenceInput] = Field(description="保存后的全部活动目标偏好")


class AvailabilityVersionResponse(RevisionedResource):
    """周额度版本（03 第 4 节 availability_versions）。"""

    id: str
    effective_from: date = Field(description="生效起始日期；按日期选择使用的版本")
    weekly_minutes: dict[str, int] = Field(
        description="周一至周日七个额度（分钟），键为 monday…sunday",
    )
    version_no: int = Field(ge=1)
    created_at: datetime
    coordination_required: bool = Field(
        description="已有安排需要协调时为 true（05-module-contracts update_availability）"
    )


class DailyOverrideResponse(RevisionedResource):
    """单日额度声明（03 第 4 节 daily_overrides）。"""

    local_date: date
    override_kind: DailyOverrideKind = Field(description="total 与 remaining 是两种口径，不可混用")
    minutes: int = Field(ge=0)
    measured_at: datetime = Field(description="声明时间；remaining 口径从此刻起扣减")
    coordination_required: bool = Field(description="已有安排需要协调时为 true")


class CapacitySummaryView(BaseModel):
    """当日额度汇总（13 号第 1 节 capacity_summary：额度来源、已投入、已保留和剩余分钟）。"""

    basis: CapacityBasis = Field(description="额度口径；total 按全天扣已投入，remaining 从声明时点起扣")
    quota_minutes: int = Field(ge=0, description="当日有效额度（分钟）")
    spent_minutes: int = Field(ge=0, description="今日已投入（分钟）")
    reserved_minutes: int = Field(ge=0, description="已保留（分钟）")
    remaining_minutes: int = Field(ge=0, description="当前剩余容量（分钟）")


class AgendaItemView(BaseModel):
    """当日一个任务的分配（13 号第 1 节 agenda_items）。首版没有起止时刻（R06）。"""

    task_id: str
    task_spec_id: str = Field(description="排期所依据的任务内容版本")
    rank: int = Field(ge=1, description="当日执行顺序")
    allocated_minutes: int = Field(gt=0, description="分配分钟数；不超过任务剩余预计分钟")
    reason_codes: list[SchedulingReasonCode] = Field(description="排序键、所选层级及原因码（13 号第 4 节）")


class DeferredTaskView(BaseModel):
    """未进入当天的任务及原因（13 号第 1 节 deferred_tasks）。"""

    task_id: str
    reason_code: SchedulingReasonCode
    detail: str | None = Field(default=None, description="面向用户的补充说明")


class ConflictView(BaseModel):
    """无法同时满足的硬约束或分钟缺口（13 号第 1、6 节）。"""

    code: SchedulingReasonCode
    task_ids: list[str] = Field(description="涉及的任务；必须项冲突时保留全部冲突项供用户选择")
    message: str = Field(description="面向用户的问题说明（Q04）")
    minutes_gap: int | None = Field(default=None, description="缺口分钟数；不适用时为空")


class ChangeProposalView(BaseModel):
    """需要用户确认的变更建议（13 号第 6 节 pending change proposal）。

    里程碑延期、增加总投入、降低某目标投入、移动用户锁定/进行中任务或影响
    关联目标时不自动执行，生成待确认建议（D06）。
    """

    proposal_type: str = Field(
        description="建议类型：deadline_extension / increase_investment / reduce_investment / "
        "move_locked_task / cross_goal_impact（取值随业务实现固定）"
    )
    task_ids: list[str] = Field(default_factory=list, description="涉及的任务")
    description: str = Field(description="建议内容与理由")
    requires_confirmation: bool = Field(default=True, description="首版建议一律待确认（D06）")


class AgendaRevisionView(BaseModel):
    """一次排期计算的完整结果（13 号第 1 节 SchedulingResult 的持久化形状）。"""

    id: str
    version_no: int = Field(ge=1)
    status: AgendaRevisionStatus
    input_planning_revision: int = Field(ge=0, description="计算基于的 planning revision")
    scheduling_policy_version: str = Field(description="当时按哪版排期策略判定（Q08 可解释）")
    capacity: CapacitySummaryView
    items: list[AgendaItemView]
    deferred: list[DeferredTaskView]
    conflicts: list[ConflictView]
    change_proposals: list[ChangeProposalView]
    created_at: datetime


class AgendaResponse(BaseModel):
    """某日的当前安排（05-module-contracts read_today）。

    读取不直接创建作业：尚无排期结果时 current 为 null，前端据此调用 generation 端点。
    """

    local_date: date
    planning_revision: int = Field(ge=0, description="当前排期协调入口版本")
    current: AgendaRevisionView | None = Field(description="当前生效的排期结果；尚无时为 null")


class TaskConstraintResponse(RevisionedResource):
    """单日任务约束的保存结果（05-module-contracts constrain_today_task）。"""

    task_id: str
    local_date: date
    constraint_kind: TaskDayConstraintKind
    status: str = Field(description="active / cleared；清除保留历史行（Q08）")


class EnsureAgendaRequest(BaseModel):
    """保证某日存在基于最新 planning revision 的安排（05-module-contracts ensure_agenda）。"""

    planning_revision: int | None = Field(
        default=None,
        ge=0,
        description="客户端已知的 planning revision；服务端以其判断是否需要重算",
    )


# —— 偏好 ——


class UpdateSchedulingPreferencesRequest(RevisionedRequest):
    """一次提交全部活动目标的 focus 状态与顺序（13 号第 8 节）。"""

    preferences: list[PreferenceInput] = Field(min_length=1)


@router.put(
    "/goals/scheduling-preferences",
    summary="保存目标调度偏好",
    description="一次提交活动目标 focus 状态和排序，产生新 planning revision 并触发受影响安排重算（13 号第 8 节）。",
    responses=_errors(401, 403, 404, 409, 422),
)
def update_scheduling_preferences(
    user: CurrentUserDep,
    idempotency_key: IdempotencyKeyDep,
    database: DatabaseDep,
    request: UpdateSchedulingPreferencesRequest,
) -> SchedulingPreferencesResponse:
    revision, preferences = scheduling_service.update_preferences(
        database,
        user,
        [entry.model_dump() for entry in request.preferences],
        expected_revision=request.expected_revision,
        idempotency_key=idempotency_key,
    )
    return SchedulingPreferencesResponse(
        planning_revision=revision,
        preferences=[PreferenceInput(**entry) for entry in preferences],
    )


# —— 周额度与单日额度 ——


class UpdateAvailabilityRequest(RevisionedRequest):
    """提交周额度与生效日期（05-module-contracts update_availability）。"""

    effective_from: date = Field(description="生效起始日期；旧版本保留，按日期选择")
    weekly_minutes: dict[str, int] = Field(description="周一至周日七个额度（分钟），键为 monday…sunday")


@router.put(
    "/availability",
    summary="保存周额度版本",
    description="保存新的周额度版本；已有安排需要协调时在响应中明确标记（05-module-contracts）。",
    responses=_errors(401, 403, 409, 422),
)
def update_availability(
    user: CurrentUserDep,
    idempotency_key: IdempotencyKeyDep,
    database: DatabaseDep,
    request: UpdateAvailabilityRequest,
) -> AvailabilityVersionResponse:
    result = scheduling_service.update_availability(
        database,
        user,
        effective_from=request.effective_from.isoformat(),
        weekly_minutes=request.weekly_minutes,
        expected_revision=request.expected_revision,
        idempotency_key=idempotency_key,
    )
    return AvailabilityVersionResponse(revision=result["version_no"], **result)


class OverrideDayRequest(RevisionedRequest):
    """声明单日额度（05-module-contracts override_today）。"""

    override_kind: DailyOverrideKind = Field(description="total=全天总额；remaining=从声明时点起的剩余")
    minutes: int = Field(ge=0)


@router.put(
    "/availability/dates/{local_date}",
    summary="声明单日额度",
    description="修改当天总额或剩余额度并触发协调；不足以容纳现有安排时返回可见冲突（R08、Q04）。",
    responses=_errors(401, 403, 404, 409, 422),
)
def override_today(
    user: CurrentUserDep,
    idempotency_key: IdempotencyKeyDep,
    database: DatabaseDep,
    local_date: LocalDatePath,
    request: OverrideDayRequest,
) -> DailyOverrideResponse:
    result = scheduling_service.override_day(
        database,
        user,
        local_date=local_date.isoformat(),
        override_kind=request.override_kind,
        minutes=request.minutes,
        expected_revision=request.expected_revision,
        idempotency_key=idempotency_key,
    )
    return DailyOverrideResponse(**result)


# —— 每日安排 ——


@router.get(
    "/agendas/{local_date}",
    summary="读取某日安排",
    description="返回当前安排或缺失状态；读取不直接创建作业（05-module-contracts read_today）。",
    responses=_errors(401, 404),
)
def read_agenda(user: CurrentUserDep, database: DatabaseDep, local_date: LocalDatePath) -> AgendaResponse:
    payload = scheduling_service.get_agenda(database, user, local_date.isoformat())
    current = payload["revision"]
    return AgendaResponse(
        local_date=payload["local_date"],
        planning_revision=payload["planning_revision"],
        current=AgendaRevisionView(**current) if current else None,
    )


class ConstrainTaskRequest(RevisionedRequest):
    """设置或清除单日任务约束（05-module-contracts constrain_today_task）。

    constraint_kind 为空表示清除该日该任务的既有约束。
    """

    constraint_kind: TaskDayConstraintKind | None = Field(
        default=None, description="must_do_today / locked；空值表示清除"
    )


@router.put(
    "/agendas/{local_date}/task-constraints/{task_id}",
    summary="设置单日任务约束",
    description="设置 must_do_today 或 locked，或清除既有约束；无法满足时返回容量冲突（13 号第 4 节）。",
    responses=_errors(401, 403, 404, 409, 422),
)
def constrain_today_task(
    user: CurrentUserDep,
    idempotency_key: IdempotencyKeyDep,
    database: DatabaseDep,
    local_date: LocalDatePath,
    task_id: TaskIdPath,
    request: ConstrainTaskRequest,
) -> TaskConstraintResponse:
    result = scheduling_service.constrain_task(
        database,
        user,
        local_date=local_date.isoformat(),
        task_id=task_id,
        constraint_kind=request.constraint_kind,
        expected_revision=request.expected_revision,
        idempotency_key=idempotency_key,
    )
    return TaskConstraintResponse(**result)


@router.post(
    "/agendas/{local_date}/generation",
    summary="生成某日安排",
    description="保证该日期存在基于最新 planning revision 的安排；已有有效安排则去重返回（13 号第 8 节）。"
    "异步：返回 202 与作业引用。",
    status_code=202,
    responses=_errors(401, 403, 404, 409, 422),
)
def ensure_agenda(
    user: CurrentUserDep,
    idempotency_key: IdempotencyKeyDep,
    database: DatabaseDep,
    publisher: JobPublisherDep,
    local_date: LocalDatePath,
    request: EnsureAgendaRequest,
) -> JobResponse:
    view = scheduling_service.ensure_agenda(
        database,
        user,
        local_date=local_date.isoformat(),
        planning_revision_hint=request.planning_revision,
        idempotency_key=idempotency_key,
        publisher=publisher,
    )
    return _job_response(view)
