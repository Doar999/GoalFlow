"""存活检查。

本工作包唯一的端点，用于让 OpenAPI 导出、类型生成与漂移检查有真实对象可跑，
见 T02 交接卡决策 A3。业务端点属于 T03 及之后的工作包。
"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from goalflow.contracts.http import ErrorResponse

router = APIRouter(prefix="/api", tags=["meta"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = Field(description="进程可响应请求时固定为 ok")


@router.get(
    "/health",
    summary="存活检查",
    description="不校验会话，不访问数据库。只表示进程能响应请求。",
    responses={500: {"model": ErrorResponse, "description": "服务内部错误"}},
)
def read_health() -> HealthResponse:
    return HealthResponse(status="ok")
