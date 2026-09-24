"""共享枚举。在此单点定义，新增或改名属于公共契约变更。

见 docs/engineering/01-contracts-and-ownership.md 第 2 节。
"""

from enum import StrEnum


class UserRole(StrEnum):
    """账号角色。首位注册者不自动成为管理员，只能由部署者本地命令授予。"""

    USER = "user"
    ADMIN = "admin"


class UserStatus(StrEnum):
    """账号状态。disabled 的账号不能登录，已有会话在禁用时全部撤销。"""

    ACTIVE = "active"
    DISABLED = "disabled"


class JobStatus(StrEnum):
    """后台作业状态（T07 决策 E9）。

    queued → running → succeeded / failed / cancelled / stale；可重试错误经 retry_wait 回到 queued。
    后四个是终态，进入后不再变化。等待用户确认属于业务会话状态，不是作业状态。
    """

    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALE = "stale"


TERMINAL_JOB_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.STALE}
)


class JobEventType(StrEnum):
    """作业持久化事件（T07 决策 E19），SSE 按 sequence 补读。

    completed、failed、cancelled、stale 是终态事件，订阅方收到后即可关闭连接。
    """

    QUEUED = "queued"
    STARTED = "started"
    STAGE_COMPLETED = "stage_completed"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALE = "stale"


# —— 目标与计划（T04 契约 PR；取值出处见各 docstring）——


class GoalDomain(StrEnum):
    """Agent 的内部策略路由，不是用户必填标签（03 第 2 节）。

    推断不足时使用 general。取值见 16-domain-policy-design.md 第 2 节。
    """

    GENERAL = "general"
    LEARNING = "learning"
    FITNESS = "fitness"


class GoalKind(StrEnum):
    """目标形态，由档案的时间边界推导（PRD D12 已确认）。

    completed 状态仅对 achievement 开放；maintenance 永不进入 completed。
    """

    ACHIEVEMENT = "achievement"
    MAINTENANCE = "maintenance"


class GoalStatus(StrEnum):
    """目标状态机（PRD D12；17-goal-lifecycle-design.md 第 2 节）。

    draft → active ↔ paused；completed / stopped 是终态，不可逆，
    仅结束操作后 24 小时内可经 undo_closure 回到结束前状态。
    """

    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    STOPPED = "stopped"


TERMINAL_GOAL_STATUSES: frozenset[GoalStatus] = frozenset({GoalStatus.COMPLETED, GoalStatus.STOPPED})


class ClosureKind(StrEnum):
    """结束方式。completed 仅对 kind=achievement 开放（17 号第 2 节）。"""

    COMPLETED = "completed"
    STOPPED = "stopped"


class ReviewPeriod(StrEnum):
    """维持型目标的周期回顾节奏（12-goal-lifecycle.md 第 4 节，默认 weekly）。"""

    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"


class DependencyCheckStatus(StrEnum):
    """暂停响应中跨目标依赖影响的检查标记（T04 决策 A12）。

    not_wired 表示依赖校验尚未接入（T06 建 task_dependencies 前），此时影响列表为空
    仅因"未检查"，不代表"无影响"。
    """

    WIRED = "wired"
    NOT_WIRED = "not_wired"


class ProfileDraftReadiness(StrEnum):
    """档案草稿的就绪状态（03 第 2 节）。"""

    NEEDS_INPUT = "needs_input"
    REVIEW_READY = "review_ready"
    BLOCKED = "blocked"


class RouteSetStatus(StrEnum):
    """路线集合状态。新集合不覆盖旧集合；输入 revision 变化使晚返回结果 stale（11 号第 3、6 节）。"""

    CURRENT = "current"
    SUPERSEDED = "superseded"
    STALE = "stale"
    FAILED = "failed"


class RouteStatus(StrEnum):
    """单条路线状态。微调创建 based_on_route_id 指向原路线的新变体（11 号第 6 节）。"""

    CURRENT = "current"
    SUPERSEDED = "superseded"


class PlanVersionStatus(StrEnum):
    """计划版本状态（03 第 3 节）。战略结构不可变；正常滚动展开不创建新版本。"""

    DRAFT = "draft"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DISCARDED = "discarded"


class TaskBatchStatus(StrEnum):
    """任务批次状态。同一计划、窗口和进度输入只有一个有效批次；过期或失败批次保留记录但不进入排期（12 号第 3 节）。"""

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    FAILED = "failed"


class TaskExecutionStatus(StrEnum):
    """任务执行状态（03 第 3 节）。

    blocked 是排期计算得到的展示态，不落库；paused 目标下的任务不改写执行状态（D12）。
    """

    PROPOSED = "proposed"
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskExecutor(StrEnum):
    """任务的执行者类型（12 号第 4 节：用户任务、Agent 任务和协作任务分别计算用户投入）。"""

    USER = "user"
    AGENT = "agent"
    COLLABORATIVE = "collaborative"


