"""`goalflow.db.session.Database` 的事务边界。

重点不是"能读能写"，而是读写两条路径**用了不同的事务开始模式**。这一条如果退化，
症状不会是报错，而是并发下吞吐塌掉或偶发不可重试的失败——两种都很难从日志看出来。
"""

import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Integer, String, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from goalflow.db.engine import create_database_engine
from goalflow.db.session import Database

_CREATE_BUDGET = """
CREATE TABLE budget (
    owner_id TEXT PRIMARY KEY,
    remaining INTEGER NOT NULL,
    revision INTEGER NOT NULL
)
"""


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    db = Database(create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'app.db'}"))
    with db.write() as session:
        session.execute(text(_CREATE_BUDGET))
        session.execute(text("INSERT INTO budget VALUES ('u1', 60, 1)"))
    try:
        yield db
    finally:
        db.dispose()


def test_write_commits_on_success(database: Database) -> None:
    with database.write() as session:
        session.execute(text("UPDATE budget SET remaining = 30, revision = 2 WHERE owner_id = 'u1'"))

    with database.read() as session:
        assert session.execute(text("SELECT remaining, revision FROM budget")).one() == (30, 2)


def test_write_rolls_back_on_exception(database: Database) -> None:
    with pytest.raises(RuntimeError, match="故意失败"), database.write() as session:
        session.execute(text("UPDATE budget SET remaining = 0 WHERE owner_id = 'u1'"))
        raise RuntimeError("故意失败")

    with database.read() as session:
        assert session.execute(text("SELECT remaining FROM budget")).scalar_one() == 60


def test_conditional_update_reports_stale_via_rowcount(database: Database) -> None:
    """并发协议的判定依据就是这个影响行数（T01 D4）。

    版本匹配返回 1，不匹配返回 0，**新值等于旧值时仍然返回 1**——所以幂等重提不会被
    误判成版本冲突。
    """
    with database.write() as session:
        fresh = session.execute(
            text("UPDATE budget SET remaining = 60, revision = 1 WHERE owner_id = 'u1' AND revision = 1")
        ).rowcount
        stale = session.execute(
            text("UPDATE budget SET remaining = 0, revision = 2 WHERE owner_id = 'u1' AND revision = 99")
        ).rowcount

    assert fresh == 1, "新值等于旧值也要计入影响行数"
    assert stale == 0, "版本不匹配返回 0，这是判定 stale 的唯一依据"


class _TestBase(DeclarativeBase):
    """测试专用，不能用 goalflow.db.base.Base——那份 metadata 会被 ORM 与迁移的一致性比对扫到。"""


class _Budget(_TestBase):
    __tablename__ = "budget"

    owner_id: Mapped[str] = mapped_column(String, primary_key=True)
    remaining: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)


def test_entities_loaded_in_read_stay_usable_after_it_ends(database: Database) -> None:
    """`read()` 退出时回滚，而回滚会让会话里的实体过期（`expire_on_commit=False` 只管提交）。

    过期实体离开会话后再读属性就是 DetachedInstanceError。所有"读事务取数 → 事务外计算 →
    写事务落库"的业务路径都依赖读出来的实体在事务结束后仍然可用。
    """
    with database.read() as session:
        budget = session.get(_Budget, "u1")

    assert budget is not None
    assert (budget.remaining, budget.revision) == (60, 1)


def test_write_does_not_see_entities_detached_by_read(database: Database) -> None:
    """读出来的实体已经脱离会话；要改它，得在写事务里重新取，不会被误当成脏数据提交。"""
    with database.read() as session:
        stale = session.get(_Budget, "u1")
    assert stale is not None
    stale.remaining = 0

    with database.write() as session:
        session.execute(text("UPDATE budget SET revision = revision + 1 WHERE owner_id = 'u1'"))

    with database.read() as session:
        assert session.execute(text("SELECT remaining, revision FROM budget")).one() == (60, 2)


def test_read_does_not_take_the_write_lock(database: Database) -> None:
    """**这条是本文件的核心。**

    只读事务进行中，另一条连接必须仍然能开写事务。如果 `read()` 退化成 IMMEDIATE，
    这里会立刻超时失败——而在生产里它的表现是所有读互相排队，不报任何错。
    """
    with database.read() as session:
        assert session.execute(text("SELECT remaining FROM budget")).scalar_one() == 60

        rival = database.engine.connect()
        try:
            rival.execute(text("UPDATE budget SET remaining = 10, revision = 2 WHERE owner_id = 'u1'"))
            rival.commit()
        finally:
            rival.close()


def test_write_lock_serialises_two_writers(database: Database) -> None:
    """两个写事务串行，靠 busy_timeout 等待而不是立刻失败。"""
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def contend(tag: str) -> None:
        barrier.wait(timeout=5)
        with database.write() as session:
            session.execute(text("UPDATE budget SET revision = revision + 1 WHERE owner_id = 'u1'"))
            time.sleep(0.05)
        outcomes.append(tag)

    threads = [threading.Thread(target=contend, args=(tag,)) for tag in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(outcomes) == ["a", "b"], "两个写事务都应当成功，冲突靠等待化解而不是失败"
    with database.read() as session:
        assert session.execute(text("SELECT revision FROM budget")).scalar_one() == 3


def test_read_sees_a_stable_snapshot(database: Database) -> None:
    """读事务看到的是事务开始时的快照；结束事务后才看到新值（T01 D2）。

    事务协议里"事务中重新检查 revision"能成立，依赖的就是这个性质。
    """
    with database.read() as session:
        assert session.execute(text("SELECT remaining FROM budget")).scalar_one() == 60

        writer = sqlite3.connect(str(database.engine.url.database), isolation_level=None, timeout=5)
        try:
            writer.execute("PRAGMA busy_timeout=5000")
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("UPDATE budget SET remaining = 5 WHERE owner_id = 'u1'")
            writer.execute("COMMIT")
        finally:
            writer.close()

        assert session.execute(text("SELECT remaining FROM budget")).scalar_one() == 60, "读事务应当停在自己的快照上"

    with database.read() as session:
        assert session.execute(text("SELECT remaining FROM budget")).scalar_one() == 5
