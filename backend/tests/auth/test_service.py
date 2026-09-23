"""账号 Interface 的业务规则。全部跑在迁移建出的真实库文件上，时钟可注入。"""

import statistics
import threading
import time
from datetime import timedelta

import pytest
from argon2 import PasswordHasher
from auth_support import PASSWORD, FakeClock
from sqlalchemy import func, select, text

from goalflow.auth.models import LoginSession, PasswordCredential, User
from goalflow.auth.service import (
    LAST_SEEN_WRITE_INTERVAL,
    SESSION_ABSOLUTE_LIFETIME,
    SESSION_IDLE_TIMEOUT,
    AuthService,
    ClientInfo,
)
from goalflow.contracts.enums import UserRole
from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.db.session import Database

NEW_PASSWORD = "a brand new passphrase"


def _client(n: int = 1) -> ClientInfo:
    # 每个"客户端"一个网段，避免同一个测试里的多次注册撞上按网段的限流。
    return ClientInfo(ip=f"10.0.{n}.1", user_agent="pytest")


def _register(service: AuthService, identifier: str = "alice", n: int = 1):
    return service.register(identifier, PASSWORD, None, _client(n))


def _count(database: Database, model: type) -> int:
    with database.read() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def test_concurrent_registration_of_equivalent_identifiers_creates_one_account(
    service: AuthService, database: Database
):
    variants = ["alice", "Alice", "ALICE", " alice ", "ａｌｉｃｅ", "aLiCe", "alicE", "Alice "]
    barrier = threading.Barrier(len(variants))
    outcomes: list[str] = []
    lock = threading.Lock()

    def attempt(index: int, identifier: str) -> None:
        barrier.wait()
        try:
            service.register(identifier, PASSWORD, None, _client(index))
            outcome = "created"
        except GoalflowError as error:
            outcome = error.code.value
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=attempt, args=pair) for pair in enumerate(variants)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("created") == 1, outcomes
    assert set(outcomes) == {"created", ErrorCode.ACCOUNT_IDENTIFIER_UNAVAILABLE.value}
    assert _count(database, User) == 1
    assert _count(database, LoginSession) == 1


def test_first_registered_user_is_not_admin(service: AuthService):
    issued = _register(service)

    assert issued.user.role is UserRole.USER


def test_promote_is_the_only_way_to_admin(service: AuthService):
    issued = _register(service)
    service.promote_to_admin("ALICE")

    current = service.authenticate(issued.token)
    assert current is not None
    assert current.role is UserRole.ADMIN


def test_session_expires_after_idle_timeout(service: AuthService, clock: FakeClock):
    token = _register(service).token

    clock.advance(SESSION_IDLE_TIMEOUT - timedelta(seconds=1))
    assert service.authenticate(token) is not None
    clock.advance(SESSION_IDLE_TIMEOUT)
    assert service.authenticate(token) is None


def test_session_expires_at_absolute_lifetime_despite_activity(service: AuthService, clock: FakeClock):
    token = _register(service).token

    elapsed = timedelta(0)
    while elapsed + timedelta(days=6) < SESSION_ABSOLUTE_LIFETIME:
        clock.advance(timedelta(days=6))
        elapsed += timedelta(days=6)
        assert service.authenticate(token) is not None
    clock.advance(SESSION_ABSOLUTE_LIFETIME - elapsed)
    assert service.authenticate(token) is None


def test_last_seen_is_written_at_most_once_per_interval(service: AuthService, database: Database, clock: FakeClock):
    """每个请求都写 last_seen_at 会让所有读请求变成写请求（交接卡第 9 节第 2 条）。"""
    issued = _register(service)
    created_at = issued.user.session_created_at

    def last_seen():
        with database.read() as session:
            return session.scalar(select(LoginSession.last_seen_at))

    clock.advance(LAST_SEEN_WRITE_INTERVAL - timedelta(seconds=1))
    service.authenticate(issued.token)
    assert last_seen() == created_at

    clock.advance(timedelta(seconds=1))
    service.authenticate(issued.token)
    assert last_seen() == clock.now


