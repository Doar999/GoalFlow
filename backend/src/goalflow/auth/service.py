"""账号模块的 Interface：注册、登录、会话校验与撤销、改密、重置，以及部署者本地操作。

事务边界归这里（T03 决策 C3）：每个方法自己开 `read()` / `write()`，路由只做参数校验和调用。
所有方法都遵守同一个顺序，这是本模块唯一必须记住的规矩：

    限流 → 规则校验 → 读事务取数据 → **事务外**算或校验 Argon2 → 写事务里重新确认并落库

Argon2 一次几十毫秒，SQLite 的写锁是库级的；把它放进写事务，全站的写都会跟着排队。
"""

import ipaddress
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, cast

from sqlalchemy import Result, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from goalflow.auth import credentials
from goalflow.auth.models import LoginSession, PasswordCredential, PasswordResetToken, User
from goalflow.auth.rate_limit import (
    CHANGE_PASSWORD_PER_USER,
    LOGIN_PER_IDENTIFIER,
    LOGIN_PER_IP_PREFIX,
    REGISTER_PER_IP_PREFIX,
    RESET_PER_IP_PREFIX,
    FixedWindowRateLimiter,
)
from goalflow.contracts.enums import UserRole, UserStatus
from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.core.config import Environment, Settings
from goalflow.db.session import Database

# 决策 C4、C18。
SESSION_IDLE_TIMEOUT: Final = timedelta(days=7)
SESSION_ABSOLUTE_LIFETIME: Final = timedelta(days=30)
# 距上次写 last_seen_at 超过它才再写一次。每个请求都写，会让所有读请求都变成写请求。
LAST_SEEN_WRITE_INTERVAL: Final = timedelta(minutes=5)
RESET_TOKEN_LIFETIME: Final = timedelta(minutes=30)

# 决策 C6。`__Host-` 前缀由浏览器强制要求 Secure、Path=/、不带 Domain。
SECURE_COOKIE_NAME: Final = "__Host-goalflow_session"
INSECURE_COOKIE_NAME: Final = "goalflow_session"

_USER_AGENT_SUMMARY_LENGTH: Final = 200

_logger: Final = logging.getLogger(__name__)


class AuthConfigurationError(RuntimeError):
    """配置不足以安全运行账号体系。启动时抛出，不要等到第一个请求。"""


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class AuthConfig:
    session_secret: bytes
    allow_registration: bool
    # 空串表示不做 Origin 校验；Settings 保证生产环境一定非空且为 https。
    public_origin: str
    secure_cookies: bool

    @classmethod
    def from_settings(cls, settings: Settings) -> "AuthConfig":
        secret = settings.session_secret.get_secret_value() if settings.session_secret is not None else ""
        if not secret:
            raise AuthConfigurationError("未配置 GOALFLOW_SESSION_SECRET：会话令牌与重置令牌的摘要依赖它")
        return cls(
            session_secret=secret.encode("utf-8"),
            allow_registration=settings.allow_registration,
            public_origin=settings.public_origin,
            secure_cookies=settings.env is Environment.PRODUCTION,
        )

    @property
    def cookie_name(self) -> str:
        return SECURE_COOKIE_NAME if self.secure_cookies else INSECURE_COOKIE_NAME


@dataclass(frozen=True)
class ClientInfo:
    """请求来源。只用于限流键与会话元数据，完整 IP 不落库也不进日志（决策 C18、C19）。"""

    ip: str | None = None
    user_agent: str | None = None

    @property
    def ip_prefix(self) -> str | None:
        if not self.ip:
            return None
        try:
            address = ipaddress.ip_address(self.ip)
        except ValueError:
            return None
        prefix_length = 24 if address.version == 4 else 48
        return str(ipaddress.ip_network(f"{address}/{prefix_length}", strict=False))

    @property
    def rate_limit_key(self) -> str:
        return self.ip_prefix or "unknown"

    @property
    def user_agent_summary(self) -> str | None:
        return self.user_agent[:_USER_AGENT_SUMMARY_LENGTH] if self.user_agent else None


@dataclass(frozen=True)
class CurrentUser:
    """已通过会话校验的请求身份。业务模块只认它，不接受客户端传入的 user_id。"""

    user_id: str
    account_identifier: str
    role: UserRole
    timezone: str
    session_id: str
    session_created_at: datetime
    session_expires_at: datetime


