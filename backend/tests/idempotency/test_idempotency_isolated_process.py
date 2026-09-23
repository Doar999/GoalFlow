"""只 import 幂等模块的进程也能写入（T07 决策 E28）。

必须在子进程里测：本测试进程里别的装置早就 import 过账号模块，users 表已经登记进
MetaData，缺登记的 bug 在这里永远看不见。Celery Worker 这类进程不会 import 账号模块。
"""

import shutil
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

from idempotency_support import sqlite_url

_SCRIPT = textwrap.dedent(
    """
    import sys
    from datetime import UTC, datetime

    from goalflow.db.engine import create_database_engine
    from goalflow.db.session import Database
    from goalflow.idempotency import IdempotentRequest, ResultRef, run_idempotent

    database = Database(create_database_engine(sys.argv[1]))
    request = IdempotentRequest(owner_id="u1", operation="create_probe", key="k1", request_hash="0" * 64)
    with database.write() as session:
        run_idempotent(session, request, lambda: (ResultRef("probe", "r1"), 201), now=datetime.now(UTC))
    """
)


def test_a_process_that_never_imports_the_account_module_can_write(migrated_template: Path, tmp_path: Path):
    path = tmp_path / "app.db"
    shutil.copyfile(migrated_template, path)
    connection = sqlite3.connect(path)
    with connection:
        connection.execute(
            "INSERT INTO users (id, account_identifier, role, status, timezone, revision, created_at, updated_at) "
            "VALUES ('u1', 'alice', 'user', 'active', 'UTC', 1, "
            "'2026-09-23T08:00:00.000000+00:00', '2026-09-23T08:00:00.000000+00:00')"
        )
    connection.close()

    result = subprocess.run(  # 命令固定，参数是本测试生成的临时库路径
        [sys.executable, "-c", _SCRIPT, sqlite_url(path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
