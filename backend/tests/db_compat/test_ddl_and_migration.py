"""E DDL 与迁移。

对应 T01 交接卡第 5 节的 E1—E4。

SQLite 的 ALTER TABLE 只支持有限操作，改列类型、加约束都要**重建表**。Alembic 的 batch
模式负责生成"建新表 → 拷数据 → 删旧表 → 改名"这串操作。重建路径最危险的失败模式是
**漏列静默丢数据**，所以 E2、E3 专门盯着"重建之后数据还在不在、列还全不全"。
"""

import sqlite3
import time

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Engine, inspect, text


def _seed_tasks(conn: sa.Connection) -> None:
    conn.execute(
        text("""
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            title TEXT NOT NULL,
            expected_minutes INTEGER NOT NULL
        )
        """)
    )
    conn.execute(text("CREATE UNIQUE INDEX ux_tasks_owner_title ON tasks(owner_id, title)"))
    conn.execute(
        text("INSERT INTO tasks VALUES (:id, :owner, :title, :minutes)"),
        [
            {"id": "t1", "owner": "u1", "title": "读一章", "minutes": 30},
            {"id": "t2", "owner": "u1", "title": "跑步", "minutes": 45},
            {"id": "t3", "owner": "u2", "title": "读一章", "minutes": 20},
        ],
    )


def test_e1_ddl_is_transactional(raw: sqlite3.Connection) -> None:
    """E1：DDL 在事务里，回滚后不留残表。

    DDL 可回滚，但**"一个迁移只做一件 DDL、每步可重入"这条规矩仍然保留**——batch
    模式的表重建步骤多，逐步可重入在排查失败迁移时依然值钱。
    """
    raw.execute("BEGIN")
    raw.execute("CREATE TABLE half_done (x INTEGER)")
    raw.execute("ROLLBACK")

    remaining = raw.execute("SELECT count(*) FROM sqlite_master WHERE name = 'half_done'").fetchone()[0]
    assert remaining == 0


def test_e2_batch_add_column_preserves_every_row_and_column(engine: Engine) -> None:
    """E2：batch 模式加列，重建后数据与列都不丢。

    `recreate="always"` 强制走表重建路径——那正是生产迁移里改约束、改类型时会走的路径，
    也是漏列丢数据的地方。用它来测，比测"加列"这个最简单的情形有意义。
    """
    with engine.begin() as conn:
        _seed_tasks(conn)

    with engine.begin() as conn:
        op = Operations(MigrationContext.configure(conn))
        with op.batch_alter_table("tasks", recreate="always") as batch:
            batch.add_column(sa.Column("minimum_minutes", sa.Integer(), nullable=True))

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, owner_id, title, expected_minutes, minimum_minutes FROM tasks ORDER BY id")
        )
        assert rows.fetchall() == [
            ("t1", "u1", "读一章", 30, None),
            ("t2", "u1", "跑步", 45, None),
            ("t3", "u2", "读一章", 20, None),
        ]

    # 唯一索引必须跟着重建活下来，否则去重约束会在一次迁移后静默消失
    index_names = [ix["name"] for ix in inspect(engine).get_indexes("tasks")]
    assert "ux_tasks_owner_title" in index_names


def test_e3_batch_alter_column_tightens_constraint_without_losing_data(engine: Engine) -> None:
    """E3：batch 模式改列约束（可空 → 非空）后数据完整。"""
    with engine.begin() as conn:
        _seed_tasks(conn)
        conn.execute(text("ALTER TABLE tasks ADD COLUMN latest_date TEXT"))
        conn.execute(text("UPDATE tasks SET latest_date = '2026-10-31'"))

    with engine.begin() as conn:
        op = Operations(MigrationContext.configure(conn))
        with op.batch_alter_table("tasks", recreate="always") as batch:
            batch.alter_column("latest_date", existing_type=sa.Text(), nullable=False)

    columns = {col["name"]: col for col in inspect(engine).get_columns("tasks")}
    assert columns["latest_date"]["nullable"] is False

    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM tasks WHERE latest_date = '2026-10-31'")).scalar_one() == 3


def test_e3_batch_drop_column_is_reversible_within_one_migration(engine: Engine) -> None:
    """E3：加列能再删回去——迁移的 downgrade 路径可用。"""
    with engine.begin() as conn:
        _seed_tasks(conn)

    with engine.begin() as conn:
        op = Operations(MigrationContext.configure(conn))
        with op.batch_alter_table("tasks", recreate="always") as batch:
            batch.add_column(sa.Column("scratch", sa.Text(), nullable=True))

    assert "scratch" in {col["name"] for col in inspect(engine).get_columns("tasks")}

    with engine.begin() as conn:
        op = Operations(MigrationContext.configure(conn))
        with op.batch_alter_table("tasks", recreate="always") as batch:
            batch.drop_column("scratch")

    assert "scratch" not in {col["name"] for col in inspect(engine).get_columns("tasks")}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM tasks")).scalar_one() == 3


def test_e4_ddl_timing_baseline(raw: sqlite3.Connection, record_property) -> None:
    """E4：记录加列与加索引在有数据的表上的耗时。**只记录，不设阈值。**"""
    raw.execute("CREATE TABLE big (id INTEGER PRIMARY KEY, owner_id TEXT NOT NULL, payload TEXT)")
    raw.execute("BEGIN IMMEDIATE")
    raw.executemany("INSERT INTO big VALUES (?, ?, ?)", [(i, f"u{i % 20}", "x" * 100) for i in range(5000)])
    raw.execute("COMMIT")

    started = time.perf_counter()
    raw.execute("ALTER TABLE big ADD COLUMN extra TEXT")
    add_column_ms = (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    raw.execute("CREATE INDEX ix_big_owner ON big(owner_id)")
    create_index_ms = (time.perf_counter() - started) * 1000

    assert raw.execute("SELECT count(*) FROM big").fetchone()[0] == 5000
    record_property("add_column_on_5000_rows_ms", round(add_column_ms, 2))
    record_property("create_index_on_5000_rows_ms", round(create_index_ms, 2))
