"""幂等测试的共用辅助。与 conftest 分开的理由同 tests/auth/auth_support.py：测试目录没有
__init__.py，测试模块无法可靠地 import conftest。
"""

import argparse
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, table, text
from sqlalchemy.orm import Session

from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.db.session import Database
from goalflow.idempotency import IdempotentOutcome, IdempotentRequest, ResultRef, run_idempotent

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "correct horse battery"

# 只存在于测试库里的"业务表"：用它的行数判断业务写入实际执行了几次。
PROBE_TABLE_DDL = "CREATE TABLE probe_effects (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, note TEXT NOT NULL)"


def sqlite_url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path.as_posix()}"


def migrate(url: str) -> None:
    config = Config()
    config.set_main_option("script_location", (BACKEND_ROOT / "migrations").as_posix())
    config.cmd_opts = argparse.Namespace(x=[f"url={url}"])
    command.upgrade(config, "head")


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 23, 8, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


def insert_probe_effect(session: Session, owner_id: str, note: str) -> ResultRef:
    effect_id = str(uuid.uuid4())
    session.execute(
        text("INSERT INTO probe_effects (id, owner_id, note) VALUES (:id, :owner_id, :note)"),
        {"id": effect_id, "owner_id": owner_id, "note": note},
    )
    return ResultRef("probe", effect_id)


def submit(
    database: Database,
    request: IdempotentRequest,
    *,
    now: datetime,
    note: str = "effect",
    status_code: int = 201,
    fail: bool = False,
) -> IdempotentOutcome:
    """模拟一个业务模块的写 Interface：在一个写事务里执行幂等包装的业务写入。"""
    with database.write() as session:

        def execute() -> tuple[ResultRef, int]:
            result = insert_probe_effect(session, request.owner_id, note)
            if fail:
                raise GoalflowError(ErrorCode.BUDGET_CONFLICT, "业务规则拒绝")
            return result, status_code

        return run_idempotent(session, request, execute, now=now)


def probe_effect_count(database: Database) -> int:
    with database.read() as session:
        return session.scalar(select(func.count()).select_from(table("probe_effects"))) or 0


def record_count(database: Database) -> int:
    with database.read() as session:
        return session.scalar(select(func.count()).select_from(table("idempotency_requests"))) or 0
