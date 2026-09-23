"""F 备份与恢复。

对应 T01 交接卡第 5 节的 F1、F2。F2 是红线：单机部署失去可恢复性等于失去可部署性
（对应 PRD Q03）。

SQLite 的备份是拷一个文件，但**不能直接拷正在写的库文件**——WAL 里可能还有未合并的
内容，直接拷会拿到一个不完整的快照。`VACUUM INTO` 在线生成一致快照，这就是 F1 要固定的做法。
"""

import shutil
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, text


def _seed(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE goals (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            title TEXT NOT NULL,
            revision INTEGER NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE checkins (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            goal_id TEXT NOT NULL REFERENCES goals(id),
            local_date TEXT NOT NULL,
            actual_minutes_delta INTEGER NOT NULL
        )
    """)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO goals VALUES ('g1', 'u1', '读完统计学入门', 3)")
    conn.execute("INSERT INTO checkins VALUES ('c1', 'u1', 'g1', '2026-09-22', 45)")
    conn.execute("INSERT INTO checkins VALUES ('c2', 'u1', 'g1', '2026-09-23', 30)")
    conn.execute("COMMIT")


def test_f1_vacuum_into_produces_a_consistent_online_backup(raw: sqlite3.Connection, tmp_path: Path) -> None:
    """F1：VACUUM INTO 在库处于打开状态时生成一致快照，命令可重复执行。"""
    _seed(raw)
    backup = tmp_path / "backup.db"

    raw.execute("VACUUM INTO ?", (str(backup),))
    assert backup.is_file()

    restored = sqlite3.connect(backup)
    try:
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert restored.execute("SELECT count(*) FROM checkins").fetchone()[0] == 2
        assert restored.execute("SELECT title FROM goals WHERE id = 'g1'").fetchone()[0] == "读完统计学入门"
    finally:
        restored.close()

    # 目标文件已存在时必须报错而不是覆盖——备份脚本要据此决定文件命名
    with pytest.raises(sqlite3.OperationalError):
        raw.execute("VACUUM INTO ?", (str(backup),))


def test_f1_backup_taken_during_writes_is_not_torn(raw: sqlite3.Connection, open_second, tmp_path: Path) -> None:
    """F1：备份期间仍有写入时，快照要么含整笔事务要么不含，不会拿到半笔。"""
    _seed(raw)
    writer = open_second()
    backup = tmp_path / "during-write.db"

    writer.execute("BEGIN IMMEDIATE")
    writer.execute("INSERT INTO checkins VALUES ('c3', 'u1', 'g1', '2026-09-23', 15)")
    # 事务尚未提交时取快照
    raw.execute("VACUUM INTO ?", (str(backup),))
    writer.execute("COMMIT")

    restored = sqlite3.connect(backup)
    try:
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        # 未提交的那笔不该出现在快照里
        assert restored.execute("SELECT count(*) FROM checkins").fetchone()[0] == 2
    finally:
        restored.close()

    # 原库提交后是 3 条，证明刚才不是因为写入失败
    assert raw.execute("SELECT count(*) FROM checkins").fetchone()[0] == 3


def test_f2_full_restore_drill(raw: sqlite3.Connection, db_path: Path, tmp_path: Path) -> None:
    """F2（红线）：全量恢复演练——恢复后数据一致、应用能连上、核心闭环可用。

    演练做满三步，缺一步就不算：
      1. 取备份；
      2. **把原库连同 WAL/SHM 一起删掉**，模拟真正的丢失而不是"改了几行"；
      3. 恢复后不只查数据，还要能继续写——只读能打开不等于服务能跑。
    """
    _seed(raw)
    backup = tmp_path / "drill.db"
    raw.execute("VACUUM INTO ?", (str(backup),))

    before = raw.execute("SELECT id, owner_id, goal_id, local_date, actual_minutes_delta FROM checkins ORDER BY id")
    expected = before.fetchall()
    raw.close()

    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(db_path) + suffix)
        if candidate.exists():
            candidate.unlink()
    assert not db_path.exists()

    shutil.copyfile(backup, db_path)

    # 应用能启动：走生产那套 SQLAlchemy 装配，而不是裸 sqlite3
    engine: Engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA integrity_check")).scalar_one() == "ok"
            rows = conn.execute(
                text("SELECT id, owner_id, goal_id, local_date, actual_minutes_delta FROM checkins ORDER BY id")
            ).fetchall()
            assert rows == expected

        # 核心闭环可用：恢复后还能继续写反馈并读回来
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO checkins VALUES ('c9', 'u1', 'g1', '2026-09-24', 60)"),
            )
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM checkins")).scalar_one() == len(expected) + 1
    finally:
        engine.dispose()
