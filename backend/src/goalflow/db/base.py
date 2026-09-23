"""ORM 声明基类（T03 决策 C2）。

全项目只有这一个 `Base`、一份 `MetaData`。各业务模块的模型继承它，但本模块**不 import
任何业务模型**——`db/` 被所有业务模块依赖，反向依赖会立刻变成循环。

迁移一律手写，不用 autogenerate。ORM 与迁移是否一致由
`backend/tests/db/test_models_match_migrations.py` 比对：新模块加了模型，要在那里补一行 import。
"""

from typing import Final

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# 约束名必须确定。SQLite 的 batch 模式重建表时按名字识别约束，匿名约束在重建后
# 会变成另一个名字，下一条迁移就再也找不到它。手写迁移里的约束名要与这里推出的一致。
NAMING_CONVENTION: Final = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
