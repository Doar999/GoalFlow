"""脱敏验收：库文件、日志、响应体里不出现密码、会话令牌或重置令牌原文（08 验收场景）。

这条靠扫描真实产物，不靠"响应模型里没有这个字段"推断——后者拦不住日志 extra、
异常消息、或者某天有人把令牌写进 details。
"""

import logging
from pathlib import Path

import pytest
from auth_support import COOKIE, PASSWORD, browser, login, register
from fastapi import FastAPI

from goalflow.auth.service import AuthService
from goalflow.core.logging import JsonFormatter
from goalflow.db.session import Database

CHANGED = "second passphrase value"
RESET = "third passphrase value!"


def test_no_secret_is_persisted_logged_or_echoed(
    app: FastAPI,
    service: AuthService,
    database: Database,
    db_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.DEBUG)
    client = browser(app)
    bodies: list[str] = []
    tokens: list[str] = []

    def record(response) -> None:
        bodies.append(response.text)
        bodies.extend(value for name, value in response.headers.items() if name.lower() != "set-cookie")
        if client.cookies.get(COOKIE):
            tokens.append(client.cookies[COOKIE])

    record(register(client, "alice"))
    record(login(client, "alice", "wrong password guess"))
    record(login(client, "alice"))
    record(client.get("/api/auth/session"))
    record(client.post("/api/auth/password/change", json={"current_password": PASSWORD, "new_password": CHANGED}))
    reset = service.issue_password_reset_token("alice", issued_by="test")
    tokens.append(reset.token)
    record(client.post("/api/auth/password/reset-with-token", json={"token": reset.token, "new_password": RESET}))
    record(login(client, "alice", RESET))
    record(client.post("/api/auth/logout"))

    database.dispose()  # 让 WAL 检查点写回主文件，下面读到的是完整内容
    stored = b"".join(path.read_bytes() for path in db_path.parent.glob(f"{db_path.name}*"))
    logs = "\n".join(JsonFormatter().format(record) for record in caplog.records)

    secrets = {"password": [PASSWORD, CHANGED, RESET], "token": tokens}
    assert len(set(tokens)) >= 4, "流程里至少应签发过 4 个不同的令牌，否则本测试没测到东西"
    for kind, values in secrets.items():
        for value in values:
            assert value.encode("utf-8") not in stored, f"库文件里出现了 {kind} 原文"
            assert value not in logs, f"日志里出现了 {kind} 原文"
            assert all(value not in body for body in bodies), f"响应里出现了 {kind} 原文"
