"""账号模块测试装置。

库文件一律由真实迁移建出，连接走 `create_database_engine()`——与生产同一组 pragma、WAL、
真实文件（AGENTS.md"测试"一节）。迁移只跑一次，之后每个用例复制一份干净的库文件，
既保证隔离又不必每条用例重跑 Alembic。
"""

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from auth_support import ORIGIN, FakeClock, browser, build_app, migrate, sqlite_url
from fastapi import FastAPI
from fastapi.testclient import TestClient

from goalflow.auth.service import AuthConfig, AuthService
from goalflow.db.engine import create_database_engine
from goalflow.db.session import Database


@pytest.fixture(scope="session")
def migrated_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("template") / "template.db"
    migrate(sqlite_url(path))
    # env.py 结束时 dispose，最后一条连接关闭会把 WAL 检查点写回主文件，单拷主文件即完整。
    return path


@pytest.fixture
def db_path(tmp_path: Path, migrated_template: Path) -> Path:
    path = tmp_path / "app.db"
    shutil.copyfile(migrated_template, path)
    return path


@pytest.fixture
def database(db_path: Path) -> Iterator[Database]:
    db = Database(create_database_engine(sqlite_url(db_path)))
    try:
        yield db
    finally:
        db.dispose()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def config() -> AuthConfig:
    return AuthConfig(
        session_secret=b"test-session-secret-not-for-production",
        allow_registration=True,
        public_origin=ORIGIN,
        secure_cookies=False,
    )


@pytest.fixture
def service(database: Database, config: AuthConfig, clock: FakeClock) -> AuthService:
    return AuthService(database, config, clock=clock)


@pytest.fixture
def app(service: AuthService) -> FastAPI:
    return build_app(service)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return browser(app)
