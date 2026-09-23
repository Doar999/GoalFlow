"""账号测试的共用辅助。与 conftest 分开，是因为测试目录没有 __init__.py，
测试模块无法可靠地 import conftest（tests/db_compat 下还有另一个同名文件）。
"""

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from goalflow.api.app import create_app
from goalflow.api.dependencies import get_auth_service
from goalflow.auth.service import AuthService

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "http://testserver"
COOKIE = "goalflow_session"
PASSWORD = "correct horse battery"


def sqlite_url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path.as_posix()}"


def migrate(url: str, revision: str = "head", *, downgrade: bool = False) -> None:
    config = Config()
    config.set_main_option("script_location", (BACKEND_ROOT / "migrations").as_posix())
    # env.py 从 -x url=... 取连接串；程序化调用时它来自 cmd_opts。
    config.cmd_opts = argparse.Namespace(x=[f"url={url}"])
    if downgrade:
        command.downgrade(config, revision)
    else:
        command.upgrade(config, revision)


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 23, 8, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


def build_app(service: AuthService) -> FastAPI:
    app = create_app()
    app.dependency_overrides[get_auth_service] = lambda: service
    return app


def browser(app: FastAPI, **headers: str) -> TestClient:
    """模拟同源页面里的浏览器：每个写请求都带上与 public_origin 相同的 Origin。"""
    return TestClient(app, headers={"Origin": ORIGIN, **headers}, raise_server_exceptions=False)


def register(client: TestClient, identifier: str, password: str = PASSWORD, **extra: Any) -> Response:
    return client.post(
        "/api/auth/register",
        json={"account_identifier": identifier, "password": password, **extra},
    )


def login(client: TestClient, identifier: str, password: str = PASSWORD) -> Response:
    return client.post("/api/auth/login", json={"account_identifier": identifier, "password": password})
