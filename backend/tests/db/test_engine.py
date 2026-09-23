"""`goalflow.db.engine` 的装配是否正确。

与 `tests/db_compat/` 的分工：那边测**SQLite 本身**能不能承载协议（T01），
这边测**我们的装配**有没有把那些结论落实。同一条事实在两边各有一次断言不是重复——
db_compat 说"数据库支持这件事"，这里说"我们真的把它设上了"。
"""

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text

from goalflow.db.engine import (
    BUSY_TIMEOUT_MS,
    DatabaseConfigurationError,
    create_database_engine,
    validate_database_url,
)


def _url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path}"


def test_every_pooled_connection_gets_the_connection_level_pragmas(tmp_path: Path) -> None:
    """连接级 pragma 漏设不会报错，所以必须显式断言它设上了。

    `dispose()` 之后强制新建连接——只查第一条连接证明不了连接池后续建的连接也带上了。
    """
    engine = create_database_engine(_url(tmp_path / "app.db"))
    try:
        for _ in range(3):
            with engine.connect() as conn:
                assert conn.execute(text("PRAGMA journal_mode")).scalar_one() == "wal"
                assert conn.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
                assert conn.execute(text("PRAGMA busy_timeout")).scalar_one() == BUSY_TIMEOUT_MS
            engine.dispose()
    finally:
        engine.dispose()


def test_foreign_keys_are_actually_enforced(tmp_path: Path) -> None:
    """pragma 设上了还不够，要确认它真的在拦人。"""
    engine = create_database_engine(_url(tmp_path / "app.db"))
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE parent (id TEXT PRIMARY KEY)"))
            conn.execute(text("CREATE TABLE child (id TEXT PRIMARY KEY, parent_id TEXT REFERENCES parent(id))"))

        with pytest.raises(Exception, match="FOREIGN KEY"), engine.begin() as conn:
            conn.execute(text("INSERT INTO child VALUES ('c1', 'missing')"))
    finally:
        engine.dispose()


def test_ddl_can_be_rolled_back(tmp_path: Path) -> None:
    """事务性 DDL 可用，等价于确认 pysqlite 的隐式 COMMIT 已经被关掉。

    驱动默认会在 DDL 前偷偷提交，那样这条就会失败——所以它同时是"隐式事务管理已关闭"
    的证据。
    """
    engine = create_database_engine(_url(tmp_path / "app.db"))
    try:
        with pytest.raises(RuntimeError, match="故意失败"), engine.begin() as conn:
            conn.execute(text("CREATE TABLE half_done (x INTEGER)"))
            raise RuntimeError("故意失败")

        with engine.connect() as conn:
            leftover = conn.execute(text("SELECT count(*) FROM sqlite_master WHERE name = 'half_done'")).scalar_one()
        assert leftover == 0
    finally:
        engine.dispose()


def test_write_transactions_begin_immediate(tmp_path: Path) -> None:
    """默认事务是 IMMEDIATE：事务一开就占住写锁，别人连事务都开不起来。

    如果这里退化成 DEFERRED，冲突会推迟到写第一行时才爆发，那种失败不受 busy_timeout
    保护也无法重试（T01 D3）——而且平时看不出来，只在并发下出问题。
    """
    path = tmp_path / "app.db"
    engine = create_database_engine(_url(path))
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE probe (id INTEGER PRIMARY KEY, n INTEGER)"))

        with engine.begin():
            rival = sqlite3.connect(path, isolation_level=None, timeout=0.2)
            try:
                with pytest.raises(sqlite3.OperationalError):
                    rival.execute("BEGIN IMMEDIATE")
            finally:
                rival.close()
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "url",
    [
        "",
        "postgresql://localhost/goalflow",
        "mysql+pymysql://localhost/goalflow",
        "sqlite+pysqlite:///:memory:",
        "sqlite:///file:app?mode=memory&cache=shared",
    ],
)
def test_unsupported_urls_are_rejected(url: str) -> None:
    """连接串在启动时就要拦下。

    内存库被拒是有意的：多进程共享同一个库文件是 RFC 0003 的前提，内存库让这个前提
    静默失效，而症状会表现为"数据莫名其妙不见了"。
    """
    with pytest.raises(DatabaseConfigurationError):
        validate_database_url(url)


def test_supported_url_passes_through(tmp_path: Path) -> None:
    url = _url(tmp_path / "app.db")
    assert validate_database_url(url) == url
