"""账号接口（T03）。取舍见 docs/worklog/T03-auth-session.md 第 4 节。

这组接口不要求 Idempotency-Key（决策 C12）：注册时用户尚不存在，按 owner 划分的去重表用不上；
重复注册由唯一约束兜底，退出与撤销本身就是幂等的。

会话令牌只经 HttpOnly Cookie 传递，任何响应体都不包含它。
"""

from datetime import datetime
from typing import Any, Final

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field, SecretStr

from goalflow.api.dependencies import (
    AuthServiceDep,
    CurrentUserDep,
    client_info,
    require_trusted_origin,
    session_token,
)
from goalflow.auth.service import AuthService, CurrentUser, IssuedSession
from goalflow.contracts.enums import UserRole
from goalflow.contracts.http import ErrorResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 传输层上限只防超长输入进入规范化与哈希；真正的长度规则在账号模块里，违反时给出明确的字段说明。
_TRANSPORT_MAX_LENGTH: Final = 1024

_ERROR_DESCRIPTIONS: Final = {
    401: "未登录、会话失效或凭证错误",
    403: "请求来源不受信任，或本实例已关闭注册",
    409: "账号标识不可用",
    422: "请求参数校验未通过",
    429: "尝试次数过多，响应头 Retry-After 给出需等待的秒数",
}


def _errors(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    return {code: {"model": ErrorResponse, "description": _ERROR_DESCRIPTIONS[code]} for code in status_codes}


class RegisterRequest(BaseModel):
    account_identifier: str = Field(
        max_length=_TRANSPORT_MAX_LENGTH,
        description="账号标识，可以是用户名或邮箱格式（首版不验证邮箱）。规范化后需为 3–254 个字符，不含空白",
    )
    password: SecretStr = Field(max_length=_TRANSPORT_MAX_LENGTH, description="12–128 个字符，不得与账号标识相同")
    timezone: str | None = Field(
        default=None,
        max_length=64,
        description="IANA 时区名，例如 Asia/Shanghai。省略时为 UTC",
    )


class LoginRequest(BaseModel):
    account_identifier: str = Field(max_length=_TRANSPORT_MAX_LENGTH)
    password: SecretStr = Field(max_length=_TRANSPORT_MAX_LENGTH)


class ChangePasswordRequest(BaseModel):
    current_password: SecretStr = Field(max_length=_TRANSPORT_MAX_LENGTH)
    new_password: SecretStr = Field(max_length=_TRANSPORT_MAX_LENGTH, description="12–128 个字符，不得与账号标识相同")


class ResetPasswordRequest(BaseModel):
    token: SecretStr = Field(max_length=_TRANSPORT_MAX_LENGTH, description="部署者用本地命令签发的一次性重置令牌")
    new_password: SecretStr = Field(max_length=_TRANSPORT_MAX_LENGTH, description="12–128 个字符，不得与账号标识相同")


class AccountUser(BaseModel):
    id: str
    account_identifier: str = Field(description="规范化后的账号标识")
    role: UserRole
    timezone: str = Field(description="IANA 时区名")


class SessionInfo(BaseModel):
    created_at: datetime
    expires_at: datetime = Field(description="绝对过期时间。7 天无活动会更早失效")


class AuthSessionResponse(BaseModel):
    """当前用户与会话元数据。不包含会话令牌。"""

    user: AccountUser
    session: SessionInfo


class RegistrationStatusResponse(BaseModel):
    registration_open: bool = Field(description="本实例是否接受新用户注册。关闭后已有用户仍可登录")


def _session_response(current: CurrentUser) -> AuthSessionResponse:
    return AuthSessionResponse(
        user=AccountUser(
            id=current.user_id,
            account_identifier=current.account_identifier,
            role=current.role,
            timezone=current.timezone,
        ),
        session=SessionInfo(created_at=current.session_created_at, expires_at=current.session_expires_at),
    )


def _set_session_cookie(response: Response, service: AuthService, issued: IssuedSession) -> None:
    response.set_cookie(
        key=service.config.cookie_name,
        value=issued.token,
        max_age=service.cookie_max_age(issued),
        path="/",
        secure=service.config.secure_cookies,
        httponly=True,
        samesite="lax",
    )


def _clear_session_cookie(response: Response, service: AuthService) -> None:
    response.delete_cookie(
        key=service.config.cookie_name,
        path="/",
        secure=service.config.secure_cookies,
        httponly=True,
        samesite="lax",
    )


@router.get(
    "/registration",
    summary="注册是否开放",
    description="登录与注册页据此决定是否展示注册入口，与注册接口的行为保持一致。",
)
def read_registration_status(service: AuthServiceDep) -> RegistrationStatusResponse:
    return RegistrationStatusResponse(registration_open=service.registration_open())


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    summary="注册并登录",
    description="创建账号并建立会话，会话令牌通过 Cookie 下发。首位注册者不会自动成为管理员。",
    dependencies=[Depends(require_trusted_origin)],
    responses=_errors(403, 409, 422, 429),
)
def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    service: AuthServiceDep,
) -> AuthSessionResponse:
    issued = service.register(
        body.account_identifier,
        body.password.get_secret_value(),
        body.timezone,
        client_info(request),
        replaced_token=session_token(request, service),
    )
    _set_session_cookie(response, service, issued)
    return _session_response(issued.user)


