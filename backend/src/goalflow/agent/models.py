"""T08 模型调用计量契约映射；不保存凭证与提示正文。"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

import goalflow.auth.models
import goalflow.jobs.models  # noqa: F401
from goalflow.db.base import Base
from goalflow.db.types import UtcDateTime


class ModelCall(Base):
    __tablename__ = "model_calls"
    __table_args__ = (
        CheckConstraint("provider IN ('openai', 'anthropic')", name="provider"),
        CheckConstraint("status IN ('succeeded', 'failed')", name="status"),
        CheckConstraint("input_tokens IS NULL OR input_tokens >= 0", name="input_tokens"),
        CheckConstraint("output_tokens IS NULL OR output_tokens >= 0", name="output_tokens"),
        CheckConstraint("estimated_cost IS NULL OR estimated_cost >= 0", name="estimated_cost"),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="latency_ms"),
        Index("ix_model_calls_owner_job", "owner_id", "job_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id"))
    provider: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    status: Mapped[str] = mapped_column(String(16))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