def test_login_failures_are_indistinguishable(service: AuthService):
    _register(service, "alice", n=1)
    _register(service, "bob", n=2)
    service.disable_user("bob")

    cases = {
        "missing": ("nobody-here", PASSWORD),
        "wrong_password": ("alice", "wrong password guess"),
        "disabled": ("bob", PASSWORD),
    }
    messages: dict[str, set[tuple[str, str]]] = {}
    durations: dict[str, list[float]] = {}
    for label, (identifier, password) in cases.items():
        for attempt in range(3):
            started = time.perf_counter()
            with pytest.raises(GoalflowError) as caught:
                service.login(identifier, password, _client(10 + attempt))
            durations.setdefault(label, []).append(time.perf_counter() - started)
            messages.setdefault(label, set()).add((caught.value.code.value, caught.value.message))

    assert all(seen == {("INVALID_CREDENTIALS", "账号或密码错误")} for seen in messages.values()), messages
    # 三条路径都做且只做一次 Argon2 校验。阈值取 3 倍，只拦"一条路径跳过了哈希"这种数量级差异。
    medians = {label: statistics.median(values) for label, values in durations.items()}
    assert max(medians.values()) < 3 * min(medians.values()), medians


def test_relogin_revokes_the_replaced_session(service: AuthService):
    first = _register(service)
    second = service.login("alice", PASSWORD, _client(), replaced_token=first.token)

    assert second.token != first.token
    assert service.authenticate(first.token) is None
    assert service.authenticate(second.token) is not None


def test_login_upgrades_a_legacy_hash(service: AuthService, database: Database):
    _register(service)
    legacy = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(PASSWORD)
    with database.write() as session:
        session.execute(text("UPDATE password_credentials SET password_hash = :h"), {"h": legacy})

    service.login("alice", PASSWORD, _client())

    with database.read() as session:
        stored = session.scalar(select(PasswordCredential.password_hash))
    assert stored is not None and "m=19456,t=2,p=1" in stored


def test_disable_revokes_sessions_and_blocks_login(service: AuthService):
    token = _register(service).token
    service.disable_user("alice")

    assert service.authenticate(token) is None
    with pytest.raises(GoalflowError) as caught:
        service.login("alice", PASSWORD, _client())
    assert caught.value.code is ErrorCode.INVALID_CREDENTIALS


def test_change_password_rotates_every_session(service: AuthService):
    laptop = _register(service)
    phone = service.login("alice", PASSWORD, _client(2))
    current = service.authenticate(laptop.token)
    assert current is not None

    rotated = service.change_password(current, PASSWORD, NEW_PASSWORD, _client())

    assert service.authenticate(laptop.token) is None
    assert service.authenticate(phone.token) is None
    assert service.authenticate(rotated.token) is not None
    service.login("alice", NEW_PASSWORD, _client(3))


def test_change_password_requires_the_current_password(service: AuthService):
    issued = _register(service)
    current = service.authenticate(issued.token)
    assert current is not None

    with pytest.raises(GoalflowError) as caught:
        service.change_password(current, "not my password", NEW_PASSWORD, _client())

    assert caught.value.code is ErrorCode.INVALID_CREDENTIALS
    assert service.authenticate(issued.token) is not None


def test_reset_token_is_single_use_and_revokes_all_sessions(service: AuthService):
    session_token = _register(service).token
    reset = service.issue_password_reset_token("alice", issued_by="test")

    service.reset_password_with_token(reset.token, NEW_PASSWORD, _client())

    assert service.authenticate(session_token) is None
    service.login("alice", NEW_PASSWORD, _client())
    with pytest.raises(GoalflowError) as caught:
        service.reset_password_with_token(reset.token, "yet another passphrase", _client())
    assert caught.value.code is ErrorCode.INVALID_CREDENTIALS


def test_reset_token_expires(service: AuthService, clock: FakeClock):
    _register(service)
    reset = service.issue_password_reset_token("alice", issued_by="test")

    clock.advance(timedelta(minutes=30))
    with pytest.raises(GoalflowError) as caught:
        service.reset_password_with_token(reset.token, NEW_PASSWORD, _client())
    assert caught.value.code is ErrorCode.INVALID_CREDENTIALS


def test_issuing_a_new_reset_token_voids_the_previous_one(service: AuthService):
    _register(service)
    first = service.issue_password_reset_token("alice", issued_by="test")
    second = service.issue_password_reset_token("alice", issued_by="test")

    with pytest.raises(GoalflowError):
        service.reset_password_with_token(first.token, NEW_PASSWORD, _client())
    service.reset_password_with_token(second.token, NEW_PASSWORD, _client())


def test_login_is_rate_limited_per_identifier(service: AuthService):
    _register(service)
    for attempt in range(10):
        with pytest.raises(GoalflowError):
            service.login("alice", "wrong password guess", _client(20 + attempt))

    # 第 11 次即使密码正确、换了网段也被拒：限流先于密码校验执行。
    with pytest.raises(GoalflowError) as caught:
        service.login("alice", PASSWORD, _client(99))
    assert caught.value.code is ErrorCode.RATE_LIMITED
