"""个人模型配置模块测试装置。

库文件由真实迁移建出、连接走 `create_database_engine()`，与生产同一组 pragma、WAL、
真实文件（AGENTS.md"测试"一节）。结构与 tests/links/conftest.py 同型但独立一份：
测试目录没有 `__init__.py`，跨目录 import 不可靠。

service 的加密与出站策略取自部署配置；测试用 autouse 夹具替换为确定性实现
（固定测试主密钥 + 公网解析器），出站自身的规则测试在 test_model_config_outbound.py。
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
from sqlalchemy import text

from goalflow.api.app import create_app
from goalflow.api.dependencies import get_auth_service
from goalflow.auth.service import AuthConfig, AuthService, CurrentUser
from goalflow.contracts.enums import UserRole
from goalflow.db.engine import create_database_engine
from goalflow.db.session import Database, get_database
from goalflow.model_configs import service
from goalflow.model_configs.crypto import CredentialCrypto
from goalflow.model_configs.outbound import OutboundPolicy

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "http://testserver"
PASSWORD = "correct horse battery"
TEST_MASTER_KEY = "t14-test-master-key-not-for-production-0123456789"

# 公网解析桩：所有主机名都解析到公网地址，且不可达（真实调用已被 monkeypatch 拦截）。


def _public_resolver(hostname: str) -> list[str]:
    return ["93.184.216.34"]


def _private_resolver(hostname: str) -> list[str]:
    return ["10.1.2.3"]


@pytest.fixture(scope="session")
def migrated_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("model-configs-template") / "template.db"
    _migrate(_sqlite_url(path))
    return path


def _migrate(url: str) -> None:
    config = Config()
    config.set_main_option("script_location", (BACKEND_ROOT / "migrations").as_posix())
    config.cmd_opts = argparse.Namespace(x=[f"url={url}"])
    command.upgrade(config, "head")


def _sqlite_url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path.as_posix()}"


@pytest.fixture
def app(migrated_template: Path, tmp_path: Path) -> Iterator[FastAPI]:
    db_path = tmp_path / "app.db"
    shutil.copyfile(migrated_template, db_path)
    database = Database(create_database_engine(_sqlite_url(db_path)))
    try:
        service_ = AuthService(
            database,
            AuthConfig(
                session_secret=b"test-session-secret-not-for-production",
                allow_registration=True,
                public_origin=ORIGIN,
                secure_cookies=False,
            ),
        )
        application = create_app()
        application.dependency_overrides[get_auth_service] = lambda: service_
        application.dependency_overrides[get_database] = lambda: database
        yield application
    finally:
        database.dispose()


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, headers={"Origin": ORIGIN}, raise_server_exceptions=False)


@pytest.fixture
def authed_client(client: TestClient) -> TestClient:
    response = client.post(
        "/api/auth/register",
        json={"account_identifier": "model-configs@local.dev", "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    return client


@pytest.fixture
def second_client(app: FastAPI, authed_client: TestClient) -> TestClient:
    """第二个独立用户，用于隔离验收（Q01）。"""
    other = TestClient(app, headers={"Origin": ORIGIN}, raise_server_exceptions=False)
    response = other.post(
        "/api/auth/register",
        json={"account_identifier": "other-user@local.dev", "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    return other


@pytest.fixture
def database(migrated_template: Path, tmp_path: Path) -> Iterator[Database]:
    db_path = tmp_path / "service.db"
    shutil.copyfile(migrated_template, db_path)
    database = Database(create_database_engine(_sqlite_url(db_path)))
    try:
        yield database
    finally:
        database.dispose()


@pytest.fixture
def user(database: Database) -> CurrentUser:
    user_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat(timespec="microseconds")
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, account_identifier, role, status, timezone, revision, created_at, updated_at)"
                " VALUES (:id, 'model-configs-service@local.dev', 'user', 'active', 'UTC', 0, :now, :now)"
            ),
            {"id": user_id, "now": now},
        )
    moment = datetime.now(UTC)
    return CurrentUser(
        user_id=user_id,
        account_identifier="model-configs-service@local.dev",
        role=UserRole.USER,
        timezone="UTC",
        session_id=str(uuid.uuid4()),
        session_created_at=moment,
        session_expires_at=moment + timedelta(days=1),
    )


@pytest.fixture(autouse=True)
def deterministic_service_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    """service 的加密与出站策略改为确定性实现：固定测试主密钥 + 公网解析桩。"""
    monkeypatch.setattr(service, "_crypto", lambda: CredentialCrypto(TEST_MASTER_KEY))
    monkeypatch.setattr(service, "_outbound_policy", lambda: OutboundPolicy("", resolver=_public_resolver))
