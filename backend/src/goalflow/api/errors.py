"""全局异常处理器：把任何异常转成统一错误结构。

路由里不要手写错误 JSON，见 docs/engineering/03-code-and-test-standards.md 第 2 节。
"""

import logging
from typing import Any, Final

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from goalflow.contracts.errors import HTTP_STATUS_BY_ERROR_CODE, RETRYABLE_ERROR_CODES, ErrorCode, GoalflowError
from goalflow.contracts.http import REQUEST_ID_HEADER, ErrorResponse
from goalflow.core.context import get_request_id, new_request_id

_logger: Final = logging.getLogger(__name__)

_ERROR_CODE_BY_HTTP_STATUS: Final[dict[int, ErrorCode]] = {
    401: ErrorCode.UNAUTHENTICATED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.REVISION_CONFLICT,
    422: ErrorCode.VALIDATION_FAILED,
}

_INTERNAL_ERROR_MESSAGE: Final = "服务内部错误，请稍后重试"


def _resolve_request_id(request: Request) -> str:
    """取本次请求的标识。三层兜底保证错误体里 request_id 永远不为空。"""
    from_state = getattr(request.state, "request_id", None)
    if isinstance(from_state, str) and from_state:
        return from_state
    return get_request_id() or new_request_id()


def _build_response(
    request: Request,
    *,
    code: ErrorCode,
    message: str,
    http_status: int,
    details: dict[str, Any] | None = None,
) -> Response:
    request_id = _resolve_request_id(request)
    body = ErrorResponse(
        code=code,
        message=message,
        request_id=request_id,
        retryable=code in RETRYABLE_ERROR_CODES,
        details=details or {},
    )
    # ServerErrorMiddleware 在 RequestIdMiddleware 外层，它发出的响应不经过那里的
    # send 包装，所以这里必须自己补响应头。
    return JSONResponse(
        status_code=http_status,
        content=body.model_dump(mode="json"),
        headers={REQUEST_ID_HEADER: request_id},
    )


async def _handle_goalflow_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, GoalflowError)
    return _build_response(
        request,
        code=exc.code,
        message=exc.message,
        http_status=exc.http_status,
        details=exc.details,
    )


async def _handle_validation_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, RequestValidationError)
    # 只保留位置、说明与类型。FastAPI 原始错误里的 "input" 会把请求体原样回显，
    # 那可能含口令或凭证。
    fields = [
        {
            "location": ".".join(str(part) for part in error.get("loc", ())),
            "message": error.get("msg", ""),
            "type": error.get("type", ""),
        }
        for error in exc.errors()
    ]
    return _build_response(
        request,
        code=ErrorCode.VALIDATION_FAILED,
        message="请求参数校验未通过",
        http_status=HTTP_STATUS_BY_ERROR_CODE[ErrorCode.VALIDATION_FAILED],
        details={"fields": fields},
    )


async def _handle_http_exception(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, StarletteHTTPException)
    if exc.status_code >= 500:
        code = ErrorCode.INTERNAL_ERROR
        message = _INTERNAL_ERROR_MESSAGE
    else:
        code = _ERROR_CODE_BY_HTTP_STATUS.get(exc.status_code, ErrorCode.VALIDATION_FAILED)
        message = exc.detail if isinstance(exc.detail, str) and exc.detail else code.value
    return _build_response(request, code=code, message=message, http_status=exc.status_code)


async def _handle_unexpected_error(request: Request, exc: Exception) -> Response:
    # 堆栈进日志，不进响应体。
    _logger.exception("未处理异常", extra={"path": request.url.path, "method": request.method})
    return _build_response(
        request,
        code=ErrorCode.INTERNAL_ERROR,
        message=_INTERNAL_ERROR_MESSAGE,
        http_status=HTTP_STATUS_BY_ERROR_CODE[ErrorCode.INTERNAL_ERROR],
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(GoalflowError, _handle_goalflow_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(Exception, _handle_unexpected_error)
