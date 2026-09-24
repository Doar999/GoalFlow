"""目标关联与变更提案接口（T06 契约 PR）：提出/确认关联、解除提案与提案终态。

端点清单的出处：

- 18-goal-link-design.md 第 6 节：POST /api/goals/{id}/link-proposals、
  POST /api/goal-links/{id}/confirm、POST /api/goal-links/{id}/unlink-proposals，
  解除的接受与拒绝复用 change-proposals accept|reject，不新增终态接口；
- GET /api/change-proposals/{id} 为本包补充的最小读取端点（交接卡决策 A7）：
  用户确认或拒绝前必须能读到影响分析。

端点本期交付契约形状（桩），业务实现在 T06 PR-2 接入（交接卡决策 A7）。
"""

from datetime import datetime
from typing import Annotated, Any, Final, NoReturn

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field

from goalflow.api.dependencies import CurrentUserDep, require_idempotency_key
from goalflow.contracts.enums import ChangeClass, ChangeProposalStatus, DependencyOutcome, GoalLinkStatus
from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.contracts.http import ErrorResponse

router = APIRouter(tags=["goal-links"])

_ERROR_DESCRIPTIONS: Final = {
    401: "未登录或会话失效",
    403: "请求来源不受信任",
    404: "目标、关联或提案不存在，或不属于当前用户",
    409: (
        "加入新依赖边后依赖图成环（DEPENDENCY_CYCLE）、Idempotency-Key 已用于另一项请求"
        "（IDEMPOTENCY_KEY_CONFLICT）、提案基础版本已变（INPUT_STALE）"
        "或提案状态不允许该操作（VALIDATION_FAILED 语义下的终态冲突）"
    ),
    422: "请求参数校验未通过",
    500: "接口尚未实现（业务实现随 T06 PR-2 接入交付）",
}


