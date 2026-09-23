"""T01 数据库行为验证套件的公共装配。

这里的连接参数就是 RFC 0003 规定的生产参数。套件的意义在于：**验证的是生产要用的那套
配置**，而不是 SQLAlchemy 的默认行为。真正的生产连接装配属于后续创建 `src/goalflow/db/`
的工作包，届时这里的常量要一起搬过去。

两个容易被跳过、跳过就让整套验证失去意义的点：

1. pysqlite 驱动默认会**自作主张管理事务**——它在 DML 前隐式 BEGIN、在 DDL 前隐式 COMMIT，
   结果是拿不到事务性 DDL，也没法自己控制 `BEGIN IMMEDIATE`。必须把 `isolation_level`
   设成 None 关掉它，再用 `begin` 事件自己发 BEGIN。
2. `foreign_keys` 和 `busy_timeout` 是**连接级**设置，不写进库文件。连接池每新建一条连接
   都要重设一次，漏设就等于这些保护不存在，而且不会有任何报错。
"""

import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, event

# WAL 是库级设置，写进文件后持久生效；其余三条是连接级的，每条连接都要重设。
BUSY_TIMEOUT_MS = 5000

CONNECTION_PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA foreign_keys=ON",
    f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}",
    "PRAGMA synchronous=NORMAL",
)


def apply_pragmas(conn: sqlite3.Connection) -> None:
    for pragma in CONNECTION_PRAGMAS:
        conn.execute(pragma)


def open_raw(path: Path, *, busy_timeout_ms: int = BUSY_TIMEOUT_MS) -> sqlite3.Connection:
    """按生产参数打开一条裸 sqlite3 连接。

    `isolation_level=None` 关掉 pysqlite 的隐式事务管理；`timeout` 是 Python 侧的
    busy 等待，与 PRAGMA busy_timeout 表达同一件事，两边都设避免依赖某一个默认值。

    `check_same_thread=False` 与 SQLAlchemy 的 pysqlite 方言默认行为一致：连接从池里
    被哪个线程取走就由哪个线程用，驱动不该替应用做这个判断。真正的安全边界是**一条连接
    同时只被一个使用者持有**，那由连接池而不是这个标志保证。
    """
    conn = sqlite3.connect(path, isolation_level=None, timeout=busy_timeout_ms / 1000, check_same_thread=False)
    apply_pragmas(conn)
    if busy_timeout_ms != BUSY_TIMEOUT_MS:
        conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
    return conn


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "goalflow_compat.db"


@pytest.fixture
def raw(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = open_raw(db_path)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def open_second(db_path: Path) -> Iterator[Callable[..., sqlite3.Connection]]:
    """再开一条指向同一库文件的连接，用于并发用例。测试结束统一关闭。"""
    opened: list[sqlite3.Connection] = []

    def _open(*, busy_timeout_ms: int = BUSY_TIMEOUT_MS) -> sqlite3.Connection:
        conn = open_raw(db_path, busy_timeout_ms=busy_timeout_ms)
        opened.append(conn)
        return conn

    try:
        yield _open
    finally:
        for conn in opened:
            conn.close()


@pytest.fixture
def engine(db_path: Path) -> Iterator[Engine]:
    """按生产参数装配的 SQLAlchemy Engine。"""
    eng = create_engine(f"sqlite+pysqlite:///{db_path}")

    @event.listens_for(eng, "connect")
    def _on_connect(dbapi_conn: sqlite3.Connection, _record: object) -> None:
        dbapi_conn.isolation_level = None
        apply_pragmas(dbapi_conn)

    @event.listens_for(eng, "begin")
    def _on_begin(conn: object) -> None:
        # 写事务一律 IMMEDIATE：DEFERRED 事务在写第一行时才升级为写锁，两个事务
        # 同时升级会拿到不受 busy_timeout 保护、也无法安全重试的 SQLITE_BUSY。
        conn.exec_driver_sql("BEGIN IMMEDIATE")  # type: ignore[attr-defined]

    try:
        yield eng
    finally:
        eng.dispose()
