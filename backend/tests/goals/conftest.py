"""目标模块测试装置。

库文件由真实迁移建出、连接走 `create_database_engine()`，与生产同一组 pragma、WAL、
真实文件（AGENTS.md"测试"一节）。迁移只跑一次，之后每个用例复制一份干净的库文件。

辅助逻辑与 tests/auth/auth_support.py 同型但独立一份：测试目录没有 `__init__.py`，
跨目录 import 不可靠。
"""

import argparse
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from goalflow.api.app import create_app
from goalflow.api.dependencies import get_auth_service
from goalflow.auth.service import AuthConfig, AuthService
from goalflow.db.engine import create_database_engine

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "http://testserver"
PASSWORD = "correct horse battery"


def _sqlite_url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path.as_posix()}"


def _migrate(url: str) -> None:
    config = Config()
    config.set_main_option("script_location", (BACKEND_ROOT / "migrations").as_posix())
    # env.py 从 -x url=... 取连接串；程序化调用时它来自 cmd_opts。
    config.cmd_opts = argparse.Namespace(x=[f"url={url}"])
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
def migrated_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("goals-template") / "template.db"
    _migrate(_sqlite_url(path))
    # env.py 结束时 dispose，最后一条连接关闭会把 WAL 检查点写回主文件，单拷主文件即完整。
    return path


@pytest.fixture
def raw_engine(migrated_template: Path, tmp_path: Path) -> Iterator[Engine]:
    """绕过会话封装的裸引擎：CHECK 兜底测试直接对库说话。"""
    db_path = tmp_path / "raw.db"
    shutil.copyfile(migrated_template, db_path)
    engine = create_database_engine(_sqlite_url(db_path))
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def app(migrated_template: Path, tmp_path: Path) -> Iterator[FastAPI]:
    db_path = tmp_path / "app.db"
    shutil.copyfile(migrated_template, db_path)
    from goalflow.db.session import Database

    database = Database(create_database_engine(_sqlite_url(db_path)))
    try:
        service = AuthService(
            database,
            AuthConfig(
                session_secret=b"test-session-secret-not-for-production",
                allow_registration=True,
                public_origin=ORIGIN,
                secure_cookies=False,
            ),
        )
        application = create_app()
        application.dependency_overrides[get_auth_service] = lambda: service
        yield application
    finally:
        database.dispose()


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """未登录浏览器：每个请求带与 public_origin 相同的 Origin。"""
    return TestClient(app, headers={"Origin": ORIGIN}, raise_server_exceptions=False)


@pytest.fixture
def authed_client(client: TestClient) -> TestClient:
    """已注册并登录的浏览器；会话 Cookie 随 TestClient 保持。"""
    response = client.post(
        "/api/auth/register",
        json={"account_identifier": "goals-contract@local.dev", "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    return client