# —— 时间预算与排期（T05 契约 PR；取值出处见各 docstring）——


class SchedulingReasonCode(StrEnum):
    """排期原因码（13-scheduling-engine.md 第 6 节）。

    同时用于 agenda_items 的安排原因、deferred_tasks 的延期原因和 conflicts 的冲突类型；
    具体语义随所在集合不同，字段描述里分别说明。
    """

    MANDATORY_CAPACITY_CONFLICT = "MANDATORY_CAPACITY_CONFLICT"
    WEEKLY_CAPACITY_CONFLICT = "WEEKLY_CAPACITY_CONFLICT"
    DEADLINE_RISK = "DEADLINE_RISK"
    DEPENDENCY_BLOCKED = "DEPENDENCY_BLOCKED"
    MINIMUM_SESSION_UNFIT = "MINIMUM_SESSION_UNFIT"
    CROSS_GOAL_IMPACT = "CROSS_GOAL_IMPACT"


class TaskDayConstraintKind(StrEnum):
    """单日任务约束（03 第 4 节 task_day_constraints.constraint_kind）。

    must_do_today 固定日期；locked 还固定当前分配分钟数（13 号第 4 节）。
    """

    MUST_DO_TODAY = "must_do_today"
    LOCKED = "locked"


class TaskDayConstraintStatus(StrEnum):
    """单日任务约束的生效状态（03 第 4 节 status 列，取值未在文档固定，T05 决策 A8）。

    清除约束保留行为 cleared 行、不删行，历史可解释（Q08）。
    """

    ACTIVE = "active"
    CLEARED = "cleared"


class DailyOverrideKind(StrEnum):
    """当天额度口径（03 第 4 节 daily_overrides.override_kind）。

    total 是"今天共有多少时间"，remaining 是"从声明时点起还剩多少时间"；
    两种口径不可混用，剩余容量计算方式不同（03 第 4 节）。
    """

    TOTAL = "total"
    REMAINING = "remaining"


class AgendaRevisionStatus(StrEnum):
    """单次排期结果状态（03 第 4 节 agenda_revisions.status）。"""

    READY = "ready"
    CONFLICTED = "conflicted"


class GoalFocusStatus(StrEnum):
    """目标调度偏好中的 focus 状态（03 第 4 节 focus_status 列，取值未在文档固定，T05 决策 A7）。

    focused 只提升弹性任务排序权重，不越过硬期限与进行中任务（13 号第 9 节）。
    """

    FOCUSED = "focused"
    NORMAL = "normal"


class CapacityBasis(StrEnum):
    """当日容量的额度口径（13 号第 1 节 capacity_summary 的"额度来源"，取值为 T05 决策 A9）。

    total 按"全天额度 − 已投入"计剩余；remaining 从用户声明时点起扣除，
    不重复扣减已投入时间（03 第 4 节）。
    """

    TOTAL = "total"
    REMAINING = "remaining"


# —— 目标关联与任务依赖（T06 契约 PR；取值出处见各 docstring）——


class GoalLinkStatus(StrEnum):
    """目标关联状态（03 第 2 节；18-goal-link-design.md 第 1 节）。

    没有 inactive——软失效会产生产品规则禁止的"关联已解除但依赖边仍在"的中间状态。
    removed 行保留归档，解除后同一目标对可重新建立（T06 决策 A2）。
    """

    PROPOSED = "proposed"
    ACTIVE = "active"
    REMOVED = "removed"


class DependencyOutcome(StrEnum):
    """任务依赖的满足条件（03 第 3 节 required_outcome；产品 13 号第 3 节）。

    区分"执行完成"与"验证通过"；健身领域不允许 verification_passed 是应用层
    策略谓词 fitness_dependency_outcome（16 号第 2 节），不在 DB CHECK 里。
    """

    EXECUTION_COMPLETED = "execution_completed"
    VERIFICATION_PASSED = "verification_passed"


class ChangeProposalStatus(StrEnum):
    """变更提案状态（03 第 3 节 change_proposals.status）。

    接受和应用在同一事务完成；提案基础版本已变时置 stale。
    """

    PENDING = "pending"
    APPLIED = "applied"
    REJECTED = "rejected"
    STALE = "stale"


class ChangeClass(StrEnum):
    """变更提案的判级（03 第 3 节 change_class，取值出处 18 号第 3 节、D04）。

    本期只含目标关联解除所需的两级；record_only / auto_schedule / auto_plan_local
    归 T12，届时随其契约 PR 扩展 CHECK（T06 决策 A5）。
    """

    CONFIRMATION_REQUIRED = "confirmation_required"
    PROFILE_REVISION_REQUIRED = "profile_revision_required"
