"""Alembic 运行环境。

两处与 alembic init 默认模板的差别，都不是风格问题：

1. **连接从 `goalflow.db` 取，不用 alembic.ini 里的 `sqlalchemy.url`。** 迁移必须跑在
   与生产完全相同的连接参数上——`foreign_keys`、`busy_timeout` 是连接级 pragma，
   漏设不会报错。用 `engine_from_config` 另起一个 Engine 等于绕开那套装配，
   迁移就会在一个比生产宽松的环境里通过。
2. **`render_as_batch=True`。** SQLite 的 `ALTER TABLE` 只支持有限操作，改列类型或
   约束都要重建表。不开 batch 模式，这类迁移根本生成不出来；开了之后 Alembic 会生成
   "建新表 → 拷数据 → 删旧表 → 改名"。

**batch 模式的表重建是丢数据的地方**：漏一列就静默丢一列的数据，只断言"表还在"看不
出来。review 必须逐列核对，见 docs/engineering/01-contracts-and-ownership.md 第 6 节。
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection

from goalflow.core.config import get_settings
from goalflow.db.engine import create_database_engine

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 业务表尚未定义。第一张表由 T03 带进来时，在这里挂上它的 MetaData 才能用
# autogenerate；在那之前 autogenerate 会把所有表都当成"待删除"，不要用。
target_metadata = None


def _database_url() -> str:
    """迁移用的连接串。

    优先用命令行传入的 `-x url=...`，方便在演练库或备份副本上试跑迁移而不碰生产库；
    否则落到与应用同一份配置。
    """
    override = context.get_x_argument(as_dictionary=True).get("url")
    return override or get_settings().database_url


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        # 比对列类型变化。SQLite 的类型只是亲和性，不开这个的话改列类型的迁移会被
        # autogenerate 漏掉，而漏掉不会报错。
        compare_type=True,
    )


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连库。"""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：走生产那套连接装配。"""
    engine = create_database_engine(_database_url())
    try:
        with engine.connect() as connection:
            _configure(connection)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
