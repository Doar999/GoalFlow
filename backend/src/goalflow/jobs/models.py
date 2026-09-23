"""作业三张表的 ORM 映射。结构以迁移 0003 为准，本文件跟随它。

JSON 列在 ORM 里就是字符串，序列化与反序列化在 service 里做；库里有 json_valid() 兜底。
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

# 只为把外键目标 users 表登记进同一份 MetaData，不读写账号表（T07 决策 E28）。
# Worker 进程不 import 账号模块，缺了它第一次写入就会 NoReferencedTableError。
import goalflow.auth.models  # noqa: F401
from goalflow.contracts.enums import JobEventType, JobStatus
from goalflow.db.base import Base
from goalflow.db.types import UtcDateTime


def _one_of(column: str, values: type[StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(member.value) for member in values)})"


# 占用去重键的状态（E10）。failed、cancelled、stale 之后同一输入可以重新提交。
DEDUPE_HOLDING_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRY_WAIT, JobStatus.SUCCEEDED)

OUTBOX_DISPATCH_JOB = "dispatch_job"
OUTBOX_PENDING = "pending"
OUTBOX_SENT = "sent"


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(_one_of("status", JobStatus), name="status"),
        CheckConstraint("attempts >= 0", name="attempts"),
        CheckConstraint("json_valid(input_refs_json)", name="input_refs_json"),
        CheckConstraint("result_refs_json IS NULL OR json_valid(result_refs_json)", name="result_refs_json"),
        Index(
            "uq_jobs_active_dedupe",
            "owner_id",
            "kind",
            "dedupe_key",
            unique=True,
            sqlite_where=text(f"status IN ({', '.join(repr(status.value) for status in DEDUPE_HOLDING_STATUSES)})"),
        ),
        Index("ix_jobs_owner_id", "owner_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(64))
    dedupe_key: Mapped[str] = mapped_column(String(200))
    input_refs_json: Mapped[str] = mapped_column(Text)
    # 提交时输入所处的版本（如 planning_revision）。处理函数提交结果前据此判断输入是否已过期。
    input_revision: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), index=True)
    # 领取时加 1，所以它等于"已经开始过的尝试次数"。
    attempts: Mapped[int] = mapped_column(Integer)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(UtcDateTime())
    cancel_requested_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    next_attempt_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    result_refs_json: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))
    # 面向用户的说明。不含凭证、不含其他用户的数据，也不放异常原文。
    error_message: Mapped[str | None] = mapped_column(String(500))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime())


class JobEvent(Base):
    __tablename__ = "job_events"
    __table_args__ = (
        UniqueConstraint("job_id", "sequence"),
        CheckConstraint(_one_of("event_type", JobEventType), name="event_type"),
        CheckConstraint("sequence >= 1", name="sequence"),
        CheckConstraint("json_valid(payload_json)", name="payload_json"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id"))
    # 单个作业内从 1 起连续递增，SSE 的 id 就是它（E19、E20）。
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(32))
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())


class OutboxEvent(Base):
    """待投递给队列的消息。与作业同事务写入，投递成功后标记为 sent（E16）。"""

    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint(f"event_type IN ({OUTBOX_DISPATCH_JOB!r})", name="event_type"),
        CheckConstraint(f"status IN ({OUTBOX_PENDING!r}, {OUTBOX_SENT!r})", name="status"),
        Index("ix_outbox_events_status", "status", "next_attempt_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    attempts: Mapped[int] = mapped_column(Integer)
    next_attempt_at: Mapped[datetime] = mapped_column(UtcDateTime())
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
    sent_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
