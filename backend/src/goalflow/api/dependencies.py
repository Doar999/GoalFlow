"""路由共用的 FastAPI 依赖。"""

from functools import lru_cache
from typing import Annotated, Final

from fastapi import Depends, Header, Request

from goalflow.auth.service import AuthConfig, AuthService, ClientInfo, CurrentUser
from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.contracts.http import IDEMPOTENCY_KEY_HEADER, normalize_idempotency_key
from goalflow.core.config import get_settings
from goalflow.db.session import get_database

_SAFE_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS"})


@lru_cache(maxsize=1)
def get_auth_service() -> AuthService:
    """进程级单例，持有限流计数。测试里用 `app.dependency_overrides` 换成指向临时库的实例。"""
    return AuthService(get_database(), AuthConfig.from_settings(get_settings()))


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def client_info(request: Request) -> ClientInfo:
    # 反向代理之后的真实 IP 由 uvicorn --proxy-headers 负责，这里不自己解析 X-Forwarded-For（决策 C19）。
    return ClientInfo(
        ip=request.client.host if request.client is not None else None,
        user_agent=request.headers.get("user-agent"),
    )


def session_token(request: Request, service: AuthService) -> str | None:
    return request.cookies.get(service.config.cookie_name)


def require_trusted_origin(request: Request, service: AuthServiceDep) -> None:
    """写请求的 CSRF 防护（决策 C7）。安全方法直接放行。"""
    if request.method in _SAFE_METHODS:
        return
    service.verify_request_origin(request.headers.get("origin"), request.headers.get("sec-fetch-site"))


def require_current_user(
    request: Request,
    service: AuthServiceDep,
    _trusted_origin: Annotated[None, Depends(require_trusted_origin)],
) -> CurrentUser:
    """请求身份只从服务端会话获得（01-contracts 第 5 节）。

    来源校验挂在这里而不是各个路由上：凡是凭会话 Cookie 发起的写请求都必须经过它，
    后续模块只要用了本依赖，就自动得到 CSRF 防护。
    """
    user = service.authenticate(session_token(request, service))
    if user is None:
        raise GoalflowError(ErrorCode.UNAUTHENTICATED, "请先登录")
    return user


CurrentUserDep = Annotated[CurrentUser, Depends(require_current_user)]


def require_idempotency_key(
    raw_key: Annotated[str | None, Header(alias=IDEMPOTENCY_KEY_HEADER)] = None,
) -> str:
    """写操作的幂等键。缺失或格式非法时返回 VALIDATION_FAILED。

    见 docs/engineering/01-contracts-and-ownership.md 第 5 节。
    本依赖只负责取值与校验；"相同 key 不同内容返回冲突"需要比对已记录的请求，
    属于存储层，见 T02 交接卡决策 A4。
    """
    return normalize_idempotency_key(raw_key)