@dataclass(frozen=True)
class IssuedSession:
    """新建的会话。`token` 是原文，只在这里出现这一次：写进 Cookie 后就丢弃。"""

    token: str
    user: CurrentUser


@dataclass(frozen=True)
class IssuedResetToken:
    token: str
    expires_at: datetime


def _new_id() -> str:
    return str(uuid.uuid4())


def _rowcount(result: Result[Any]) -> int:
    return cast("CursorResult[Any]", result).rowcount


def _detach(session: Session) -> None:
    """让读事务里取出的实体在事务结束后仍可用。

    `Database.read()` 退出时回滚，而回滚会让会话里所有实体过期（`expire_on_commit=False`
    只管提交，不管回滚）。过期实体离开会话后再读属性就是 DetachedInstanceError。
    先 expunge 再回滚，实体就带着已加载的值脱离会话。
    """
    session.expunge_all()


def _invalid_credentials(message: str = "账号或密码错误") -> GoalflowError:
    return GoalflowError(ErrorCode.INVALID_CREDENTIALS, message)


def _identifier_unavailable() -> GoalflowError:
    # 注册查重不可避免地暴露"这个标识已被占用"，靠限流缓解（决策 C11）。
    return GoalflowError(
        ErrorCode.ACCOUNT_IDENTIFIER_UNAVAILABLE,
        "该账号标识不可用，请换一个",
        details={"field": "account_identifier"},
    )


def _current_user(user: User, login_session: LoginSession) -> CurrentUser:
    return CurrentUser(
        user_id=user.id,
        account_identifier=user.account_identifier,
        role=UserRole(user.role),
        timezone=user.timezone,
        session_id=login_session.id,
        session_created_at=login_session.created_at,
        session_expires_at=login_session.expires_at,
    )


def _is_live(login_session: LoginSession, now: datetime) -> bool:
    return (
        login_session.revoked_at is None
        and now < login_session.expires_at
        and now < login_session.last_seen_at + SESSION_IDLE_TIMEOUT
    )


