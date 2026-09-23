"""作业接口（T07 PR-3）：查询、事件流、取消。取舍见 docs/worklog/T07-job-execution.md 决策 E15、E19—E21、E29—E31。

他人的作业与不存在的作业一律返回 404，响应无法区分（E21）。
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Header, Path
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from goalflow.api.dependencies import CurrentUserDep, DatabaseDep, require_idempotency_key
from goalflow.contracts.enums import JobEventType, JobStatus
from goalflow.contracts.errors import ErrorCode
from goalflow.contracts.http import ErrorResponse, RevisionedRequest, RevisionedResource
from goalflow.idempotency import IdempotentRequest
from goalflow.jobs import (
    Heartbeat,
    JobEventView,
    JobView,
    StreamSettings,
    cancel_job,
    follow_events,
    get_job,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_ERROR_DESCRIPTIONS: Final = {
    401: "未登录或会话失效",
    403: "请求来源不受信任",
    404: "作业不存在，或不属于当前用户",
    409: "作业版本已更新（REVISION_CONFLICT），或 Idempotency-Key 已用于另一项请求（IDEMPOTENCY_KEY_CONFLICT）",
    422: "请求参数校验未通过",
}


def _errors(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    return {code: {"model": ErrorResponse, "description": _ERROR_DESCRIPTIONS[code]} for code in status_codes}


JobIdPath = Annotated[str, Path(max_length=36, description="作业 ID")]


class JobResultRef(BaseModel):
    type: str = Field(description="结果资源的类型，例如 route_set")
    id: str


class JobResponse(RevisionedResource):
    """作业的当前状态。只含展示与恢复所需的字段，不含输入快照。"""

    id: str
    kind: str = Field(description="作业种类，由提交它的业务模块定义")
    status: JobStatus
    attempts: int = Field(ge=0, description="已经开始过的尝试次数，最多 3 次")
    result_refs: list[JobResultRef] = Field(description="成功后产出的资源引用；未成功时为空")
    error_code: ErrorCode | None = Field(description="失败、重试等待或过期时的错误码")
    error_message: str | None = Field(description="面向用户的错误说明")
    cancel_requested: bool = Field(description="已请求取消但作业仍在运行，等待 Worker 在下一个检查点停下")
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


class CancelJobRequest(RevisionedRequest):
    pass


class JobEventMessage(BaseModel):
    """事件流里每条消息的 data 字段（JSON）。SSE 的 id 为 sequence，event 为 type。"""

    sequence: int = Field(ge=1, description="单个作业内从 1 起连续递增；断线重连时作为 Last-Event-ID 发回")
    type: JobEventType = Field(description="completed、failed、cancelled、stale 是终态事件，收到后流随即结束")
    payload: dict[str, Any] = Field(description="随事件类型而定，例如 stage_completed 带 stage 名")
    created_at: datetime


def _job_response(job: JobView) -> JobResponse:
    return JobResponse(
        id=job.id,
        kind=job.kind,
        status=job.status,
        revision=job.revision,
        attempts=job.attempts,
        result_refs=[JobResultRef(type=ref.type, id=ref.id) for ref in job.result_refs],
        error_code=ErrorCode(job.error_code) if job.error_code is not None else None,
        error_message=job.error_message,
        cancel_requested=job.cancel_requested,
        created_at=job.created_at,
        updated_at=job.updated_at,
        finished_at=job.finished_at,
    )


def get_job_stream_settings() -> StreamSettings:
    """测试里覆盖它，把轮询、心跳与时长上限缩到毫秒级。"""
    return StreamSettings()


class EventSourceResponse(StreamingResponse):
    media_type = "text/event-stream"


_KEEP_ALIVE: Final = ": keep-alive\n\n"


def _sse(event: JobEventView) -> str:
    data = JobEventMessage(
        sequence=event.sequence,
        type=event.type,
        payload=dict(event.payload),
        created_at=event.created_at,
    ).model_dump(mode="json")
    return f"id: {event.sequence}\nevent: {event.type.value}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get(
    "/{job_id}",
    summary="查询作业",
    description="刷新页面、断线或事件流结束后，用它取得作业的最终状态与结果引用。",
    responses=_errors(401, 404, 422),
)
def inspect_job(job_id: JobIdPath, user: CurrentUserDep, database: DatabaseDep) -> JobResponse:
    return _job_response(get_job(database, owner_id=user.user_id, job_id=job_id))


@router.get(
    "/{job_id}/events",
    summary="订阅作业事件（SSE）",
    description=(
        "text/event-stream。每条消息的 id 为 sequence、event 为事件类型、data 为 JobEventMessage 的 JSON。"
        "先补发 Last-Event-ID 之后的全部持久化事件，再持续推送新事件；收到终态事件后服务端关闭连接。"
        "无新事件时每 15 秒发一行注释作为心跳；单条连接最长 5 分钟，之后由客户端重连。断开连接不会取消作业。"
    ),
    response_class=EventSourceResponse,
    responses={
        200: {"model": JobEventMessage, "description": "事件流；schema 描述的是每条消息的 data"},
        **_errors(401, 404, 422),
    },
)
def watch_job(
    job_id: JobIdPath,
    user: CurrentUserDep,
    database: DatabaseDep,
    settings: Annotated[StreamSettings, Depends(get_job_stream_settings)],
    last_event_id: Annotated[
        int | None,
        Header(alias="Last-Event-ID", ge=0, description="已收到的最后一个 sequence；浏览器重连时自动携带"),
    ] = None,
) -> EventSourceResponse:
    # 归属必须在流开始之前确认：响应头一旦发出，就不能再以 404 返回了。
    get_job(database, owner_id=user.user_id, job_id=job_id)

    def stream() -> Iterator[str]:
        for item in follow_events(
            database,
            owner_id=user.user_id,
            job_id=job_id,
            after_sequence=last_event_id or 0,
            settings=settings,
        ):
            yield _KEEP_ALIVE if isinstance(item, Heartbeat) else _sse(item)

    # X-Accel-Buffering：让 Nginx 不缓冲这条响应，事件才能即时到达（部署配置见 T13）。
    return EventSourceResponse(stream(), headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post(
    "/{job_id}/cancellation",
    summary="取消作业",
    description=(
        "排队或等待重试中的作业立即取消；运行中的作业记下取消请求，在下一个检查点停下。"
        "已结束的作业原样返回当前状态，已成功发布的结果不会被撤销。"
    ),
    responses=_errors(401, 403, 404, 409, 422),
)
def request_job_cancellation(
    job_id: JobIdPath,
    body: CancelJobRequest,
    user: CurrentUserDep,
    database: DatabaseDep,
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
) -> JobResponse:
    request = IdempotentRequest.build(
        owner_id=user.user_id,
        operation="cancel_job",
        key=idempotency_key,
        body=body,
        path_params={"job_id": job_id},
    )
    job = cancel_job(
        database,
        owner_id=user.user_id,
        job_id=job_id,
        expected_revision=body.expected_revision,
        now=datetime.now(UTC),
        idempotency=request,
    )
    return _job_response(job)
