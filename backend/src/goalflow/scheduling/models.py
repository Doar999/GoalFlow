"""时间预算与每日安排族表的 ORM 映射。结构以迁移 0005 为准，本文件跟随它。

字段与约束的出处：docs/development/03-data-model.md 第 4 节，排期行为见
13-scheduling-engine.md（T05 交接卡决策 A1—A4、A7—A10）。

约定（沿用 goals/models.py 的做法）：

- JSON 列在 ORM 里就是字符串，序列化在业务实现里做；库里有 json_valid() 兜底。
- 枚举列声明为 `Mapped[str]` + `String(n)`，枚举类型只用于取值校验与 CHECK 文本，
  不映射为 SQLAlchemy Enum——否则 compare_metadata 会把它当成与迁移不同的类型。
- 迁移手写，CHECK 文本与这里的构造逐字一致，由
  tests/db/test_models_match_migrations.py 比对。
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

# 只为把外键目标表登记进同一份 MetaData。
import goalflow.auth.models
import goalflow.goals.models  # noqa: F401  tasks/task_specs 被 agenda_items 引用
from goalflow.contracts.enums import (
    AgendaRevisionStatus,
    DailyOverrideKind,
    GoalFocusStatus,
    TaskDayConstraintKind,
    TaskDayConstraintStatus,
)
from goalflow.db.base import Base
from goalflow.db.types import UtcDateTime


def _one_of(column: str, values: type[StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(member.value) for member in values)})"


_DATE_IS_DATE = "{column} = date({column})"


def _json_valid(column: str) -> str:
    return f"json_valid({column})"


class UserPlanningState(Base):
    """每用户一个的排期协调入口（03 第 4 节）。

    目标优先级、用户锁定、任务状态、可用时间、计划启用和执行反馈都会递增 revision；
    应用服务在短写事务里重查它判断输入是否已变化（13 号第 7 节）。
    owner_id 即主键：每用户一行。
    """

    __tablename__ = "user_planning_state"

    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime())


class AvailabilityVersion(Base):
    """周额度版本：周一至周日七个额度，按生效日期选择版本（03 第 4 节）。

    新版本不覆盖旧版本，历史安排凭当时生效的版本解释（R08）。
    """

    __tablename__ = "availability_versions"
    __table_args__ = (
        UniqueConstraint("owner_id", "version_no"),
        CheckConstraint(_DATE_IS_DATE.format(column="effective_from"), name="effective_from_format"),
        CheckConstraint(_json_valid("weekly_minutes_json"), name="weekly_minutes_json"),
        Index("ix_availability_versions_owner_id", "owner_id", "effective_from"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    effective_from: Mapped[str] = mapped_column(String(10))
    # 键为周一到周日七个日期名，值为当日额度分钟数。
    weekly_minutes_json: Mapped[str] = mapped_column(Text)
    version_no: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())


class DailyOverride(Base):
    """用户对单日额度的声明（03 第 4 节）。

    total 与 remaining 是两种口径，不可混用：total 的剩余容量为
    max(0, minutes − 已投入)；remaining 从声明时点起扣除后续投入。
    """

    __tablename__ = "daily_overrides"
    __table_args__ = (
        UniqueConstraint("owner_id", "local_date"),
        CheckConstraint(_one_of("override_kind", DailyOverrideKind), name="override_kind"),
        CheckConstraint(_DATE_IS_DATE.format(column="local_date"), name="local_date_format"),
        CheckConstraint("minutes >= 0", name="minutes"),
        Index("ix_daily_overrides_owner_id", "owner_id", "local_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    local_date: Mapped[str] = mapped_column(String(10))
    override_kind: Mapped[str] = mapped_column(String(16))
    minutes: Mapped[int] = mapped_column(Integer)
    # 用户声明剩余额度的时间点；remaining 口径从此刻起扣减（03 第 4 节）。
    measured_at: Mapped[datetime] = mapped_column(UtcDateTime())
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime())


class GoalSchedulingPreference(Base):
    """目标的调度偏好：focus 状态与顺序（03 第 4 节）。

    默认同等优先；focus/rank 只参与弹性任务取舍，不越过硬期限或进行中任务
    （13 号第 9 节）。无偏好行的目标按默认值参与排期。
    """

    __tablename__ = "goal_scheduling_preferences"
    __table_args__ = (
        UniqueConstraint("owner_id", "goal_id"),
        CheckConstraint(_one_of("focus_status", GoalFocusStatus), name="focus_status"),
        CheckConstraint("rank >= 1", name="rank"),
        Index("ix_goal_scheduling_preferences_owner_id", "owner_id", "goal_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    goal_id: Mapped[str] = mapped_column(String(36), ForeignKey("goals.id"))
    focus_status: Mapped[str] = mapped_column(String(16))
    rank: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime())


class TaskDayConstraint(Base):
    """用户对任务在某一天的锁定（03 第 4 节）。

    must_do_today 固定日期；locked 还固定当前分配分钟数（13 号第 4 节）。
    清除保留 cleared 行不删行，历史可解释（Q08，T05 决策 A8）。
    """

    __tablename__ = "task_day_constraints"
    __table_args__ = (
        UniqueConstraint("owner_id", "task_id", "local_date"),
        CheckConstraint(_one_of("constraint_kind", TaskDayConstraintKind), name="constraint_kind"),
        CheckConstraint(_one_of("status", TaskDayConstraintStatus), name="status"),
        CheckConstraint(_DATE_IS_DATE.format(column="local_date"), name="local_date_format"),
        Index("ix_task_day_constraints_owner_id", "owner_id", "local_date", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"))
    local_date: Mapped[str] = mapped_column(String(10))
    constraint_kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime())


class DailyAgenda(Base):
    """稳定的每日页面身份（03 第 4 节）。

    current_revision_id 指向当前生效的排期结果；重排创建新 agenda_revision 并切换指针，
    旧 revision 保留可查（Q08）。首次生成前为空。
    """

    __tablename__ = "daily_agendas"
    __table_args__ = (
        UniqueConstraint("owner_id", "local_date"),
        CheckConstraint(_DATE_IS_DATE.format(column="local_date"), name="local_date_format"),
        CheckConstraint(_json_valid("timezone_snapshot_json"), name="timezone_snapshot_json"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    local_date: Mapped[str] = mapped_column(String(10))
    # 计算时使用的用户本地日期与时区快照（13 号第 1 节 SchedulingSnapshot）。
    timezone_snapshot_json: Mapped[str] = mapped_column(Text)
    current_revision_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agenda_revisions.id", use_alter=True)
    )
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime())


class AgendaRevision(Base):
    """一次排期计算的不可变结果（03 第 4 节）。

    input_planning_revision 记录计算基于的协调入口版本，是并发判定的依据；
    scheduling_policy_version 记录当时按哪版排期策略判定（T05 决策 A1）。
    change_proposals 序列化进 conflict_json 的 change_proposals 键（T05 决策 A9）。
    """

    __tablename__ = "agenda_revisions"
    __table_args__ = (
        UniqueConstraint("agenda_id", "version_no"),
        CheckConstraint(_one_of("status", AgendaRevisionStatus), name="status"),
        CheckConstraint("input_planning_revision >= 0", name="input_planning_revision"),
        CheckConstraint(_json_valid("capacity_snapshot_json"), name="capacity_snapshot_json"),
        CheckConstraint(_json_valid("deferred_json"), name="deferred_json"),
        CheckConstraint(_json_valid("conflict_json"), name="conflict_json"),
        Index("ix_agenda_revisions_owner_id", "owner_id", "agenda_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    agenda_id: Mapped[str] = mapped_column(String(36), ForeignKey("daily_agendas.id"))
    version_no: Mapped[int] = mapped_column(Integer)
    input_planning_revision: Mapped[int] = mapped_column(Integer)
    scheduling_policy_version: Mapped[str] = mapped_column(String(64))
    # 额度来源、已投入、已保留和剩余分钟（13 号第 1 节 capacity_summary）。
    capacity_snapshot_json: Mapped[str] = mapped_column(Text)
    # 未进入当天的任务及原因（13 号第 1 节 deferred_tasks）。
    deferred_json: Mapped[str] = mapped_column(Text)
    # 无法满足的硬约束、分钟缺口与待确认变更建议（13 号第 1 节 conflicts、change_proposals）。
    conflict_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())


class AgendaItem(Base):
    """单次排期结果里的一个任务分配（03 第 4 节）。

    排期只分配分钟和顺序，不生成起止时刻；同一天同一任务最多一个 agenda item
    （13 号第 5 节）。无法容纳任何分量的任务进 deferred_tasks，不产生 0 分钟分配，
    因此 allocated_minutes 必须 > 0。
    """

    __tablename__ = "agenda_items"
    __table_args__ = (
        UniqueConstraint("agenda_revision_id", "task_id"),
        CheckConstraint("rank >= 1", name="rank"),
        CheckConstraint("allocated_minutes > 0", name="allocated_minutes"),
        CheckConstraint(_json_valid("reason_codes_json"), name="reason_codes_json"),
        Index("ix_agenda_items_owner_id", "owner_id", "task_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    agenda_revision_id: Mapped[str] = mapped_column(String(36), ForeignKey("agenda_revisions.id"))
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"))
    task_spec_id: Mapped[str] = mapped_column(String(36), ForeignKey("task_specs.id"))
    rank: Mapped[int] = mapped_column(Integer)
    allocated_minutes: Mapped[int] = mapped_column(Integer)
    # 排序键、所选层级及原因码（13 号第 4 节）。
    reason_codes_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
