"""Alembic 脚手架是否按 RFC 0003 与迁移规程装配。

这一组测的不是某条迁移，而是**迁移的运行环境**：它有没有走生产那套连接装配、
batch 模式开没开、失败的迁移会不会留下半套表。

最后一条尤其值得测：`alembic upgrade` 会打印 "Will assume non-transactional DDL"，
那是 Alembic 对 SQLite 的默认判断。但本项目关掉了 pysqlite 的隐式事务管理，
DDL 实际上是事务性的（T01 E1）。这两句话互相矛盾，靠读日志判断不了，只能跑一遍。
"""

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_BACKEND_ROOT = Path(__file__).resolve().parents[2]

# 注意：ini 必须是纯 ASCII。Alembic 用 configparser 按平台默认编码读它，
# 中文 Windows 上不是 UTF-8，非 ASCII 会让每条 alembic 命令都 UnicodeDecodeError。
# env.py 会调 fileConfig()，所以 logging 段不能省——缺了会 KeyError: 'formatters'，
# alembic 在跑到迁移之前就挂掉，靠"非零退出"判定的测试会因此假通过。
_INI_TEMPLATE = """\
[alembic]
script_location = {script_location}
file_template = %%(rev)s_%%(slug)s
prepend_sys_path = {src}
path_separator = os

[loggers]
keys = root

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARNING
handlers = console
qualname =

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %%(levelname)-5.5s [%%(name)s] %%(message)s
"""

# 先建表、再抛异常。如果 DDL 不是事务性的，boom 表会留在库里。
_FAILING_MIGRATION = '''\
"""故意失败的迁移，用于验证回滚"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TABLE boom (id INTEGER)")
    raise RuntimeError("故意失败")


def downgrade() -> None:
    op.execute("DROP TABLE boom")
'''


def _run_alembic(ini: Path, database_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # 命令行固定，参数是本测试生成的临时路径
        [sys.executable, "-m", "alembic", "-c", str(ini), "-x", f"url={database_url}", *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _tables(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    finally:
        conn.close()


@pytest.fixture
def scratch(tmp_path: Path) -> tuple[Path, str]:
    """把仓库里的 migrations 目录复制一份，versions 清空。

    复制而不是 mock，是因为要测的正是真实的 env.py——换一份简化的就什么都没测到。
    """
    migrations = tmp_path / "migrations"
    shutil.copytree(_BACKEND_ROOT / "migrations", migrations)
    versions = migrations / "versions"
    shutil.rmtree(versions, ignore_errors=True)
    versions.mkdir()

    ini = tmp_path / "alembic.ini"
    ini.write_text(
        _INI_TEMPLATE.format(script_location=migrations.as_posix(), src=(_BACKEND_ROOT / "src").as_posix()),
        encoding="ascii",
    )
    return ini, f"sqlite+pysqlite:///{(tmp_path / 'app.db').as_posix()}"


def test_upgrade_runs_through_the_production_connection_assembly(scratch: tuple[Path, str], tmp_path: Path) -> None:
    """env.py 必须用 goalflow.db 的 engine，不能自己 engine_from_config。

    判据是库文件被切成了 WAL——只有走 `create_database_engine()` 才会设这条 pragma。
    如果哪天有人把 env.py 改回默认模板，这里会立刻红。
    """
    ini, url = scratch
    result = _run_alembic(ini, url, "upgrade", "head")

    assert result.returncode == 0, result.stderr
    conn = sqlite3.connect(tmp_path / "app.db")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()


def test_failing_migration_leaves_no_half_applied_schema(scratch: tuple[Path, str], tmp_path: Path) -> None:
    """失败的迁移必须整体回滚，不能留下已建的表。

    Alembic 对 SQLite 默认按"非事务性 DDL"处理并会如此打印，但本项目关掉了 pysqlite
    的隐式事务管理，实际行为以这条断言为准。
    """
    ini, url = scratch
    (ini.parent / "migrations" / "versions" / "0001_boom.py").write_text(_FAILING_MIGRATION, encoding="utf-8")

    result = _run_alembic(ini, url, "upgrade", "head")
    assert result.returncode != 0, "故意失败的迁移应当以非零退出"
    # 必须确认失败发生在迁移体内。少了这一条，任何早期失败（比如 ini 写错）
    # 都会让"表不存在"成立，测试就变成了自我欺骗。
    assert "故意失败" in result.stderr, f"alembic 没有跑到迁移体内：{result.stderr[-400:]}"

    leftovers = _tables(tmp_path / "app.db")
    assert "boom" not in leftovers, f"失败的迁移留下了半套表：{leftovers}"


def test_batch_mode_is_enabled(scratch: tuple[Path, str]) -> None:
    """render_as_batch 必须开着，否则改列类型或约束的迁移根本生成不出来。

    直接读 env.py 的源码断言，比起造一次 autogenerate 便宜得多，也更直白——
    这一项只有"开"和"没开"两种状态。
    """
    ini, _ = scratch
    env_source = (ini.parent / "migrations" / "env.py").read_text(encoding="utf-8")
    # 只数实参那一行，不要把模块文档里提到的那处也算进来
    kwargs = [line for line in env_source.splitlines() if line.strip() == "render_as_batch=True,"]
    assert len(kwargs) == 2, f"在线与离线两条路径都要开 batch 模式，实际 {len(kwargs)} 处"


def test_memory_database_is_rejected(scratch: tuple[Path, str]) -> None:
    """内存库让"多进程共享同一个库文件"这个前提静默失效，迁移也不例外。"""
    ini, _ = scratch
    result = _run_alembic(ini, "sqlite+pysqlite:///:memory:", "upgrade", "head")

    assert result.returncode != 0
    assert "DatabaseConfigurationError" in result.stderr
