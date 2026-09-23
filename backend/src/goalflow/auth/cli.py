"""部署者本地命令（T03 决策 C14）。首版的管理能力只有这些，不提供 HTTP 管理接口。

    python -m goalflow.auth.cli promote <账号标识>
    python -m goalflow.auth.cli disable <账号标识>
    python -m goalflow.auth.cli issue-reset-token <账号标识>

读取与应用相同的 GOALFLOW_* 配置，直接操作同一个库文件。重置令牌原文只打印这一次，
由部署者通过可信渠道交给用户；本命令不发送任何邮件或消息。
"""

import argparse
import sys
from collections.abc import Sequence

from goalflow.auth.service import AuthConfig, AuthService
from goalflow.contracts.errors import GoalflowError
from goalflow.core.config import get_settings
from goalflow.db.session import get_database

_ISSUED_BY = "cli"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m goalflow.auth.cli", description="GoalFlow 账号运维命令")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("promote", "把账号提升为管理员"),
        ("disable", "禁用账号并撤销它的全部会话"),
        ("issue-reset-token", "签发一次性密码重置令牌（30 分钟内有效）"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("account_identifier", help="账号标识")
    return parser


def run(argv: Sequence[str], service: AuthService) -> int:
    """命令主体。与进程入口分开，测试可以直接传入指向临时库的 service。"""
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "promote":
            service.promote_to_admin(args.account_identifier)
            print("已授予管理员")
        elif args.command == "disable":
            service.disable_user(args.account_identifier)
            print("已禁用，该账号的全部会话已撤销")
        else:
            issued = service.issue_password_reset_token(args.account_identifier, issued_by=_ISSUED_BY)
            print(f"重置令牌：{issued.token}")
            print(f"有效期至：{issued.expires_at.isoformat(timespec='seconds')}（UTC）")
            print("令牌只显示这一次。此前签发的未用令牌已作废。")
    except GoalflowError as error:
        print(f"失败：{error.message}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    settings = get_settings()
    return run(sys.argv[1:], AuthService(get_database(), AuthConfig.from_settings(settings)))


if __name__ == "__main__":
    raise SystemExit(main())