class AuthService:
    def __init__(
        self,
        database: Database,
        config: AuthConfig,
        *,
        clock: Callable[[], datetime] = utc_now,
        limiter: FixedWindowRateLimiter | None = None,
    ) -> None:
        self._db = database
        self._config = config
        self._clock = clock
        self._limiter = limiter or FixedWindowRateLimiter(clock)

    @property
    def config(self) -> AuthConfig:
        return self._config

    # ------------------------------------------------------------------ 请求来源

    def verify_request_origin(self, origin: str | None, sec_fetch_site: str | None) -> None:
        """非安全方法的来源校验（决策 C7）。调用方负责只对 POST/PUT/PATCH/DELETE 调用。

        有 Origin 就必须与 public_origin 逐字相等；没有 Origin 时退而看 Sec-Fetch-Site；
        两者都没有一律拒绝。"null" 这类 Origin 自然不相等，也被拒绝。
        """
        if not self._config.public_origin:
            return
        if origin is not None:
            if origin == self._config.public_origin:
                return
        elif sec_fetch_site == "same-origin":
            return
        raise GoalflowError(ErrorCode.FORBIDDEN, "请求来源不受信任")

    def cookie_max_age(self, issued: IssuedSession) -> int:
        return max(0, int((issued.user.session_expires_at - self._clock()).total_seconds()))

    # ------------------------------------------------------------------ 注册与登录

    def registration_open(self) -> bool:
        return self._config.allow_registration

    def register(
        self,
        account_identifier: str,
        password: str,
        timezone: str | None,
        client: ClientInfo,
        *,
        replaced_token: str | None = None,
    ) -> IssuedSession:
        if not self._config.allow_registration:
            raise GoalflowError(ErrorCode.REGISTRATION_CLOSED, "本实例已关闭新用户注册")
        self._limiter.hit(REGISTER_PER_IP_PREFIX, client.rate_limit_key)

        identifier = credentials.normalize_account_identifier(account_identifier)
        credentials.validate_new_password(password, identifier)
        zone = credentials.validate_timezone(timezone)

        # 预检只为省下一次哈希；真正的去重靠唯一约束，见下面的 IntegrityError。
        with self._db.read() as session:
            if session.scalar(select(User.id).where(User.account_identifier == identifier)) is not None:
                raise _identifier_unavailable()

        password_hash = credentials.hash_password(password)
        now = self._clock()
        user = User(
            id=_new_id(),
            account_identifier=identifier,
            role=UserRole.USER.value,
            status=UserStatus.ACTIVE.value,
            timezone=zone,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        try:
            with self._db.write() as session:
                session.add(user)
                session.flush()
                session.add(
                    PasswordCredential(
                        user_id=user.id,
                        password_hash=password_hash,
                        hash_scheme=credentials.HASH_SCHEME,
                        changed_at=now,
                    )
                )
                if replaced_token:
                    self._revoke_token(session, replaced_token, now)
                issued = self._open_session(session, user, client, now)
        except IntegrityError as exc:
            if "users.account_identifier" in str(exc.orig):
                raise _identifier_unavailable() from None
            raise

        _logger.info("注册成功", extra={"event": "auth.registered", "user_id": user.id, "ip_prefix": client.ip_prefix})
        return issued

    def login(
        self,
        account_identifier: str,
        password: str,
        client: ClientInfo,
        *,
        replaced_token: str | None = None,
    ) -> IssuedSession:
        """账号不存在、密码错误、账号已禁用三种失败对外完全一致，且都花一次哈希校验的时间。"""
        self._limiter.hit(LOGIN_PER_IP_PREFIX, client.rate_limit_key)
        identifier = credentials.try_normalize_account_identifier(account_identifier)
        if identifier is not None:
            self._limiter.hit(LOGIN_PER_IDENTIFIER, identifier)

        found = None
        if identifier is not None:
            with self._db.read() as session:
                found = session.execute(
                    select(User, PasswordCredential)
                    .join(PasswordCredential, PasswordCredential.user_id == User.id)
                    .where(User.account_identifier == identifier)
                ).one_or_none()
                _detach(session)

        if found is None:
            credentials.spend_verification_time(password)
            self._log_login_failure(client)
            raise _invalid_credentials()

        user, credential = found
        if not credentials.verify_password(credential.password_hash, password) or user.status != UserStatus.ACTIVE:
            self._log_login_failure(client)
            raise _invalid_credentials()

        rehashed = (
            credentials.hash_password(password) if credentials.password_needs_rehash(credential.password_hash) else None
        )
        now = self._clock()
        with self._db.write() as session:
            # 校验发生在事务外，这期间账号可能被禁用、密码可能被改——在写锁内重新确认。
            fresh = session.execute(
                select(User, PasswordCredential)
                .join(PasswordCredential, PasswordCredential.user_id == User.id)
                .where(User.id == user.id)
            ).one_or_none()
            if (
                fresh is None
                or fresh[0].status != UserStatus.ACTIVE
                or fresh[1].password_hash != credential.password_hash
            ):
                raise _invalid_credentials()
            fresh_user, fresh_credential = fresh
            if rehashed is not None:
                fresh_credential.password_hash = rehashed
            if replaced_token:
                self._revoke_token(session, replaced_token, now)
            issued = self._open_session(session, fresh_user, client, now)

        _logger.info("登录成功", extra={"event": "auth.login_succeeded", "user_id": user.id})
        return issued

    # ------------------------------------------------------------------ 会话

    def authenticate(self, token: str | None) -> CurrentUser | None:
        """校验会话令牌。无效、过期、已撤销、账号已禁用一律返回 None。"""
        if not token or len(token) > credentials.MAX_TOKEN_LENGTH:
            return None
        digest = self._digest(token)
        now = self._clock()
        with self._db.read() as session:
            found = session.execute(
                select(LoginSession, User)
                .join(User, User.id == LoginSession.user_id)
                .where(LoginSession.token_hash == digest)
            ).one_or_none()
            _detach(session)
        if found is None:
            return None
        login_session, user = found
        if not _is_live(login_session, now) or user.status != UserStatus.ACTIVE:
            return None

        if now - login_session.last_seen_at >= LAST_SEEN_WRITE_INTERVAL:
            with self._db.write() as session:
                session.execute(
                    update(LoginSession)
                    .where(LoginSession.id == login_session.id, LoginSession.revoked_at.is_(None))
                    .values(last_seen_at=now)
                )
        return _current_user(user, login_session)

    def logout(self, token: str | None) -> None:
        """撤销当前会话。令牌无效时什么也不做：退出本身就是幂等的。"""
        if not token or len(token) > credentials.MAX_TOKEN_LENGTH:
            return
        with self._db.write() as session:
            self._revoke_token(session, token, self._clock())

    def logout_all(self, current: CurrentUser) -> None:
        """撤销该用户的全部会话，包括当前这一个（决策 C16）。"""
        with self._db.write() as session:
            self._revoke_all_sessions(session, current.user_id, self._clock())
        _logger.info("退出全部设备", extra={"event": "auth.logged_out_everywhere", "user_id": current.user_id})

    # ------------------------------------------------------------------ 密码

    def change_password(
        self,
        current: CurrentUser,
        current_password: str,
        new_password: str,
        client: ClientInfo,
    ) -> IssuedSession:
        """改密后撤销全部旧会话，并给当前设备换发一个新会话（决策 C17）。"""
        self._limiter.hit(CHANGE_PASSWORD_PER_USER, current.user_id)
        credentials.validate_new_password(new_password, current.account_identifier, field="new_password")

        with self._db.read() as session:
            old_hash = session.scalar(
                select(PasswordCredential.password_hash).where(PasswordCredential.user_id == current.user_id)
            )
        if old_hash is None or not credentials.verify_password(old_hash, current_password):
            raise _invalid_credentials("当前密码不正确")

        new_hash = credentials.hash_password(new_password)
        now = self._clock()
        with self._db.write() as session:
            # 条件更新：校验之后若密码已被别处改过，影响行数为 0。
            changed = session.execute(
                update(PasswordCredential)
                .where(PasswordCredential.user_id == current.user_id, PasswordCredential.password_hash == old_hash)
                .values(password_hash=new_hash, hash_scheme=credentials.HASH_SCHEME, changed_at=now)
            )
            if _rowcount(changed) != 1:
                raise _invalid_credentials("当前密码不正确")
            user = session.get(User, current.user_id)
            if user is None or user.status != UserStatus.ACTIVE:
                raise GoalflowError(ErrorCode.UNAUTHENTICATED, "请先登录")
            self._revoke_all_sessions(session, user.id, now)
            issued = self._open_session(session, user, client, now)

        _logger.info("修改密码", extra={"event": "auth.password_changed", "user_id": current.user_id})
        return issued

    def reset_password_with_token(self, token: str, new_password: str, client: ClientInfo) -> None:
        """凭部署者签发的一次性令牌重置密码。成功后全部会话失效，不自动登录（决策 C17）。"""
        self._limiter.hit(RESET_PER_IP_PREFIX, client.rate_limit_key)
        invalid = _invalid_credentials("重置凭证无效或已过期")
        if not token or len(token) > credentials.MAX_TOKEN_LENGTH:
            raise invalid

        digest = self._digest(token)
        now = self._clock()
        with self._db.read() as session:
            found = session.execute(
                select(PasswordResetToken, User)
                .join(User, User.id == PasswordResetToken.user_id)
                .where(PasswordResetToken.token_hash == digest)
            ).one_or_none()
            _detach(session)
        if found is None:
            raise invalid
        reset, user = found
        if reset.consumed_at is not None or now >= reset.expires_at or user.status != UserStatus.ACTIVE:
            raise invalid

        credentials.validate_new_password(new_password, user.account_identifier, field="new_password")
        new_hash = credentials.hash_password(new_password)
        now = self._clock()
        with self._db.write() as session:
            # 一次性：两个请求并发使用同一个令牌时只有一个能把它标成已用。
            consumed = session.execute(
                update(PasswordResetToken)
                .where(
                    PasswordResetToken.id == reset.id,
                    PasswordResetToken.consumed_at.is_(None),
                    PasswordResetToken.expires_at > now,
                )
                .values(consumed_at=now)
            )
            if _rowcount(consumed) != 1:
                raise invalid
            session.execute(
                update(PasswordCredential)
                .where(PasswordCredential.user_id == user.id)
                .values(password_hash=new_hash, hash_scheme=credentials.HASH_SCHEME, changed_at=now)
            )
            self._revoke_all_sessions(session, user.id, now)

        _logger.info("凭令牌重置密码", extra={"event": "auth.password_reset", "user_id": user.id})

    # ------------------------------------------------------------------ 部署者本地操作（决策 C14）

    def promote_to_admin(self, account_identifier: str) -> None:
        now = self._clock()
        with self._db.write() as session:
            user = self._require_user(session, account_identifier)
            if user.role != UserRole.ADMIN:
                user.role = UserRole.ADMIN.value
                user.revision += 1
                user.updated_at = now
        _logger.info("授予管理员", extra={"event": "auth.promoted", "user_id": user.id})

    def disable_user(self, account_identifier: str) -> None:
        now = self._clock()
        with self._db.write() as session:
            user = self._require_user(session, account_identifier)
            if user.status != UserStatus.DISABLED:
                user.status = UserStatus.DISABLED.value
                user.revision += 1
                user.updated_at = now
            self._revoke_all_sessions(session, user.id, now)
        _logger.info("禁用账号", extra={"event": "auth.disabled", "user_id": user.id})

    def issue_password_reset_token(self, account_identifier: str, *, issued_by: str) -> IssuedResetToken:
        """签发一次性重置令牌，同时作废该用户此前未用的令牌。原文只在返回值里出现一次。"""
        token = credentials.new_token()
        now = self._clock()
        expires_at = now + RESET_TOKEN_LIFETIME
        with self._db.write() as session:
            user = self._require_user(session, account_identifier)
            if user.status != UserStatus.ACTIVE:
                raise GoalflowError(ErrorCode.FORBIDDEN, "账号已禁用，重置密码也无法登录")
            session.execute(
                update(PasswordResetToken)
                .where(
                    PasswordResetToken.user_id == user.id,
                    PasswordResetToken.consumed_at.is_(None),
                    PasswordResetToken.expires_at > now,
                )
                .values(expires_at=now)
            )
            session.add(
                PasswordResetToken(
                    id=_new_id(),
                    user_id=user.id,
                    token_hash=self._digest(token),
                    issued_by=issued_by,
                    created_at=now,
                    expires_at=expires_at,
                    consumed_at=None,
                )
            )
        _logger.info("签发重置令牌", extra={"event": "auth.reset_token_issued", "user_id": user.id})
        return IssuedResetToken(token=token, expires_at=expires_at)

    # ------------------------------------------------------------------ 内部

    def _digest(self, token: str) -> str:
        return credentials.token_digest(self._config.session_secret, token)

    def _open_session(self, session: Session, user: User, client: ClientInfo, now: datetime) -> IssuedSession:
        token = credentials.new_token()
        login_session = LoginSession(
            id=_new_id(),
            user_id=user.id,
            token_hash=self._digest(token),
            created_at=now,
            last_seen_at=now,
            expires_at=now + SESSION_ABSOLUTE_LIFETIME,
            revoked_at=None,
            user_agent_summary=client.user_agent_summary,
            ip_prefix=client.ip_prefix,
        )
        session.add(login_session)
        return IssuedSession(token=token, user=_current_user(user, login_session))

    def _revoke_token(self, session: Session, token: str, now: datetime) -> None:
        if len(token) > credentials.MAX_TOKEN_LENGTH:
            return
        session.execute(
            update(LoginSession)
            .where(LoginSession.token_hash == self._digest(token), LoginSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    @staticmethod
    def _revoke_all_sessions(session: Session, user_id: str, now: datetime) -> None:
        session.execute(
            update(LoginSession)
            .where(LoginSession.user_id == user_id, LoginSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    @staticmethod
    def _require_user(session: Session, account_identifier: str) -> User:
        identifier = credentials.try_normalize_account_identifier(account_identifier)
        user = (
            session.scalar(select(User).where(User.account_identifier == identifier))
            if identifier is not None
            else None
        )
        if user is None:
            raise GoalflowError(ErrorCode.NOT_FOUND, "账号不存在")
        return user

    @staticmethod
    def _log_login_failure(client: ClientInfo) -> None:
        # 不记账号标识：失败登录里的标识可能是用户把密码输错了栏。
        _logger.info("登录失败", extra={"event": "auth.login_failed", "ip_prefix": client.ip_prefix})
