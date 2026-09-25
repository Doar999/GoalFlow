"""T08 对话 HTTP 契约桩；消息写入与澄清作业在后续业务 PR 接线。"""

from datetime import datetime
from typing import Annotated, Any, Final, NoReturn

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field

from goalflow.api.dependencies import CurrentUserDep, DatabaseDep, require_idempotency_key
from goalflow.api.routes.jobs import JobResponse
from goalflow.contracts.enums import MessageRole
from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.contracts.http import ErrorResponse, RevisionedRequest, RevisionedResource

router = APIRouter(tags=["conversations"])

_ERROR_DESCRIPTIONS: Final = {
    401: "未登录或会话失效",
    403: "请求来源不受信任",
    404: "目标或对话不存在，或不属于当前用户",
    409: "对话版本已更新，或 Idempotency-Key 已用于另一项请求",
    422: "请求参数校验未通过",
    500: "对话业务接线尚未实现",
    503: "默认模型配置不可用",
}


def _errors(*codes: int) -> dict[int | str, dict[str, Any]]:
    return {code: {"model": ErrorResponse, "description": _ERROR_DESCRIPTIONS[code]} for code in codes}


GoalIdPath = Annotated[str, Path(max_length=36, description="目标 ID")]
ConversationIdPath = Annotated[str, Path(max_length=36, description="对话 ID")]
IdempotencyKeyDep = Annotated[str, Depends(require_idempotency_key)]


class ConversationResponse(RevisionedResource):
    id: str
    goal_id: str | None
    task_id: str | None
    created_at: datetime
    updated_at: datetime


class GoalConversationsResponse(BaseModel):
    items: list[ConversationResponse]


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    sequence: int = Field(ge=1)
    role: MessageRole
    content: str
    job_id: str | None
    created_at: datetime


class MessagesResponse(BaseModel):
    items: list[MessageResponse]
    next_after_sequence: int | None = Field(description="非空表示还有消息，下一页以此值作为 after_sequence")


class SubmitMessageRequest(RevisionedRequest):
    content: str = Field(min_length=1, max_length=20000, description="用户补充的澄清文本")


class SubmittedMessageResponse(BaseModel):
    message: MessageResponse
    job: JobResponse


def _contract_stub() -> NoReturn:
    raise GoalflowError(ErrorCode.INTERNAL_ERROR, "T08 对话业务接线尚未交付")


@router.get(
    "/api/goals/{goal_id}/conversations",
    summary="读取目标的澄清对话",
    description="按创建时间列出目标关联的对话；路径目标必须属于当前用户。",
    responses=_errors(401, 404, 500),
)
def list_goal_conversations(goal_id: GoalIdPath, user: CurrentUserDep, db: DatabaseDep) -> GoalConversationsResponse:
    _contract_stub()


@router.get(
    "/api/conversations/{conversation_id}/messages",
    summary="按顺序读取澄清消息",
    description="刷新页面后按对话内 sequence 补读消息；消息只属于当前用户。",
    responses=_errors(401, 404, 422, 500),
)
def list_messages(
    conversation_id: ConversationIdPath,
    user: CurrentUserDep,
    db: DatabaseDep,
    after_sequence: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> MessagesResponse:
    _contract_stub()


@router.post(
    "/api/conversations/{conversation_id}/messages",
    summary="提交澄清消息",
    description="原子保存用户消息与澄清作业；首次及幂等重放均返回 202 和同一作业引用。",
    status_code=202,
    responses=_errors(401, 403, 404, 409, 422, 500, 503),
)
def submit_message(
    conversation_id: ConversationIdPath,
    request: SubmitMessageRequest,
    user: CurrentUserDep,
    db: DatabaseDep,
    idempotency_key: IdempotencyKeyDep,
) -> SubmittedMessageResponse:
    _contract_stub()
