"""SQLite 连接装配。

这里的每一条参数都是 RFC 0003 与 T01 结论的直接落地，全部经 `backend/tests/db_compat/`
实测。改任何一条之前先读 `docs/worklog/T01-sqlite-verification.md` 的结论表——这些参数里
有三条属于"漏设不会报错、保护却已经消失"的那一类。

三件必须做对的事：

1. **连接级 pragma 每条连接都要重设。** `foreign_keys` 默认关闭、`busy_timeout`
   默认为 0，且都不写进库文件。连接池每建一条新连接就要重设一次，漏设的后果是
   外键变成一句注释、锁等待变成立即失败，而且没有任何异常提示（T01 A2、C5）。
2. **关掉 pysqlite 的隐式事务管理。** 驱动默认在 DML 前隐式 BEGIN、在 DDL 前隐式
   COMMIT，结果是拿不到事务性 DDL，也没法自己决定事务以哪种模式开始。
3. **区分读写事务。** 写事务必须 `BEGIN IMMEDIATE`，否则 `BEGIN DEFERRED` 在写第一行
   时才升级写锁，升级冲突不受 `busy_timeout` 保护、也无法靠等待解决（T01 D3）。
   但**只读事务必须留在 DEFERRED**——让只读事务也去抢写锁，会让读与读、读与写全部
   串行，WAL "读不阻塞写"的收益直接归零。
"""

import sqlite3
from typing import Final

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import Connection

# T01 D6 实测：配置 400ms 时实际等待 ≥0.35s 后放弃。5s 是留给"另一个进程正在提交"的
# 窗口，不是留给长事务的——长事务本身就违反"短写事务"协议。
BUSY_TIMEOUT_MS: Final = 5000

# 按用到的特性倒推：部分唯一索引 3.8.0、VACUUM INTO 3.27.0、DROP COLUMN 3.35.0。
# 标准库 sqlite3 用的是 Python 发行版自带的 SQLite，同一份 uv.lock 在不同机器上可能
# 对应不同版本，所以这里必须在启动时断言，而不是等第一条 SQL 失败。
MINIMUM_SQLITE_VERSION: Final = (3, 35, 0)

# journal_mode 是库级的，写进文件后持久生效；其余三条是连接级的。
# 四条都在 connect 事件里执行，因为重设一条库级 pragma 的代价可以忽略，
# 而"哪些是连接级"这件事一旦记错就会漏设。
CONNECTION_PRAGMAS: Final = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA foreign_keys=ON",
    f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}",
    "PRAGMA synchronous=NORMAL",
)

# 事务开始模式通过 execution_option 传递，默认 IMMEDIATE。
# 只读路径显式传 DEFERRED，见 session.py。
BEGIN_MODE_OPTION: Final = "goalflow_begin_mode"
BEGIN_IMMEDIATE: Final = "IMMEDIATE"
BEGIN_DEFERRED: Final = "DEFERRED"

_SUPPORTED_URL_PREFIXES: Final = ("sqlite+pysqlite:///", "sqlite:///")


class DatabaseConfigurationError(RuntimeError):
    """连接串或运行时环境不满足 RFC 0003 的前提。"""


def check_runtime_sqlite_version() -> None:
    """启动时断言运行时 SQLite 版本，不满足就拒绝启动。"""
    actual = tuple(int(part) for part in sqlite3.sqlite_version.split("."))
    if actual < MINIMUM_SQLITE_VERSION:
        required = ".".join(str(n) for n in MINIMUM_SQLITE_VERSION)
        raise DatabaseConfigurationError(
            f"运行时 SQLite {sqlite3.sqlite_version} 低于最低要求 {required}。"
            "部分唯一索引、VACUUM INTO、DROP COLUMN 依赖它，见 RFC 0003。"
        )


def validate_database_url(database_url: str) -> str:
    """检查连接串形如 sqlite+pysqlite:///<路径>，并拒绝内存库。

    内存库在这里被拒是有意的：AGENTS.md 规定数据库验收必须用真实库文件，
    而内存库每条连接各自独立，"多进程共享同一个库文件"这个前提直接不成立——
    拿它跑出来的"通过"什么都不说明。测试要用内存库请直接构造 Engine，不走这里。
    """
    if not database_url:
        raise DatabaseConfigurationError("GOALFLOW_DATABASE_URL 为空。格式：sqlite+pysqlite:///<绝对或相对路径>")
    if not database_url.startswith(_SUPPORTED_URL_PREFIXES):
        raise DatabaseConfigurationError(f"不支持的连接串 {database_url!r}。首版数据库为 SQLite，见 RFC 0003")
    if ":memory:" in database_url or "mode=memory" in database_url:
        raise DatabaseConfigurationError("不接受内存库：多进程必须共享同一个库文件，见 RFC 0003")
    return database_url


def install_connection_hooks(engine: Engine) -> None:
    """把 pragma 与事务开始模式挂到 Engine 上。

    单独暴露是为了让测试能把同一套钩子装到自己构造的 Engine 上——验收环境与生产环境的
    差异只允许出现在库文件路径上，不允许出现在连接参数上。
    """

    @event.listens_for(engine, "connect")
    def _apply_pragmas(dbapi_connection: sqlite3.Connection, _record: object) -> None:
        # 必须先关掉驱动的隐式事务管理，否则下面的 PRAGMA 会被包进一个隐式事务，
        # 而 journal_mode=WAL 在事务里是改不动的。
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        try:
            for pragma in CONNECTION_PRAGMAS:
                cursor.execute(pragma)
        finally:
            cursor.close()

    @event.listens_for(engine, "begin")
    def _begin_with_mode(connection: Connection) -> None:
        mode = connection.get_execution_options().get(BEGIN_MODE_OPTION, BEGIN_IMMEDIATE)
        connection.exec_driver_sql(f"BEGIN {mode}")


def create_database_engine(database_url: str) -> Engine:
    """按生产参数装配 Engine。

    返回的 Engine 默认以 `BEGIN IMMEDIATE` 开启事务。只读路径应当使用
    `Database.read()`，它会把开始模式换成 DEFERRED。
    """
    check_runtime_sqlite_version()
    engine = create_engine(validate_database_url(database_url))
    install_connection_hooks(engine)
    return engine
