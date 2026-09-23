"""A 连接与工具链、G 环境与基线。

对应 T01 交接卡第 5 节的 A1—A4 与 G1、G2。
"""

import platform
import sqlite3
import time

from sqlalchemy import Engine, inspect, text

# 依赖的最低版本，按用到的特性倒推：
#   3.8.0  部分唯一索引（C4 用它表达"一个目标最多一个当前执行版本"）
#   3.27.0 VACUUM INTO（F1 的在线备份方式）
#   3.35.0 ALTER TABLE DROP COLUMN（迁移回滚需要）
MINIMUM_SQLITE_VERSION = (3, 35, 0)


def test_a1_sqlalchemy_connect_query_close(engine: Engine) -> None:
    """A1：sqlite+pysqlite 方言建连、查询、关闭正常。"""
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar_one() == 1
    assert engine.dialect.name == "sqlite"


def test_a1_runtime_sqlite_version_meets_minimum() -> None:
    """A1：运行时 SQLite 版本满足用到的特性门槛。

    标准库 sqlite3 用的是 Python 发行版自带的 SQLite，同一份 uv.lock 在不同机器上
    可能对应不同版本，所以这条必须是断言而不是记录。
    """
    actual = tuple(int(part) for part in sqlite3.sqlite_version.split("."))
    assert actual >= MINIMUM_SQLITE_VERSION, (
        f"运行时 SQLite {sqlite3.sqlite_version} 低于最低要求 {'.'.join(str(n) for n in MINIMUM_SQLITE_VERSION)}"
    )


def test_a2_wal_persists_in_file_but_connection_pragmas_do_not(db_path, raw, open_second) -> None:
    """A2：区分库级与连接级 pragma。

    WAL 写进库文件，换连接仍然是 WAL；foreign_keys 和 busy_timeout 不写进文件，
    新连接不设就是默认值。**这正是"漏设 pragma 等于保护不存在"的来源**，所以要把它
    固定成已知事实，而不是留给后来的人踩。
    """
    raw.execute("CREATE TABLE probe (x INTEGER)")

    # 不走 open_raw、完全不设 pragma 的裸连接
    bare = sqlite3.connect(db_path)
    try:
        assert bare.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert bare.execute("PRAGMA foreign_keys").fetchone()[0] == 0
    finally:
        bare.close()

    configured = open_second()
    assert configured.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert configured.execute("PRAGMA busy_timeout").fetchone()[0] > 0


def test_a3_engine_applies_pragmas_to_every_pooled_connection(engine: Engine) -> None:
    """A3：连接池每新建一条连接都重设连接级 pragma。"""
    for _ in range(3):
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
            assert conn.execute(text("PRAGMA busy_timeout")).scalar_one() > 0
        engine.dispose()  # 强制下次拿到全新连接而不是池里的旧连接


def test_a4_reflection_sees_columns_indexes_unique_and_foreign_keys(engine: Engine) -> None:
    """A4：inspect() 反射结果与建表语句一致。

    反射不全会直接决定迁移能不能用 Alembic autogenerate，所以逐类检查而不是只看表名。
    """
    with engine.begin() as conn:
        conn.execute(
            text("""
            CREATE TABLE goals (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                title TEXT NOT NULL
            )
            """)
        )
        conn.execute(
            text("""
            CREATE TABLE goal_profiles (
                id TEXT PRIMARY KEY,
                goal_id TEXT NOT NULL REFERENCES goals(id),
                version_no INTEGER NOT NULL,
                status TEXT NOT NULL,
                UNIQUE (goal_id, version_no)
            )
            """)
        )
        conn.execute(text("CREATE INDEX ix_goal_profiles_status ON goal_profiles(status)"))

    insp = inspect(engine)
    assert set(insp.get_table_names()) == {"goals", "goal_profiles"}

    columns = {col["name"]: col for col in insp.get_columns("goal_profiles")}
    assert set(columns) == {"id", "goal_id", "version_no", "status"}
    assert columns["version_no"]["nullable"] is False

    unique = insp.get_unique_constraints("goal_profiles")
    assert [tuple(uc["column_names"]) for uc in unique] == [("goal_id", "version_no")]

    assert [ix["name"] for ix in insp.get_indexes("goal_profiles")] == ["ix_goal_profiles_status"]

    fks = insp.get_foreign_keys("goal_profiles")
    assert len(fks) == 1
    assert fks[0]["referred_table"] == "goals"
    assert fks[0]["constrained_columns"] == ["goal_id"]

    assert insp.get_pk_constraint("goal_profiles")["constrained_columns"] == ["id"]


def test_g1_runs_against_a_plain_file_on_this_platform(engine: Engine, db_path, record_property) -> None:
    """G1：开发机直接跑，不需要数据库服务进程或容器。

    这条不是形式主义："开发机上跑数据库测试要不要先起容器"直接决定了这套用例会不会
    被日常跳过。断言它，就是把"不需要外部依赖"固定成事实而不是印象。
    """
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (x INTEGER)"))
        conn.execute(text("INSERT INTO t VALUES (1)"))

    assert db_path.is_file()
    record_property("platform", platform.platform())
    record_property("python", platform.python_version())
    record_property("sqlite", sqlite3.sqlite_version)


def test_g2_performance_baseline(raw, record_property) -> None:
    """G2：记录性能基线。**只记录，不设阈值**（交接卡决策 A4）。

    断言部分只确认操作真的完成了——数值本身拿去填交接卡的结论表，不作为通过条件。
    """
    raw.execute("CREATE TABLE bench (id INTEGER PRIMARY KEY, owner_id TEXT NOT NULL, payload TEXT)")
    raw.execute("CREATE INDEX ix_bench_owner ON bench(owner_id)")

    rows = [(i, f"u{i % 10}", "x" * 200) for i in range(1000)]

    started = time.perf_counter()
    raw.execute("BEGIN IMMEDIATE")
    raw.executemany("INSERT INTO bench VALUES (?, ?, ?)", rows)
    raw.execute("COMMIT")
    insert_ms = (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    found = raw.execute("SELECT count(*) FROM bench WHERE owner_id = 'u3'").fetchone()[0]
    query_ms = (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    raw.execute("BEGIN IMMEDIATE")
    updated = raw.execute("UPDATE bench SET payload = 'y' WHERE id = 500").rowcount
    raw.execute("COMMIT")
    update_ms = (time.perf_counter() - started) * 1000

    assert found == 100
    assert updated == 1

    record_property("insert_1000_rows_ms", round(insert_ms, 2))
    record_property("indexed_count_query_ms", round(query_ms, 3))
    record_property("single_conditional_update_ms", round(update_ms, 3))
