"""会话与事务边界。

数据模型第 7 节的并发协议只有一种形状：**短写事务 + 带 revision 条件的 UPDATE +
影响行数判定**。这里提供的两个上下文管理器就是那个协议的入口，业务模块不应该自己
`create_engine` 或自己拼 `BEGIN`。

读写分成两个入口不是洁癖，是性能与正确性的分界线：

- `write()` 走 `BEGIN IMMEDIATE`，事务一开始就拿到写锁，冲突变成可等待、可重试的
  `SQLITE_BUSY`，而不是不可恢复的升级失败（T01 D3）。
- `read()` 走 `BEGIN DEFERRED`。WAL 下读事务看到的是事务开始时的快照（T01 D2），
  读不阻塞写、写不阻塞读。**如果让只读事务也用 IMMEDIATE，所有读都会互相排队**，
  WAL 的全部收益就没了。
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from goalflow.core.config import get_settings
from goalflow.db.engine import BEGIN_DEFERRED, BEGIN_MODE_OPTION, create_database_engine


class Database:
    """一个库文件对应一个实例，持有连接池与两套会话工厂。

    读写两套工厂共享同一个连接池：`Engine.execution_options()` 返回的是共享池的
    派生 Engine，不会多占连接。
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        read_engine = engine.execution_options(**{BEGIN_MODE_OPTION: BEGIN_DEFERRED})
        # expire_on_commit=False：提交后不过期实例属性。开着的话，调用方在事务结束后
        # 读任何一个属性都会触发一次隐式刷新——那次刷新发生在事务之外，既多一次查询，
        # 又可能读到与刚提交结果不一致的值。
        self._write_sessions = sessionmaker(engine, expire_on_commit=False)
        self._read_sessions = sessionmaker(read_engine, expire_on_commit=False)

    @property
    def engine(self) -> Engine:
        """给 Alembic 与验证套件用。业务代码不要直接拿它执行 SQL。"""
        return self._engine

    @contextmanager
    def write(self) -> Iterator[Session]:
        """写事务：`BEGIN IMMEDIATE`，正常退出提交，异常回滚。

        事务里**不要调用模型、不要发 HTTP**。写锁是库级的，一个慢事务会把所有
        写路径堵住——模型调用一律在事务外做，事务里只重新校验版本再落库。
        """
        session = self._write_sessions()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def read(self) -> Iterator[Session]:
        """只读事务：`BEGIN DEFERRED`，退出时回滚。

        回滚而不是提交，是因为读事务没有要提交的东西；显式结束事务才能让下一次读
        拿到新快照，否则会一直停在旧快照上（T01 D2）。
        """
        session = self._read_sessions()
        try:
            yield session
        finally:
            session.rollback()
            session.close()

    def dispose(self) -> None:
        self._engine.dispose()


@lru_cache(maxsize=1)
def get_database() -> Database:
    """进程级单例。

    API、Worker、Beat 是不同进程，各自持有自己的连接池指向同一个库文件——这正是
    RFC 0003 里"必须同主机同本地文件系统"那条约束的由来。

    测试里需要指向别的库文件时直接构造 `Database(create_database_engine(url))`，
    不要改这里的缓存。
    """
    return Database(create_database_engine(get_settings().database_url))
