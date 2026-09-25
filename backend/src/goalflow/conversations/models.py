"""T08 对话与消息契约映射；业务写入在后续 PR 中实现。"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

import goalflow.auth.models
import goalflow.goals.models
import goalflow.jobs.models  # noqa: F401
from goalflow.db.base import Base
from goalflow.db.types import UtcDateTime


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint("revision >= 0", name="revision"),
        Index("ix_conversations_owner_goal", "owner_id", "goal_id"),
        Index("ix_conversations_owner_task", "owner_id", "task_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    goal_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("goals.id"))
    task_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("tasks.id"))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime())


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence"),
        CheckConstraint("sequence >= 1", name="sequence"),
        CheckConstraint("role IN ('user', 'assistant')", name="role"),
        Index("ix_messages_owner_conversation", "owner_id", "conversation_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversations.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    job_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("jobs.id"))
    client_request_key: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
