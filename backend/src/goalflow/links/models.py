"""目标关联、任务依赖与变更提案表的 ORM 映射。结构以迁移 0006 为准，本文件跟随它。

字段与约束的出处：docs/development/03-data-model.md 第 2、3 节，
命令边界见 18-goal-link-design.md 第 1—3 节（T06 交接卡决策 A1—A9）。

约定（沿用 goals/models.py 的做法）：

- JSON 列在 ORM 里就是字符串，序列化在业务实现里做；库里有 json_valid() 兜底。
- 枚举列声明为 `Mapped[str]` + `String(n)`，枚举类型只用于取值校验与 CHECK 文本，
  不映射为 SQLAlchemy Enum——否则 compare_metadata 会把它当成与迁移不同的类型。
- 迁移手写，CHECK 文本与这里的构造逐字一致，由
  tests/db/test_models_match_migrations.py 比对。
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

# 只为把外键目标表登记进同一份 MetaData。
import goalflow.auth.models
import goalflow.goals.models  # noqa: F401
from goalflow.contracts.enums import ChangeClass, ChangeProposalStatus, DependencyOutcome, GoalLinkStatus
from goalflow.db.base import Base
from goalflow.db.types import UtcDateTime


def _one_of(column: str, values: type[StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(member.value) for member in values)})"


def _json_valid(column: str) -> str:
    return f"json_valid({column})"


class GoalLink(Base):
    """两个目标之间的用户确认关系。规范化目标对唯一，不设软失效状态（03 第 2 节）。"""

    __tablename__ = "goal_links"
    __table_args__ = (
        CheckConstraint(_one_of("status", GoalLinkStatus), name="status"),
        # 规范化方向：propose 时业务层把目标对按 id 排序后写入（T06 决策 A2）。
        CheckConstraint("goal_a_id < goal_b_id", name="canonical_pair"),
        # 未失效的关联对目标对唯一；removed 行保留归档，解除后可重建（T06 决策 A2）。
        Index(
            "uq_goal_links_active_pair",
            "goal_a_id",
            "goal_b_id",
            unique=True,
            sqlite_where=text(f"status IN ({GoalLinkStatus.PROPOSED.value!r}, {GoalLinkStatus.ACTIVE.value!r})"),
        ),
        Index("ix_goal_links_owner_id", "owner_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    goal_a_id: Mapped[str] = mapped_column(String(36), ForeignKey("goals.id"))
    goal_b_id: Mapped[str] = mapped_column(String(36), ForeignKey("goals.id"))
    status: Mapped[str] = mapped_column(String(16))
    # proposed 阶段为空，confirm 时写入（18 号第 1、2 节）。
    confirmed_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime())


class TaskDependency(Base):
    """任务先后依赖边。跨目标边必须挂在已确认关联下，由业务层校验（03 第 57 行）。"""

    __tablename__ = "task_dependencies"
    __table_args__ = (
        CheckConstraint(_one_of("required_outcome", DependencyOutcome), name="required_outcome"),
        # 自依赖是平凡环，DB 层直接禁止（T06 决策 A4）。
        CheckConstraint("predecessor_task_id <> successor_task_id", name="no_self_edge"),
        # 一个版本内边唯一（03 第 3 节）。
        UniqueConstraint("plan_version_id", "predecessor_task_id", "successor_task_id"),
        Index("ix_task_dependencies_goal_link_id", "goal_link_id"),
        Index("ix_task_dependencies_successor_task_id", "successor_task_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    plan_version_id: Mapped[str] = mapped_column(String(36), ForeignKey("plan_versions.id"))
    predecessor_task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"))
    successor_task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"))
    required_outcome: Mapped[str] = mapped_column(String(32))
    # 同目标版本内的依赖边不挂 link；跨目标边必须有已确认关联（T06 决策 A4）。
    goal_link_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("goal_links.id"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())


class ChangeProposal(Base):
    """变更提案。解除关联等重大变化先提案后原子应用（03 第 3 节；18 号第 3 节）。"""

    __tablename__ = "change_proposals"
    __table_args__ = (
        CheckConstraint(_one_of("change_class", ChangeClass), name="change_class"),
        CheckConstraint(_one_of("status", ChangeProposalStatus), name="status"),
        CheckConstraint(_json_valid("base_versions_json"), name="base_versions_json"),
        CheckConstraint(_json_valid("proposed_patch_json"), name="proposed_patch_json"),
        CheckConstraint(_json_valid("impact_json"), name="impact_json"),
        Index("ix_change_proposals_owner_id", "owner_id", "goal_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    goal_id: Mapped[str] = mapped_column(String(36), ForeignKey("goals.id"))
    # 提案所依据的计划版本等基础状态快照；stale 判定依据（T06 决策 A6）。
    base_versions_json: Mapped[str] = mapped_column(Text)
    input_revision: Mapped[int] = mapped_column(Integer)
    proposed_patch_json: Mapped[str] = mapped_column(Text)
    # 影响分析：未满足依赖边、后继任务及执行状态、criteria_unsatisfiable 标记等（18 号第 3 节）。
    impact_json: Mapped[str] = mapped_column(Text)
    change_class: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(500))
    accepted_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    applied_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime())