@router.post(
    "/login",
    summary="登录",
    description="账号不存在、密码错误、账号已禁用统一返回 INVALID_CREDENTIALS。已登录时再次登录会撤销旧会话。",
    dependencies=[Depends(require_trusted_origin)],
    responses=_errors(401, 403, 422, 429),
)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    service: AuthServiceDep,
) -> AuthSessionResponse:
    issued = service.login(
        body.account_identifier,
        body.password.get_secret_value(),
        client_info(request),
        replaced_token=session_token(request, service),
    )
    _set_session_cookie(response, service, issued)
    return _session_response(issued.user)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="退出当前会话",
    description="未登录或会话已失效时同样返回 204。",
    dependencies=[Depends(require_trusted_origin)],
    responses=_errors(403),
)
def logout(request: Request, response: Response, service: AuthServiceDep) -> None:
    service.logout(session_token(request, service))
    _clear_session_cookie(response, service)


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="退出全部设备",
    description="撤销该用户的全部会话，包括当前会话。",
    responses=_errors(401, 403),
)
def logout_all(current: CurrentUserDep, response: Response, service: AuthServiceDep) -> None:
    service.logout_all(current)
    _clear_session_cookie(response, service)


@router.get(
    "/session",
    summary="当前用户与会话",
    responses=_errors(401),
)
def read_session(current: CurrentUserDep) -> AuthSessionResponse:
    return _session_response(current)


@router.post(
    "/password/change",
    summary="修改密码",
    description="成功后撤销该用户的全部旧会话，并为当前设备换发新会话 Cookie。",
    responses=_errors(401, 403, 422, 429),
)
def change_password(
    body: ChangePasswordRequest,
    current: CurrentUserDep,
    request: Request,
    response: Response,
    service: AuthServiceDep,
) -> AuthSessionResponse:
    issued = service.change_password(
        current,
        body.current_password.get_secret_value(),
        body.new_password.get_secret_value(),
        client_info(request),
    )
    _set_session_cookie(response, service, issued)
    return _session_response(issued.user)


@router.post(
    "/password/reset-with-token",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="凭重置令牌设置新密码",
    description="令牌一次性、30 分钟内有效。成功后该用户全部会话失效，需重新登录。",
    dependencies=[Depends(require_trusted_origin)],
    responses=_errors(401, 403, 422, 429),
)
def reset_password_with_token(
    body: ResetPasswordRequest,
    request: Request,
    response: Response,
    service: AuthServiceDep,
) -> None:
    service.reset_password_with_token(
        body.token.get_secret_value(),
        body.new_password.get_secret_value(),
        client_info(request),
    )
    _clear_session_cookie(response, service)
