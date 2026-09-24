"""确定性多目标排期引擎（13-scheduling-engine.md 的纯函数实现）。

`calculate_agenda(snapshot) -> SchedulingResult` 是本模块唯一入口：

- **纯函数**：不读写数据库、不调用模型、不取时钟；相同快照必然得到相同结果
  （排序键带全序 tiebreaker，不用任何带状态的结构）。
- 排期只分配分钟数与顺序，不生成起止时刻（R06）；同一天同一任务最多一个分配项。
- 数值阈值来自 `contracts.policies` 的版本化常量（T05 决策 A1），不在本模块散落魔数。

候选过滤（13 号第 3 节）依赖依赖结果的部分：快照中的 `dependency_satisfied` 由
服务层按 `task_dependencies` 生效边装配（T06 回填 T05 决策 A2 的占位）。
暂停目标的任务不进入候选集也不进 deferred——它们属于"整体不参与排期"的目标，
出现在每日页会误导用户；目标恢复后 revision 递增会触发重算。
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta

from goalflow.contracts.enums import (
    AgendaRevisionStatus,
    CapacityBasis,
    SchedulingReasonCode,
)
from goalflow.contracts.policies import (
    DEFAULT_MINIMUM_SESSION_MINUTES,
    SCHEDULING_POLICY_VERSION,
    SOON_WITHIN_DAYS,
    STARVATION_AFTER_DAYS,
    URGENT_WITHIN_DAYS,
    WEEKLY_GAP_HIGH_MINUTES,
)

# —— 快照（不可变输入）——


@dataclass(frozen=True)
class GoalSchedulingInput:
    """单个活动目标的排期输入。paused 为 True 的目标整体不参与排期。"""

    goal_id: str
    paused: bool
    focus: bool
    rank: int
    weekly_demand_minutes: int
    weekly_fulfilled_minutes: int


@dataclass(frozen=True)
class TaskSchedulingInput:
    """单个任务的排期输入。分钟数一律指"还差的量"，不是原始预计量。"""

    task_id: str
    task_spec_id: str
    goal_id: str
    # 取 TaskExecutionStatus 的 pending / in_progress；其余状态由快照组装方过滤。
    execution_status: str
    remaining_minutes: int
    can_split: bool
    minimum_session_minutes: int | None
    earliest_date: str | None
    latest_date: str
    # 服务层按生效依赖边装配（T06 回填 A2 占位）；默认 True 使不含依赖边的快照保持原语义。
    dependency_satisfied: bool = True
    # locked 约束固定当前分配分钟数（13 号第 4 节）；未锁定为 None。
    locked_minutes: int | None = None
    # 连续未获安排的天数（防长期饥饿，13 号第 4 节排序键 4）。
    unscheduled_days: int = 0


@dataclass(frozen=True)
class SchedulingSnapshot:
    """排期计算的不可变输入（13 号第 1 节）。"""

    local_date: str
    timezone: str
    planning_revision: int
    capacity_basis: CapacityBasis
    # 按口径给出的当日有效额度：total 为全天额度，remaining 为声明时点起的剩余。
    daily_quota_minutes: int
    # 今日已投入。total 口径参与容量扣减；remaining 口径不重复扣（03 第 4 节）。
    spent_minutes: int
    weekly_remaining_capacity_minutes: int
    # (日期, 有效容量) 列表，覆盖今日之后若干天，用于 DEADLINE_RISK 判定。
    daily_capacity_forecast: tuple[tuple[str, int], ...]
    goals: tuple[GoalSchedulingInput, ...]
    tasks: tuple[TaskSchedulingInput, ...]
    # 当日的用户锁定（task_day_constraints 中 status=active 的行）。
    must_do_task_ids: frozenset[str] = frozenset()
    locked_task_ids: frozenset[str] = frozenset()


# —— 结果 ——


@dataclass(frozen=True)
class AgendaItemDraft:
    task_id: str
    task_spec_id: str
    rank: int
    allocated_minutes: int
    # 首版正常分配不带原因码；解释性信息在 deferred 与 conflicts 里。
    reason_codes: tuple[SchedulingReasonCode, ...] = ()


@dataclass(frozen=True)
class DeferredDraft:
    task_id: str
    reason_code: SchedulingReasonCode
    detail: str | None = None


@dataclass(frozen=True)
class ConflictDraft:
    code: SchedulingReasonCode
    task_ids: tuple[str, ...]
    message: str
    minutes_gap: int | None = None


@dataclass(frozen=True)
class ChangeProposalDraft:
    proposal_type: str
    task_ids: tuple[str, ...]
    description: str
    requires_confirmation: bool = True


@dataclass(frozen=True)
class CapacitySummaryDraft:
    """13 号第 1 节 capacity_summary：额度来源、已投入、已保留和剩余分钟。"""

    basis: CapacityBasis
    quota_minutes: int
    spent_minutes: int
    reserved_minutes: int
    remaining_minutes: int


@dataclass(frozen=True)
class SchedulingResult:
    """一次排期计算的完整结果（13 号第 1 节 SchedulingResult）。"""

    status: AgendaRevisionStatus
    scheduling_policy_version: str
    capacity: CapacitySummaryDraft
    items: tuple[AgendaItemDraft, ...]
    deferred: tuple[DeferredDraft, ...]
    conflicts: tuple[ConflictDraft, ...]
    change_proposals: tuple[ChangeProposalDraft, ...]


# —— 内部辅助 ——


def _d(iso: str) -> date:
    return date.fromisoformat(iso)


def _remaining_capacity(snapshot: SchedulingSnapshot) -> int:
    """当日剩余容量。total 扣已投入；remaining 不重复扣（03 第 4 节）。"""
    if snapshot.capacity_basis is CapacityBasis.TOTAL:
        return max(0, snapshot.daily_quota_minutes - snapshot.spent_minutes)
    return max(0, snapshot.daily_quota_minutes)


def _session_floor(task: TaskSchedulingInput) -> int:
    """可拆分任务的单次分配下限；不可拆分任务必须整体放入，没有下限概念。"""
    if task.minimum_session_minutes is not None:
        return task.minimum_session_minutes
    return DEFAULT_MINIMUM_SESSION_MINUTES


def _sort_key(
    task: TaskSchedulingInput, goal: GoalSchedulingInput, today: date, initial_remaining: int
) -> tuple[int, int, int, int, int, int, int, str, str]:
    """弹性任务的分层排序键（13 号第 4 节），全部为稳定可解释的离散区间。

    数值小的排前面。最后两位是 goal_id/task_id，保证同区间内结果全序、可复现。
    """
    days_left = (_d(task.latest_date) - today).days
    if days_left <= URGENT_WITHIN_DAYS:
        urgency = 0
    elif days_left <= SOON_WITHIN_DAYS:
        urgency = 1
    else:
        urgency = 2
    gap = goal.weekly_demand_minutes - goal.weekly_fulfilled_minutes
    if gap >= WEEKLY_GAP_HIGH_MINUTES:
        gap_tier = 0
    elif gap > 0:
        gap_tier = 1
    else:
        gap_tier = 2
    # 防饥饿（13 号第 9 节）：连续未安排天数达到阈值后，在本紧迫/缺口层级内获得
    # 更高顺序——低优先级目标也能被提升，但绝不挤掉必须项（必须项已先行分配）。
    starving = 0 if task.unscheduled_days >= STARVATION_AFTER_DAYS else 1
    # 排序键 5：能否适配初始剩余容量——小任务不被放不进的大任务挡住。
    fit = 0 if task.remaining_minutes <= initial_remaining else 1
    return (
        urgency,
        gap_tier,
        starving,
        0 if goal.focus else 1,
        goal.rank,
        -task.unscheduled_days,
        fit,
        goal.goal_id,
        task.task_id,
    )


def _forecast_capacity_until(snapshot: SchedulingSnapshot, latest: date) -> int:
    """今日之后到 latest（含）的容量合计；forecast 未覆盖的日期不计容量。"""
    start = _d(snapshot.local_date) + timedelta(days=1)
    wanted = {day.isoformat() for day in _daterange(start, latest)}
    return sum(minutes for day, minutes in snapshot.daily_capacity_forecast if day in wanted)


def _daterange(start: date, end: date) -> Iterator[date]:
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


# —— 主入口 ——


def calculate_agenda(snapshot: SchedulingSnapshot) -> SchedulingResult:
    """确定性排期：容量 → 候选过滤 → 必须项 → 分层排序 → 装箱拆分 → 冲突输出。"""
    today = _d(snapshot.local_date)
    goal_by_id = {goal.goal_id: goal for goal in snapshot.goals}
    remaining_start = _remaining_capacity(snapshot)
    conflicts: list[ConflictDraft] = []
    proposals: list[ChangeProposalDraft] = []

    active_tasks = [task for task in snapshot.tasks if not goal_by_id[task.goal_id].paused]

    # —— 必须项集合（13 号第 4 节）——
    mandatory: list[tuple[TaskSchedulingInput, int]] = []
    deferred: list[DeferredDraft] = []
    flexible: list[TaskSchedulingInput] = []
    deadline_breach: list[TaskSchedulingInput] = []

    for task in active_tasks:
        if task.execution_status == "in_progress":
            mandatory.append((task, task.remaining_minutes))
            continue
        if task.execution_status != "pending":
            continue  # proposed/completed/cancelled 不参与排期
        if task.task_id in snapshot.must_do_task_ids or task.task_id in snapshot.locked_task_ids:
            minutes = task.locked_minutes if task.locked_minutes is not None else task.remaining_minutes
            mandatory.append((task, minutes))
            continue
        if task.latest_date == snapshot.local_date:
            mandatory.append((task, task.remaining_minutes))
            continue
        if _d(task.latest_date) < today:
            # 超过 latest_date 的任务进入冲突或变更建议，不静默移动（13 号第 3 节）。
            deadline_breach.append(task)
            continue
        if task.earliest_date is not None and _d(task.earliest_date) > today:
            continue  # 未到开始日，尚与今天无关
        if not task.dependency_satisfied:
            deferred.append(DeferredDraft(task.task_id, SchedulingReasonCode.DEPENDENCY_BLOCKED, "前置任务尚未完成"))
            continue
        flexible.append(task)

    mandatory_total = sum(minutes for _, minutes in mandatory if minutes > 0)

    # 必须项超过容量：全部冲突项保留供用户选择，不按隐藏分数删除（13 号第 4 节）。
    if mandatory_total > remaining_start:
        conflicts.append(
            ConflictDraft(
                code=SchedulingReasonCode.MANDATORY_CAPACITY_CONFLICT,
                task_ids=tuple(task.task_id for task, _ in mandatory),
                message="必须进行的事项超过了今天的可用时间，请选择保留哪些",
                minutes_gap=mandatory_total - remaining_start,
            )
        )
        flexible_deferred = tuple(
            DeferredDraft(task.task_id, SchedulingReasonCode.MANDATORY_CAPACITY_CONFLICT) for task in flexible
        )
        return SchedulingResult(
            status=AgendaRevisionStatus.CONFLICTED,
            scheduling_policy_version=SCHEDULING_POLICY_VERSION,
            capacity=CapacitySummaryDraft(
                basis=snapshot.capacity_basis,
                quota_minutes=snapshot.daily_quota_minutes,
                spent_minutes=snapshot.spent_minutes,
                reserved_minutes=0,
                remaining_minutes=remaining_start,
            ),
            items=(),
            deferred=flexible_deferred,
            conflicts=tuple(conflicts),
            change_proposals=(),
        )

    # —— 必须项先行分配 ——
    items: list[AgendaItemDraft] = []
    remaining = remaining_start
    rank = 1
    for task, minutes in sorted(mandatory, key=lambda pair: (-pair[1], pair[0].task_id)):
        minutes = min(minutes, remaining)
        if minutes <= 0:
            break
        items.append(
            AgendaItemDraft(
                task_id=task.task_id,
                task_spec_id=task.task_spec_id,
                rank=rank,
                allocated_minutes=minutes,
            )
        )
        rank += 1
        remaining -= minutes

    # —— 弹性任务：分层排序后装箱（13 号第 4、5 节）——
    ordered = sorted(
        flexible,
        key=lambda task: _sort_key(task, goal_by_id[task.goal_id], today, remaining_start),
    )
    for task in ordered:
        if remaining <= 0:
            deferred.append(
                DeferredDraft(task.task_id, SchedulingReasonCode.MINIMUM_SESSION_UNFIT, "今日剩余容量已用完")
            )
            continue
        need = task.remaining_minutes
        if not task.can_split:
            if need <= remaining:
                allocate = need
            else:
                deferred.append(
                    DeferredDraft(
                        task.task_id,
                        SchedulingReasonCode.MINIMUM_SESSION_UNFIT,
                        f"任务需一次投入 {need} 分钟，今日剩余 {remaining} 分钟",
                    )
                )
                continue
        else:
            floor = _session_floor(task)
            allocate = min(need, remaining)
            if allocate < floor:
                deferred.append(
                    DeferredDraft(
                        task.task_id,
                        SchedulingReasonCode.MINIMUM_SESSION_UNFIT,
                        f"剩余容量不足以安排一次有意义的投入（至少 {floor} 分钟）",
                    )
                )
                continue
        items.append(
            AgendaItemDraft(
                task_id=task.task_id,
                task_spec_id=task.task_spec_id,
                rank=rank,
                allocated_minutes=allocate,
            )
        )
        rank += 1
        remaining -= allocate

    # —— 周容量冲突（13 号第 6 节）——
    weekly_gap = sum(
        goal.weekly_demand_minutes - goal.weekly_fulfilled_minutes for goal in snapshot.goals if not goal.paused
    )
    if weekly_gap > snapshot.weekly_remaining_capacity_minutes:
        unscheduled = [task.task_id for task in ordered if task.task_id not in {i.task_id for i in items}]
        conflicts.append(
            ConflictDraft(
                code=SchedulingReasonCode.WEEKLY_CAPACITY_CONFLICT,
                task_ids=tuple(unscheduled),
                message="本周的活动计划需求超过了剩余的周容量",
                minutes_gap=weekly_gap - snapshot.weekly_remaining_capacity_minutes,
            )
        )

    # —— 期限风险：已过期的任务 + 按容量推算无法按期完成的任务（13 号第 3、6 节）——
    for task in deadline_breach:
        conflicts.append(
            ConflictDraft(
                code=SchedulingReasonCode.DEADLINE_RISK,
                task_ids=(task.task_id,),
                message=f"任务已超过最晚日期 {task.latest_date}，需要确认如何处理",
            )
        )
        proposals.append(
            ChangeProposalDraft(
                proposal_type="deadline_extension",
                task_ids=(task.task_id,),
                description=f"任务已超过最晚日期 {task.latest_date}，建议顺延期限或调整投入",
            )
        )
    # 今日已分配的部分抵扣剩余工作量：可拆分任务今天做了一部分，剩下的从明天起算。
    allocated_by_task: dict[str, int] = {}
    for item in items:
        allocated_by_task[item.task_id] = allocated_by_task.get(item.task_id, 0) + item.allocated_minutes
    for task in ordered:
        remaining_work = task.remaining_minutes - allocated_by_task.get(task.task_id, 0)
        if remaining_work <= 0:
            continue
        forecast_capacity = _forecast_capacity_until(snapshot, _d(task.latest_date))
        if forecast_capacity < remaining_work:
            conflicts.append(
                ConflictDraft(
                    code=SchedulingReasonCode.DEADLINE_RISK,
                    task_ids=(task.task_id,),
                    message=f"按当前容量推算，任务无法在 {task.latest_date} 前完成",
                    minutes_gap=remaining_work - forecast_capacity,
                )
            )
            proposals.append(
                ChangeProposalDraft(
                    proposal_type="deadline_extension",
                    task_ids=(task.task_id,),
                    description=f"任务预计无法在 {task.latest_date} 前完成，建议顺延期限或增加投入",
                )
            )

    status = AgendaRevisionStatus.CONFLICTED if conflicts else AgendaRevisionStatus.READY
    return SchedulingResult(
        status=status,
        scheduling_policy_version=SCHEDULING_POLICY_VERSION,
        capacity=CapacitySummaryDraft(
            basis=snapshot.capacity_basis,
            quota_minutes=snapshot.daily_quota_minutes,
            spent_minutes=snapshot.spent_minutes,
            reserved_minutes=sum(item.allocated_minutes for item in items),
            remaining_minutes=remaining,
        ),
        items=tuple(items),
        deferred=tuple(deferred),
        conflicts=tuple(conflicts),
        change_proposals=tuple(proposals),
    )
