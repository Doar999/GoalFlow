"""幂等测试装置。

库文件由真实迁移建出，连接走 `create_database_engine()`——与生产同一组 pragma、WAL、真实文件。
迁移只跑一次，之后每个用例复制一份干净的库文件。
"""

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from idempotency_support import PASSWORD, PROBE_TABLE_DDL, FakeClock, migrate, sqlite_url
from sqlalchemy import text

from goalflow.auth.service import AuthConfig, AuthService, ClientInfo
from goalflow.db.engine import create_database_engine
from goalflow.db.session import Database


@pytest.fixture(scope="session")
def migrated_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("template") / "template.db"
    url = sqlite_url(path)
    migrate(url)
    engine = create_database_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text(PROBE_TABLE_DDL))
    finally:
        # 最后一条连接关闭时 WAL 检查点写回主文件，之后单拷主文件即完整。
        engine.dispose()
    return path


@pytest.fixture
def database(tmp_path: Path, migrated_template: Path) -> Iterator[Database]:
    path = tmp_path / "app.db"
    shutil.copyfile(migrated_template, path)
    db = Database(create_database_engine(sqlite_url(path)))
    try:
        yield db
    finally:
        db.dispose()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def auth_config() -> AuthConfig:
    return AuthConfig(
        session_secret=b"test-session-secret-not-for-production",
        allow_registration=True,
        public_origin="",
        secure_cookies=False,
    )


@pytest.fixture
def auth_service(database: Database, auth_config: AuthConfig, clock: FakeClock) -> AuthService:
    return AuthService(database, auth_config, clock=clock)


@pytest.fixture
def alice(auth_service: AuthService) -> str:
    """幂等记录的 owner_id 外键指向 users，所以用例需要真实注册的用户。"""
    return auth_service.register("alice", PASSWORD, None, ClientInfo(ip="192.0.2.1")).user.user_id


@pytest.fixture
def bob(auth_service: AuthService) -> str:
    return auth_service.register("bob", PASSWORD, None, ClientInfo(ip="192.0.2.2")).user.user_id
