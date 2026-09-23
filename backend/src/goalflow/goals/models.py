"""目标、档案、路线与计划族表的 ORM 映射。结构以迁移 0004 为准，本文件跟随它。

字段与约束的出处：docs/development/03-data-model.md 第 1—3 节，生命周期字段见
17-goal-lifecycle-design.md 第 6 节（T04 交接卡决策 A2、A8—A12）。

约定（沿用 jobs/models.py 的做法）：

- JSON 列在 ORM 里就是字符串，序列化在业务实现里做；库里有 json_valid() 兜底。
- 枚举列声明为 `Mapped[str]` + `String(n)`，枚举类型只用于取值校验与 CHECK 文本，
  不映射为 SQLAlchemy Enum——否则 compare_metadata 会把它当成与迁移不同的类型。
- 迁移手写，CHECK 文本与这里的构造逐字一致，由
  tests/db/test_models_match_migrations.py 比对。
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

# 只为把外键目标表登记进同一份 MetaData；Worker 进程不会 import 账号与作业模块。
import goalflow.auth.models
import goalflow.idempotency.models
import goalflow.jobs.models  # noqa: F401
from goalflow.contracts.enums import (
    ClosureKind,
    GoalDomain,
    GoalKind,
    GoalStatus,
    PlanVersionStatus,
    ProfileDraftReadiness,
    ReviewPeriod,
    RouteSetStatus,
    RouteStatus,
    TaskBatchStatus,
    TaskExecutionStatus,
    TaskExecutor,
)
from goalflow.db.base import Base
from goalflow.db.types import UtcDateTime


def _one_of(column: str, values: type[StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(member.value) for member in values)})"


# —— 通用 CHECK 片段，迁移里的文本与这里逐字一致 ——

_DATE_IS_DATE = "{column} = date({column})"
_DATE_IS_DATE_OR_NULL = "{column} IS NULL OR {column} = date({column})"


def _json_valid(column: str) -> str:
    return f"json_valid({column})"


class Goal(Base):
    """目标聚合根。状态转换是显式命令的产物，不是任何统计量的副作用（17 号原则）。"""

    __tablename__ = "goals"
    __table_args__ = (
        CheckConstraint(_one_of("domain", GoalDomain), name="domain"),
        CheckConstraint(_one_of("kind", GoalKind), name="kind"),
        CheckConstraint(_one_of("status", GoalStatus), name="status"),
        CheckConstraint(_one_of("review_period", ReviewPeriod), name="review_period"),
        CheckConstraint(_one_of("closure_kind", ClosureKind), name="closure_kind"),
        # closed_at 与 closure_kind 必须成对出现（17 号第 4 节 close_goal）。
        CheckConstraint(
            "(closed_at IS NULL AND closure_kind IS NULL) OR (closed_at IS NOT NULL AND closure_kind IS NOT NULL)",
            name="closure_pair",
        ),
        # completed 仅对达成型开放（D12）；stopped 对两种形态都合法。
        CheckConstraint(
            "closure_kind IS NULL OR closure_kind = 'stopped' OR kind = 'achievement'",
            name="completed_requires_achievement",
        ),
        CheckConstraint(
            "domain_confidence IS NULL OR (domain_confidence >= 0 AND domain_confidence <= 1)",
            name="domain_confidence",
        ),
        CheckConstraint(
            f"closure_criteria_snapshot_json IS NULL OR {_json_valid('closure_criteria_snapshot_json')}",
            name="closure_criteria_snapshot_json",
        ),
        Index("ix_goals_owner_id", "owner_id", "status"),
        Index("ix_goals_source_goal_id", "source_goal_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str] = mapped_column(String(16))
    domain_confidence: Mapped[float | None] = mapped_column(Float)
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    # 目标隔离规则的取值由 T06 定义；先占位存储，不加 CHECK。
    isolation_mode: Mapped[str | None] = mapped_column(String(16))
    # 两个反向引用与子表的 goal_id 外键构成循环，use_alter 让元数据排序可解
    # （消除 sorted_tables 的 cycle 告警）；迁移是手写的，该标记不影响库结构。
    active_profile_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("goal_profiles.id", use_alter=True))
    current_plan_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("plan_versions.id", use_alter=True)
    )
    paused_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    pause_reason: Mapped[str | None] = mapped_column(String(500))
    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    closure_kind: Mapped[str | None] = mapped_column(String(16))
    closure_note: Mapped[str | None] = mapped_column(String(2000))
    # 结束时一次写入的成功标准逐条确认快照（T04 决策 A8）。
    closure_criteria_snapshot_json: Mapped[str | None] = mapped_column(Text)
    review_period: Mapped[str] = mapped_column(String(16))
    # "以此为起点新建"的派生关系（T04 决策 A8 清单第 7 项）。
    source_goal_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("goals.id"))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime())


class GoalProfileDraft(Base):
    """可修改的澄清交互状态；确认后的不可变版本在 goal_profiles（03 第 2 节）。"""

    __tablename__ = "goal_profile_drafts"
    __table_args__ = (
        # 每个目标一个当前草稿。
        UniqueConstraint("goal_id"),
        CheckConstraint(_one_of("readiness", ProfileDraftReadiness), name="readiness"),
        CheckConstraint(_json_valid("content_json"), name="content_json"),
        CheckConstraint(_json_valid("source_map_json"), name="source_map_json"),
        CheckConstraint(_json_valid("gaps_json"), name="gaps_json"),
        CheckConstraint(_json_valid("assumptions_json"), name="assumptions_json"),
        CheckConstraint(_json_valid("contradictions_json"), name="contradictions_json"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    goal_id: Mapped[str] = mapped_column(String(36), ForeignKey("goals.id"))
    content_json: Mapped[str] = mapped_column(Text)
    source_map_json: Mapped[str] = mapped_column(Text)
    gaps_json: Mapped[str] = mapped_column(Text)
    assumptions_json: Mapped[str] = mapped_column(Text)
    contradictions_json: Mapped[str] = mapped_column(Text)
    readiness: Mapped[str] = mapped_column(String(16))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime())


class GoalProfile(Base):
    """用户确认后的不可变档案版本。修订创建新 version_no，不改写旧版本（R05、T04 目标）。"""

    __tablename__ = "goal_profiles"
    __table_args__ = (
        UniqueConstraint("goal_id", "version_no"),
        CheckConstraint(_json_valid("success_criteria_json"), name="success_criteria_json"),
        CheckConstraint(_json_valid("baseline_json"), name="baseline_json"),
        CheckConstraint(_json_valid("constraints_json"), name="constraints_json"),
        CheckConstraint(_json_valid("facts_json"), name="facts_json"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    goal_id: Mapped[str] = mapped_column(String(36), ForeignKey("goals.id"))
    version_no: Mapped[int] = mapped_column(Integer)
    result_definition: Mapped[str] = mapped_column(Text)
    success_criteria_json: Mapped[str] = mapped_column(Text)
    baseline_json: Mapped[str] = mapped_column(Text)
    constraints_json: Mapped[str] = mapped_column(Text)
    facts_json: Mapped[str] = mapped_column(Text)
    confirmed_at: Mapped[datetime] = mapped_column(UtcDateTime())


class PlanningSession(Base):
    """澄清到启用的交互进度。等待用户不占用 Worker（03 第 2 节）。"""

    __tablename__ = "planning_sessions"
    __table_args__ = (UniqueConstraint("goal_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    goal_id: Mapped[str] = mapped_column(String(36), ForeignKey("goals.id"))
    # 会话状态取值随澄清引擎（T08）细化，先不加 CHECK。
    state: Mapped[str] = mapped_column(String(32))
    profile_draft_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("goal_profile_drafts.id"))
    confirmed_profile_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("goal_profiles.id"))
    selected_route_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("routes.id"))
    draft_plan_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("plan_versions.id"))
    current_job_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("jobs.id"))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime())


class RouteSet(Base):
    """一组可比较的路线。绑定目标档案、策略及预算快照（11 号第 2 节）。"""

    __tablename__ = "route_sets"
    __table_args__ = (
        CheckConstraint(_one_of("status", RouteSetStatus), name="status"),
        CheckConstraint(_json_valid("input_snapshot_json"), name="input_snapshot_json"),
        Index("ix_route_sets_owner_id", "owner_id", "goal_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    goal_id: Mapped[str] = mapped_column(String(36), ForeignKey("goals.id"))
    profile_id: Mapped[str] = mapped_column(String(36), ForeignKey("goal_profiles.id"))
    planning_revision: Mapped[int] = mapped_column(Integer)
    availability_revision: Mapped[int] = mapped_column(Integer)
    generation_policy_version: Mapped[str] = mapped_column(String(64))
    input_snapshot_json: Mapped[str] = mapped_column(Text)
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    invalidated_reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())


class Route(Base):
    """结构化路线。修改生成新变体或新集合，不覆盖已被计划引用的路线（11 号第 6 节）。"""

    __tablename__ = "routes"
    __table_args__ = (
        CheckConstraint(_one_of("status", RouteStatus), name="status"),
        CheckConstraint("weekly_minutes >= 0", name="weekly_minutes"),
        CheckConstraint(_json_valid("difference_keys_json"), name="difference_keys_json"),
        CheckConstraint(_json_valid("duration_range_json"), name="duration_range_json"),
        CheckConstraint(_json_valid("phase_outline_json"), name="phase_outline_json"),
        CheckConstraint(_json_valid("tradeoffs_json"), name="tradeoffs_json"),
        CheckConstraint(_json_valid("risks_json"), name="risks_json"),
        CheckConstraint(_json_valid("assumptions_json"), name="assumptions_json"),
        CheckConstraint(_json_valid("resources_json"), name="resources_json"),
        # 派生指标由服务端计算，模型不能覆盖（11 号第 4 节）。
        CheckConstraint(_json_valid("derived_metrics_json"), name="derived_metrics_json"),
        Index("ix_routes_route_set_id", "route_set_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    route_set_id: Mapped[str] = mapped_column(String(36), ForeignKey("route_sets.id"))
    based_on_route_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("routes.id"))
    status: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(200))
    approach: Mapped[str] = mapped_column(Text)
    difference_keys_json: Mapped[str] = mapped_column(Text)
    duration_range_json: Mapped[str] = mapped_column(Text)
    phase_outline_json: Mapped[str] = mapped_column(Text)
    weekly_minutes: Mapped[int] = mapped_column(Integer)
    tradeoffs_json: Mapped[str] = mapped_column(Text)
    risks_json: Mapped[str] = mapped_column(Text)
    assumptions_json: Mapped[str] = mapped_column(Text)
    resources_json: Mapped[str] = mapped_column(Text)
    derived_metrics_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())


class PlanVersion(Base):
    """不可变的战略结构：阶段、里程碑、核心依赖与总体假设（12 号第 1 节）。"""

    __tablename__ = "plan_versions"
    __table_args__ = (
        UniqueConstraint("goal_id", "version_no"),
        CheckConstraint(_one_of("status", PlanVersionStatus), name="status"),
        CheckConstraint(_DATE_IS_DATE.format(column="start_date"), name="start_date_format"),
        CheckConstraint(_DATE_IS_DATE_OR_NULL.format(column="horizon_end"), name="horizon_end_format"),
        CheckConstraint("horizon_end IS NULL OR horizon_end >= start_date", name="horizon_end_not_before_start"),
        CheckConstraint(
            _DATE_IS_DATE_OR_NULL.format(column="detailed_through_date"), name="detailed_through_date_format"
        ),
        # 一个目标最多一个当前执行版本（03 第 1 节部分唯一索引）。
        Index(
            "uq_plan_versions_active_per_goal",
            "goal_id",
            unique=True,
            sqlite_where=text(f"status = {PlanVersionStatus.ACTIVE.value!r}"),
        ),
        Index("ix_plan_versions_owner_id", "owner_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    goal_id: Mapped[str] = mapped_column(String(36), ForeignKey("goals.id"))
    version_no: Mapped[int] = mapped_column(Integer)
    profile_id: Mapped[str] = mapped_column(String(36), ForeignKey("goal_profiles.id"))
    route_id: Mapped[str] = mapped_column(String(36), ForeignKey("routes.id"))
    based_on_version_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("plan_versions.id"))
    status: Mapped[str] = mapped_column(String(16))
    start_date: Mapped[str] = mapped_column(String(10))
    # 达成型非空且展开不得越过；维持型可空表示持续滚动无终点（03 第 3 节）。
    horizon_end: Mapped[str | None] = mapped_column(String(10))
    detailed_through_date: Mapped[str | None] = mapped_column(String(10))
    strategy_version: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())


class PlanPhase(Base):
    """阶段。保存完整阶段结构，随计划版本不可变（03 第 3 节）。"""

    __tablename__ = "plan_phases"
    __table_args__ = (
        UniqueConstraint("plan_version_id", "phase_key"),
        CheckConstraint("rank >= 1", name="rank"),
        CheckConstraint(_json_valid("exit_criteria_json"), name="exit_criteria_json"),
        CheckConstraint(_json_valid("duration_estimate_json"), name="duration_estimate_json"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    plan_version_id: Mapped[str] = mapped_column(String(36), ForeignKey("plan_versions.id"))
    phase_key: Mapped[str] = mapped_column(String(64))
    rank: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    outcome: Mapped[str] = mapped_column(Text)
    exit_criteria_json: Mapped[str] = mapped_column(Text)
    duration_estimate_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())


class PlanMilestone(Base):
    """可检查的阶段结果。维持型目标退化为周期性回顾点（12 号第 3 节）。"""

    __tablename__ = "plan_milestones"
    __table_args__ = (
        UniqueConstraint("plan_version_id", "milestone_key"),
        CheckConstraint("rank >= 1", name="rank"),
        CheckConstraint(_json_valid("success_criteria_json"), name="success_criteria_json"),
        CheckConstraint(_json_valid("target_window_json"), name="target_window_json"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    plan_version_id: Mapped[str] = mapped_column(String(36), ForeignKey("plan_versions.id"))
    phase_id: Mapped[str] = mapped_column(String(36), ForeignKey("plan_phases.id"))
    milestone_key: Mapped[str] = mapped_column(String(64))
    rank: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    success_criteria_json: Mapped[str] = mapped_column(Text)
    target_window_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())


class TaskBatch(Base):
    """某连续日期窗口内生成的详细任务。正常滚动不创建新计划版本（12 号第 1 节）。"""

    __tablename__ = "task_batches"
    __table_args__ = (
        CheckConstraint(_one_of("status", TaskBatchStatus), name="status"),
        CheckConstraint(_DATE_IS_DATE.format(column="window_start"), name="window_start_format"),
        CheckConstraint(_DATE_IS_DATE.format(column="window_end"), name="window_end_format"),
        CheckConstraint("window_end >= window_start", name="window_end_not_before_start"),
        # 同一计划、窗口和进度输入只产生一个有效批次（03 第 3 节）。
        Index(
            "uq_task_batches_active_per_window",
            "plan_version_id",
            "window_start",
            "input_progress_revision",
            unique=True,
            sqlite_where=text(f"status = {TaskBatchStatus.ACTIVE.value!r}"),
        ),
        Index("ix_task_batches_plan_version_id", "plan_version_id", "window_start"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    plan_version_id: Mapped[str] = mapped_column(String(36), ForeignKey("plan_versions.id"))
    window_start: Mapped[str] = mapped_column(String(10))
    window_end: Mapped[str] = mapped_column(String(10))
    input_progress_revision: Mapped[int] = mapped_column(Integer)
    generation_policy_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())


class Task(Base):
    """稳定的任务身份及当前执行投影。重排和计划修订不复制完成记录（R10）。"""

    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(_one_of("execution_status", TaskExecutionStatus), name="execution_status"),
        CheckConstraint(
            "remaining_minutes_estimate IS NULL OR remaining_minutes_estimate >= 0",
            name="remaining_minutes_estimate",
        ),
        Index("ix_tasks_owner_id", "owner_id", "execution_status"),
        Index("ix_tasks_goal_id", "goal_id", "execution_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    goal_id: Mapped[str] = mapped_column(String(36), ForeignKey("goals.id"))
    execution_status: Mapped[str] = mapped_column(String(16))
    remaining_minutes_estimate: Mapped[int | None] = mapped_column(Integer)
    progress_revision: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime())


class TaskSpec(Base):
    """不可变的任务内容版本。内容更新创建新 spec_no（03 第 3 节）。"""

    __tablename__ = "task_specs"
    __table_args__ = (
        UniqueConstraint("task_id", "spec_no"),
        CheckConstraint(_one_of("executor", TaskExecutor), name="executor"),
        CheckConstraint("expected_minutes >= 0", name="expected_minutes"),
        CheckConstraint("minimum_minutes >= 0", name="minimum_minutes"),
        CheckConstraint("maximum_minutes >= 0", name="maximum_minutes"),
        # minimum <= expected <= maximum（03 第 3 节、12 号第 4 节）。
        CheckConstraint("minimum_minutes <= expected_minutes", name="minimum_not_above_expected"),
        CheckConstraint("expected_minutes <= maximum_minutes", name="expected_not_above_maximum"),
        CheckConstraint(
            "minimum_session_minutes IS NULL OR minimum_session_minutes >= 0", name="minimum_session_minutes"
        ),
        CheckConstraint(_DATE_IS_DATE_OR_NULL.format(column="earliest_date"), name="earliest_date_format"),
        CheckConstraint(_DATE_IS_DATE.format(column="latest_date"), name="latest_date_format"),
        CheckConstraint("earliest_date IS NULL OR earliest_date <= latest_date", name="earliest_not_after_latest"),
        CheckConstraint("can_split IN (0, 1)", name="can_split"),
        CheckConstraint(_json_valid("completion_criteria_json"), name="completion_criteria_json"),
        CheckConstraint(
            f"domain_payload_json IS NULL OR {_json_valid('domain_payload_json')}", name="domain_payload_json"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"))
    spec_no: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    instructions: Mapped[str] = mapped_column(Text)
    completion_criteria_json: Mapped[str] = mapped_column(Text)
    executor: Mapped[str] = mapped_column(String(16))
    expected_minutes: Mapped[int] = mapped_column(Integer)
    minimum_minutes: Mapped[int] = mapped_column(Integer)
    maximum_minutes: Mapped[int] = mapped_column(Integer)
    # 置信档位的取值随生成策略细化，先不加 CHECK。
    estimate_confidence: Mapped[str | None] = mapped_column(String(16))
    earliest_date: Mapped[str | None] = mapped_column(String(10))
    # latest_date 非空（05-module-contracts 默认值表）。
    latest_date: Mapped[str] = mapped_column(String(10))
    can_split: Mapped[bool] = mapped_column(Boolean)
    minimum_session_minutes: Mapped[int | None] = mapped_column(Integer)
    verification_policy: Mapped[str | None] = mapped_column(String(64))
    # 领域扩展事实并带结构版本（03 第 3 节；学习领域存学习单元标识与被复习单元引用）。
    domain_payload_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())


class PlanTaskMembership(Base):
    """任务在计划中的归属。任务必须属于阶段，里程碑引用可选（03 第 3 节）。"""

    __tablename__ = "plan_task_memberships"
    __table_args__ = (
        # 一个任务在一个计划版本里只归属一次。
        UniqueConstraint("plan_version_id", "task_id"),
        Index("ix_plan_task_memberships_task_id", "task_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    plan_version_id: Mapped[str] = mapped_column(String(36), ForeignKey("plan_versions.id"))
    task_batch_id: Mapped[str] = mapped_column(String(36), ForeignKey("task_batches.id"))
    phase_id: Mapped[str] = mapped_column(String(36), ForeignKey("plan_phases.id"))
    milestone_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("plan_milestones.id"))
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"))
    task_spec_id: Mapped[str] = mapped_column(String(36), ForeignKey("task_specs.id"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
