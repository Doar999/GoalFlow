"""ORM 模型与迁移建出的真实库结构一致（T03 决策 C2）。

迁移手写、不用 autogenerate，所以两边可能各改各的。漂移不会在建表时报错，而是在某条
查询或某次 batch 重建时才暴露——这里在每次 PR 上把两边对一遍。

新模块加了模型，要在下面补一行 import，否则它的表会被当成"库里有、模型里没有"。
"""

import argparse
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import CheckConstraint, inspect

import goalflow.auth.models  # 注册账号表（F401 只报在同一包名的最后一条 import 上）
import goalflow.goals.models
import goalflow.idempotency.models  # 注册幂等表
import goalflow.jobs.models
import goalflow.links.models
import goalflow.model_configs.models
import goalflow.scheduling.models  # noqa: F401  注册排期表
from goalflow.db.base import Base
from goalflow.db.engine import create_database_engine

_MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


def _alembic(url: str, action: str, revision: str) -> None:
    config = Config()
    config.set_main_option("script_location", _MIGRATIONS.as_posix())
    config.cmd_opts = argparse.Namespace(x=[f"url={url}"])
    getattr(command, action)(config, revision)


@pytest.fixture
def url(tmp_path: Path) -> str:
    return f"sqlite+pysqlite:///{(tmp_path / 'app.db').as_posix()}"


def test_models_match_the_migrated_schema(url: str):
    _alembic(url, "upgrade", "head")
    engine = create_database_engine(url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            assert compare_metadata(context, Base.metadata) == []
    finally:
        engine.dispose()


def test_check_constraints_match_by_name_and_expression(url: str):
    """compare_metadata 不比 CHECK 约束，单独比。"""
    _alembic(url, "upgrade", "head")
    engine = create_database_engine(url)
    try:
        inspector = inspect(engine)
        for table in Base.metadata.sorted_tables:
            expected = {
                (constraint.name, str(constraint.sqltext))
                for constraint in table.constraints
                if isinstance(constraint, CheckConstraint)
            }
            actual = {(item["name"], item["sqltext"]) for item in inspector.get_check_constraints(table.name)}
            assert actual == expected, table.name
    finally:
        engine.dispose()


def test_account_migration_round_trips(url: str):
    """降级必须真的把表拿掉，再升级必须回到同一结构——否则回滚预案是纸面上的。"""
    _alembic(url, "upgrade", "head")
    engine = create_database_engine(url)
    try:
        before = {name: inspect(engine).get_columns(name) for name in inspect(engine).get_table_names()}
    finally:
        engine.dispose()

    _alembic(url, "downgrade", "base")
    engine = create_database_engine(url)
    try:
        assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    finally:
        engine.dispose()

    _alembic(url, "upgrade", "head")
    engine = create_database_engine(url)
    try:
        after = {name: inspect(engine).get_columns(name) for name in inspect(engine).get_table_names()}
    finally:
        engine.dispose()
    assert {name: [str(column) for column in columns] for name, columns in after.items()} == {
        name: [str(column) for column in columns] for name, columns in before.items()
    }