def _errors(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    return {code: {"model": ErrorResponse, "description": _ERROR_DESCRIPTIONS[code]} for code in status_codes}


GoalIdPath = Annotated[str, Path(max_length=36, description="目标 ID")]
LinkOrProposalIdPath = Annotated[str, Path(max_length=36, description="关联或提案 ID")]
IdempotencyKeyDep = Annotated[str, Depends(require_idempotency_key)]


def _implementation_stub() -> NoReturn:
    """契约桩：业务实现随 T06 PR-2 接入（交接卡决策 A7）。"""
    raise GoalflowError(
        ErrorCode.INTERNAL_ERROR,
        "该端点随 T06 业务实现（PR-2）接入交付",
    )


# —— 请求体 ——


class TaskDependencyEdge(BaseModel):
    """关联携带的一条具体任务依赖（18 号第 2 节：不接受只有两个 goal_id 的"相关"声明）。"""

    predecessor_task_id: str = Field(max_length=36, description="前驱任务（须先完成/通过的一方）")
    successor_task_id: str = Field(max_length=36, description="后继任务")
    required_outcome: DependencyOutcome = Field(description="满足条件：执行完成或验证通过")


class ProposeGoalLinkRequest(BaseModel):
    """提出关联。必须携带具体任务关系（18 号第 2 节）。"""

    target_goal_id: str = Field(max_length=36, description="关联对方目标；连同路径 goal_id 规范化为目标对")
    dependencies: list[TaskDependencyEdge] = Field(
        min_length=1,
        description="具体的任务先后依赖边；至少一条。此时仅创建 proposed 关联，确认后才建立依赖边",
    )


class ConfirmGoalLinkRequest(BaseModel):
    """确认建立关联。确认时在同一事务内校验同用户、不自关联、目标对不重复与全图无环（18 号第 2 节）。"""

    pass


class ProposeUnlinkRequest(BaseModel):
    """生成解除提案及影响分析（18 号第 3 节）。操作本身不被未完成依赖阻止。"""

    pass


class ChangeProposalActionRequest(BaseModel):
    """提案终态动作（接受 / 拒绝）。接受和应用在同一事务完成（03 第 3 节）。"""

    pass


# —— 响应 ——


class GoalLinkResponse(BaseModel):
    """目标关联当前状态。removed 行保留归档（T06 决策 A2）。"""

    id: str
    goal_a_id: str = Field(description="规范化目标对的较小 id 一方")
    goal_b_id: str = Field(description="规范化目标对的较大 id 一方")
    status: GoalLinkStatus
    confirmed_at: datetime | None = Field(description="确认时间；proposed 阶段为空")
    created_at: datetime
    updated_at: datetime


class UnlinkImpact(BaseModel):
    """解除提案的影响分析（18 号第 3 节）。结构随业务实现细化，首版用说明性键。"""

    unsatisfied_edges: list[dict[str, Any]] = Field(
        description="该关联下未满足的依赖边清单，每条含后继任务及其当前执行状态"
    )
    criteria_unsatisfiable: list[dict[str, Any]] = Field(
        description="完成标准引用了前驱产出、解除后将不可执行的后继任务"
    )
    affected_milestones: list[dict[str, Any]] = Field(description="受影响的里程碑与期限")
    satisfied_edges: list[dict[str, Any]] = Field(description="已满足的依赖边；随关联归档，无需处理")


class ChangeProposalResponse(BaseModel):
    """变更提案。解除关联时 change_class=confirmation_required（改变成功标准时升级，18 号第 3 节）。"""

    id: str
    goal_id: str
    change_class: ChangeClass
    status: ChangeProposalStatus = Field(description="提案基础版本已变时为 stale，不沿用旧确认")
    input_revision: int = Field(description="提案生成时的 planning revision；落后于当前值即为 stale（T06 决策 A6）")
    impact: UnlinkImpact
    reason: str | None
    created_at: datetime
    accepted_at: datetime | None
    applied_at: datetime | None


# —— 端点 ——


@router.post(
    "/api/goals/{goal_id}/link-proposals",
    summary="提出目标关联",
    description=(
        "创建 proposed 关联，必须携带具体的任务依赖关系（18 号第 2 节）。两目标须同属当前用户；目标对规范化存储。"
    ),
    status_code=201,
    responses=_errors(401, 403, 404, 409, 422, 500),
)
def propose_goal_link(
    user: CurrentUserDep,
    goal_id: GoalIdPath,
    request: ProposeGoalLinkRequest,
    idempotency_key: IdempotencyKeyDep,
) -> GoalLinkResponse:
    _implementation_stub()


@router.post(
    "/api/goal-links/{link_id}/confirm",
    summary="确认建立关联",
    description=(
        "proposed → active，并在同一事务内建立依赖边。确认时校验同用户、不自关联、"
        "目标对未重复与全图无环（含其他目标的当前版本）；成环返回 DEPENDENCY_CYCLE（18 号第 2 节）。"
    ),
    responses=_errors(401, 403, 404, 409, 422, 500),
)
def confirm_goal_link(
    user: CurrentUserDep,
    link_id: LinkOrProposalIdPath,
    request: ConfirmGoalLinkRequest,
    idempotency_key: IdempotencyKeyDep,
) -> GoalLinkResponse:
    _implementation_stub()


@router.post(
    "/api/goal-links/{link_id}/unlink-proposals",
    summary="生成解除提案",
    description=(
        "计算并返回解除影响：未满足依赖边、后继任务执行状态、criteria_unsatisfiable 标记、"
        "受影响的里程碑与期限；已满足边随关联归档（18 号第 3 节）。操作不被未完成依赖阻止。"
    ),
    status_code=201,
    responses=_errors(401, 403, 404, 409, 422, 500),
)
def propose_unlink(
    user: CurrentUserDep,
    link_id: LinkOrProposalIdPath,
    request: ProposeUnlinkRequest,
    idempotency_key: IdempotencyKeyDep,
) -> ChangeProposalResponse:
    _implementation_stub()


@router.get(
    "/api/change-proposals/{proposal_id}",
    summary="读取变更提案",
    description="读取提案状态与影响分析；确认或拒绝前用户须能看到完整影响（T06 决策 A7）。",
    responses=_errors(401, 404),
)
def read_change_proposal(user: CurrentUserDep, proposal_id: LinkOrProposalIdPath) -> ChangeProposalResponse:
    _implementation_stub()


@router.post(
    "/api/change-proposals/{proposal_id}/accept",
    summary="接受变更提案",
    description=(
        "接受并在同一事务内原子应用：置关联 removed、删除依赖边、处理 criteria_unsatisfiable "
        "的未开始任务、写审计并递增 planning revision（18 号第 3 节）。中途失败两者都不变。"
    ),
    responses=_errors(401, 403, 404, 409, 422, 500),
)
def accept_change_proposal(
    user: CurrentUserDep,
    proposal_id: LinkOrProposalIdPath,
    request: ChangeProposalActionRequest,
    idempotency_key: IdempotencyKeyDep,
) -> ChangeProposalResponse:
    _implementation_stub()


@router.post(
    "/api/change-proposals/{proposal_id}/reject",
    summary="拒绝变更提案",
    description="拒绝后提案置 rejected，关联与依赖保持原样，问题仍然可见（产品 13 号第 4 节）。",
    responses=_errors(401, 403, 404, 409, 422, 500),
)
def reject_change_proposal(
    user: CurrentUserDep,
    proposal_id: LinkOrProposalIdPath,
    request: ChangeProposalActionRequest,
    idempotency_key: IdempotencyKeyDep,
) -> ChangeProposalResponse:
    _implementation_stub()
