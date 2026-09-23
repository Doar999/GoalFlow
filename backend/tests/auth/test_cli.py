"""部署者本地命令（决策 C14）。"""

import pytest
from auth_support import PASSWORD

from goalflow.auth.cli import run
from goalflow.auth.service import AuthService, ClientInfo
from goalflow.contracts.enums import UserRole


@pytest.fixture
def alice_token(service: AuthService) -> str:
    return service.register("alice", PASSWORD, None, ClientInfo(ip="10.0.0.1")).token


def test_promote(service: AuthService, alice_token: str, capsys: pytest.CaptureFixture[str]):
    assert run(["promote", "Alice"], service) == 0

    current = service.authenticate(alice_token)
    assert current is not None and current.role is UserRole.ADMIN
    assert "已授予管理员" in capsys.readouterr().out


def test_disable(service: AuthService, alice_token: str):
    assert run(["disable", "alice"], service) == 0

    assert service.authenticate(alice_token) is None


def test_issue_reset_token_prints_a_usable_token_once(service: AuthService, alice_token: str, capsys):
    assert run(["issue-reset-token", "alice"], service) == 0

    output = capsys.readouterr().out
    token = next(line.split("：", 1)[1] for line in output.splitlines() if line.startswith("重置令牌："))
    service.reset_password_with_token(token, "a brand new passphrase", ClientInfo(ip="10.0.0.1"))
    assert service.authenticate(alice_token) is None


def test_unknown_account_fails_with_nonzero_exit(service: AuthService, capsys):
    assert run(["promote", "nobody"], service) == 1
    assert "账号不存在" in capsys.readouterr().err
