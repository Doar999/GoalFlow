"""T14 create model config table

建 model_configs。字段与取舍见 docs/worklog/T14-model-config.md 决策 A1—A14、
docs/development/07-model-provider-design.md 第 2 节（原文标为"建议"，经决策 A1 落地为契约）
与 docs/product/06-model-configuration.md（建议，经决策 A11/A12 采纳）。

- 用户默认配置引用不落 users 表：is_default 列 + 部分唯一索引保证每用户至多一个默认，
  避免跨模块修改 T03 的账号表（决策 A2）。
- anthropic 下 api_mode 必须为空，由 ck_model_configs_api_mode_provider_pairing 强制。
- 本地无鉴权模型可不填 Key（06 号第 2 节，决策 A11），活跃配置允许凭证为空，
  故不设"有 enabled 必有凭证"的 CHECK。
- 禁用与删除是两个操作（06 号：凭证支持替换、禁用和删除，决策 A12）：enabled=0 是
  用户禁用（凭证保留、可恢复）；deleted_at 非空是删除归档——凭证清空、默认标记清除、
  enabled 置 0、行保留以支撑作业所需非敏感历史信息，不可恢复（决策 A4）。
  ck_model_configs_deleted_disabled 强制归档行必然已禁用。
- 凭证以信封加密密文入库，主密钥由部署环境提供（GOALFLOW_CREDENTIAL_ENCRYPTION_KEY，
  T03 预置），不与密文同表（决策 A10）。

手写迁移，不 import 应用代码。约束名与 goalflow.db.base.NAMING_CONVENTION 推出的名字一致，
CHECK 文本与 goalflow.model_configs.models 的构造逐字一致，由
tests/db/test_models_match_migrations.py 比对。

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ID = 36


def upgrade() -> None:
    op.create_table(
        "model_configs",
        sa.Column("id", sa.String(_ID), nullable=False),
        sa.Column("owner_id", sa.String(_ID), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("model_provider", sa.String(16), nullable=False),
        sa.Column("api_mode", sa.String(16), nullable=True),
        sa.Column("base_url", sa.String(500), nullable=True),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("credential_ciphertext", sa.Text(), nullable=True),
        sa.Column("encryption_key_version", sa.String(32), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("deleted_at", sa.String(32), nullable=True),
        sa.Column("capabilities_json", sa.Text(), nullable=True),
        sa.Column("last_test_at", sa.String(32), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_model_configs"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_model_configs_owner_id_users"),
        sa.CheckConstraint("model_provider IN ('openai', 'anthropic')", name="ck_model_configs_model_provider"),
        sa.CheckConstraint(
            "api_mode IN ('responses', 'chat_completions') OR api_mode IS NULL",
            name="ck_model_configs_api_mode",
        ),
        sa.CheckConstraint(
            "api_mode IS NULL OR model_provider = 'openai'",
            name="ck_model_configs_api_mode_provider_pairing",
        ),
        sa.CheckConstraint(
            "deleted_at IS NULL OR enabled = 0",
            name="ck_model_configs_deleted_disabled",
        ),
        sa.CheckConstraint("enabled IN (0, 1)", name="ck_model_configs_enabled"),
        sa.CheckConstraint("is_default IN (0, 1)", name="ck_model_configs_is_default"),
        sa.CheckConstraint("json_valid(capabilities_json)", name="ck_model_configs_capabilities_json"),
    )
    # 每用户至多一个默认配置（T14 决策 A2）。
    op.create_index(
        "uq_model_configs_owner_default",
        "model_configs",
        ["owner_id"],
        unique=True,
        sqlite_where=sa.text("is_default = 1"),
    )
    op.create_index("ix_model_configs_owner_id", "model_configs", ["owner_id", "enabled"])


def downgrade() -> None:
    op.drop_index("ix_model_configs_owner_id", table_name="model_configs")
    op.drop_index("uq_model_configs_owner_default", table_name="model_configs")
    op.drop_table("model_configs")
