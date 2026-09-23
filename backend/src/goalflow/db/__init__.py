"""数据库连接与事务边界。

业务模块只应该用到 `Database` 与 `get_database`；`engine` 模块里的常量是给验证套件和
Alembic 用的，业务代码引用它们通常意味着绕开了事务边界。
"""

from goalflow.db.engine import (
    BUSY_TIMEOUT_MS,
    CONNECTION_PRAGMAS,
    MINIMUM_SQLITE_VERSION,
    DatabaseConfigurationError,
    create_database_engine,
    install_connection_hooks,
)
from goalflow.db.session import Database, get_database

__all__ = [
    "BUSY_TIMEOUT_MS",
    "CONNECTION_PRAGMAS",
    "MINIMUM_SQLITE_VERSION",
    "Database",
    "DatabaseConfigurationError",
    "create_database_engine",
    "get_database",
    "install_connection_hooks",
]
