"""排期模块测试装置。

库文件由真实迁移建出、连接走 `create_database_engine()`，与生产同一组 pragma、WAL、
真实文件（AGENTS.md"测试"一节）。迁移只跑一次，之后每个用例复制一份干净的库文件。

辅助逻辑与 tests/goals/conftest.py 同型但独立一份：测试目录没有 `__init__.py`，
跨目录 import 不可靠。
"""

import argparse
import shutil
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient

from goalflow.api.app import create_app
from goalflow.api.dependencies import get_auth_service, get_database, get_job_publisher
from goalflow.auth.service import AuthConfig, AuthService, CurrentUser
from goalflow.contracts.enums import UserRole
from goalflow.db.engine import create_database_engine
from goalflow.db.session import Database

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
    path = tmp_path_factory.mktemp("scheduling-template") / "template.db"
    _migrate(_sqlite_url(path))
    return path


@pytest.fixture
def app(migrated_template: Path, tmp_path: Path) -> Iterator[FastAPI]:
    db_path = tmp_path / "app.db"
    shutil.copyfile(migrated_template, db_path)
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
        # scheduling 路由经 DatabaseDep 拿库；不覆盖的话会指向开发库（get_database 全局单例）。
        application.dependency_overrides[get_database] = lambda: database
        # 作业投递换成 no-op 收集器：API 测试不连 Redis，测试可断言投递了哪些作业。
        published: list[str] = []
        application.dependency_overrides[get_job_publisher] = lambda: published.append
        application.state.published_job_ids = published
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
        json={"account_identifier": "scheduling-api@local.dev", "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    return client


@pytest.fixture
def today() -> str:
    """真实"今天"（UTC 日期的 ISO 形式）；服务层以 _now_utc 取同一天。"""
    return datetime.now(UTC).date().isoformat()


@pytest.fixture
def database(migrated_template: Path, tmp_path: Path) -> Iterator[Database]:
    """业务层测试用的 Database 实例：Interface 函数自己开事务（T03 决策 C3）。"""
    db_path = tmp_path / "service.db"
    shutil.copyfile(migrated_template, db_path)
    database = Database(create_database_engine(_sqlite_url(db_path)))
    try:
        yield database
    finally:
        database.dispose()


@pytest.fixture
def user(database: Database) -> CurrentUser:
    """直连业务层用的请求身份；users 行真实落库以满足外键。"""
    user_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat(timespec="microseconds")
    with database.engine.begin() as connection:
        from sqlalchemy import text

        connection.execute(
            text(
                "INSERT INTO users (id, account_identifier, role, status, timezone, revision, created_at, updated_at)"
                " VALUES (:id, 'scheduling-service@local.dev', 'user', 'active', 'UTC', 0, :now, :now)"
            ),
            {"id": user_id, "now": now},
        )
    moment = datetime.now(UTC)
    return CurrentUser(
        user_id=user_id,
        account_identifier="scheduling-service@local.dev",
        role=UserRole.USER,
        timezone="UTC",
        session_id=str(uuid.uuid4()),
        session_created_at=moment,
        session_expires_at=moment + timedelta(days=1),
    )
