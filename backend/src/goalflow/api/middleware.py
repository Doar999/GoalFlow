"""请求级中间件。"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from goalflow.contracts.http import REQUEST_ID_HEADER
from goalflow.core.context import bind_request_id, new_request_id, reset_request_id


class RequestIdMiddleware:
    """给每个请求分配 request_id，写入上下文、响应头与 scope state。

    request_id 一律服务端生成，不采信客户端传入的同名请求头——它会进入日志，
    采信外部值等于把日志内容的控制权交出去。

    写 scope state 而不只写 ContextVar，是因为未处理异常会先穿过本中间件的 finally
    （ContextVar 已复位）再由 ServerErrorMiddleware 处理，那时只有 state 还留着值。

    用纯 ASGI 而非 BaseHTTPMiddleware：后者把下游放进另一个任务，
    上下文只能单向传递，异常路径上的行为也更难推理。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = new_request_id()
        scope.setdefault("state", {})["request_id"] = request_id
        token = bind_request_id(request_id)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            reset_request_id(token)
