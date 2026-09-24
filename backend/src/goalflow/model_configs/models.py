"""个人模型配置表的 ORM 映射。结构以迁移 0007 为准，本文件跟随它。

字段与约束的出处：docs/development/07-model-provider-design.md 第 2 节
（原文标为"建议"，经 T14 交接卡决策 A1 落地为契约），命令边界见交接卡 A3—A6。

约定（沿用 links/models.py 的做法）：

- JSON 列在 ORM 里就是字符串，序列化在业务实现里做；库里有 json_valid() 兜底。
- 枚举列声明为 `Mapped[str]` + `String(n)`，枚举类型只用于取值校验与 CHECK 文本，
  不映射为 SQLAlchemy Enum——否则 compare_metadata 会把它当成与迁移不同的类型。
- 迁移手写，CHECK 文本与这里的构造逐字一致，由
  tests/db/test_models_match_migrations.py 比对。
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

# 只为把外键目标表登记进同一份 MetaData。
import goalflow.auth.models  # noqa: F401
from goalflow.contracts.enums import ModelApiMode, ModelProvider
from goalflow.db.base import Base
from goalflow.db.types import UtcDateTime


def _one_of(column: str, values: type[StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(member.value) for member in values)})"


class ModelConfig(Base):
    """一位用户的个人模型配置。凭证以密文入库，任何接口不回传凭证材料（T14 决策 A5）。

    状态语义（T14 决策 A4/A12）：enabled=0 是用户主动禁用（06 号：凭证支持替换、禁用和删除），
    凭证保留、可重新启用；deleted_at 非空是删除（归档）——凭证与密钥版本清空、默认标记清除、
    enabled 置 0，行保留以支撑作业所需非敏感历史信息，不可恢复。
    本地无鉴权模型可不填 Key（06 号第 2 节），因此活跃配置允许凭证为空。
    """

    __tablename__ = "model_configs"
    __table_args__ = (
        CheckConstraint(_one_of("model_provider", ModelProvider), name="model_provider"),
        CheckConstraint(
            f"{_one_of('api_mode', ModelApiMode)} OR api_mode IS NULL",
            name="api_mode",
        ),
        # anthropic 下 api_mode 必须为空（07 号第 2 节；T14 决策 A3）。
        CheckConstraint("api_mode IS NULL OR model_provider = 'openai'", name="api_mode_provider_pairing"),
        # 删除是终态：归档行必然已禁用（T14 决策 A4）。
        CheckConstraint("deleted_at IS NULL OR enabled = 0", name="deleted_disabled"),
        CheckConstraint("enabled IN (0, 1)", name="enabled"),
        CheckConstraint("is_default IN (0, 1)", name="is_default"),
        CheckConstraint("json_valid(capabilities_json)", name="capabilities_json"),
        # 每用户至多一个默认配置（T14 决策 A2：默认引用不落在 users 表）。
        Index("uq_model_configs_owner_default", "owner_id", unique=True, sqlite_where=text("is_default = 1")),
        Index("ix_model_configs_owner_id", "owner_id", "enabled"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(100))
    model_provider: Mapped[str] = mapped_column(String(16))
    # 仅 openai 有效（07 号第 2 节）；anthropic 下为空。
    api_mode: Mapped[str | None] = mapped_column(String(16))
    # 空 = provider 官方默认端点；非空时出站校验先于 LangChain 构造执行（T14 决策 A6）。
    base_url: Mapped[str | None] = mapped_column(String(500))
    # 供应商模型名（如 claude-sonnet-4），沿用 07 号文档拼写，与主键 id 无关。
    model_id: Mapped[str] = mapped_column(String(128))
    # 信封加密密文；主密钥由部署环境提供（GOALFLOW_CREDENTIAL_ENCRYPTION_KEY，T03 预置），
    # 不与密文同表。本地无鉴权模型允许为空（T14 决策 A11），归档行清空（T14 决策 A4/A10）。
    credential_ciphertext: Mapped[str | None] = mapped_column(Text)
    encryption_key_version: Mapped[str | None] = mapped_column(String(32))
    # 配置版本；PATCH 带 expected_revision 乐观锁，api_mode 修改视为版本变化（T14 决策 A3）。
    revision: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean)
    is_default: Mapped[bool] = mapped_column(Boolean)
    # 删除（归档）时间戳；非空即已删除，与"禁用"区分（T14 决策 A4/A12）。
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    # 能力记录：basic_generation / structured_output / streaming 三项三态
    # （07 号第 1 节；T14 决策 A8）。测试时间和配置版本在 last_test_at 与 revision 上。
    capabilities_json: Mapped[str | None] = mapped_column(Text)
    last_test_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime())
