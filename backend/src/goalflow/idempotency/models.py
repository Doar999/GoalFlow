"""幂等记录的 ORM 映射。结构以迁移 0002 为准，本文件跟随它。"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from goalflow.db.base import Base
from goalflow.db.types import UtcDateTime


class IdempotencyRecord(Base):
    """一次已成功提交的写请求。只有成功的请求会留下记录（决策 E6）。"""

    __tablename__ = "idempotency_requests"
    __table_args__ = (
        UniqueConstraint("owner_id", "request_key"),
        CheckConstraint("response_status BETWEEN 200 AND 299", name="response_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    operation: Mapped[str] = mapped_column(String(64))
    request_key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    result_type: Mapped[str] = mapped_column(String(32))
    result_id: Mapped[str] = mapped_column(String(36))
    response_status: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)
