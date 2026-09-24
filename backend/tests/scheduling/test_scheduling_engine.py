"""排期引擎（calculate_agenda 纯函数）的验收测试。

对应交接卡第 5 节"容量与额度 / 分层排序与锁定 / 装箱与拆分"的场景。
引擎不碰数据库，这里直接构造快照。
"""

from copy import deepcopy

from goalflow.contracts.enums import AgendaRevisionStatus, CapacityBasis, SchedulingReasonCode
from goalflow.contracts.policies import SCHEDULING_POLICY_VERSION
from goalflow.scheduling.engine import (
    GoalSchedulingInput,
    SchedulingSnapshot,
    TaskSchedulingInput,
    calculate_agenda,
)

DAY = "2026-09-24"  # 周四


def make_snapshot(**overrides) -> SchedulingSnapshot:
    """基准快照：单目标、容量 240、14 天每日 240 的预报。"""
    defaults: dict = {
        "local_date": DAY,
        "timezone": "UTC",
        "planning_revision": 7,
        "capacity_basis": CapacityBasis.TOTAL,
        "daily_quota_minutes": 240,
        "spent_minutes": 0,
        "weekly_remaining_capacity_minutes": 1200,
        "daily_capacity_forecast": tuple((f"2026-09-{25 + offset:02d}", 240) for offset in range(13)),
        "goals": (
            GoalSchedulingInput(
                goal_id="g1",
                paused=False,
                focus=False,
                rank=1,
                weekly_demand_minutes=300,
                weekly_fulfilled_minutes=100,
            ),
        ),
        "tasks": (),
        "must_do_task_ids": frozenset(),
        "locked_task_ids": frozenset(),
    }
    defaults.update(overrides)
    return SchedulingSnapshot(**defaults)


def make_task(
    task_id: str,
    goal_id: str = "g1",
    *,
    remaining: int = 60,
    can_split: bool = True,
    minimum_session: int | None = None,
    latest: str = "2026-10-15",
    earliest: str | None = None,
    execution_status: str = "pending",
    locked_minutes: int | None = None,
    unscheduled_days: int = 0,
    dependency_satisfied: bool = True,
) -> TaskSchedulingInput:
    return TaskSchedulingInput(
        task_id=task_id,
        task_spec_id=f"{task_id}-spec",
        goal_id=goal_id,
        execution_status=execution_status,
        remaining_minutes=remaining,
        can_split=can_split,
        minimum_session_minutes=minimum_session,
        earliest_date=earliest,
        latest_date=latest,
        dependency_satisfied=dependency_satisfied,
        locked_minutes=locked_minutes,
        unscheduled_days=unscheduled_days,
    )


class TestCapacityAndQuota:
    def test_total_basis_deducts_spent(self):
        result = calculate_agenda(make_snapshot(spent_minutes=100))
        assert result.capacity.remaining_minutes == 140
        assert result.capacity.basis is CapacityBasis.TOTAL

    def test_remaining_basis_does_not_deduct_spent_again(self):
        """剩余额度口径从声明时点起扣，不重复扣除已投入时间（03 第 4 节）。"""
        snapshot = make_snapshot(
            capacity_basis=CapacityBasis.REMAINING,
            daily_quota_minutes=140,
            spent_minutes=100,
        )
        result = calculate_agenda(snapshot)
        assert result.capacity.remaining_minutes == 140

    def test_zero_budget_defers_everything_without_negative_capacity(self):
        """预算为零：不产生负容量、不静默丢弃，冲突/延期输出如实反映。"""
        task = make_task("t1", remaining=60)
        result = calculate_agenda(make_snapshot(daily_quota_minutes=0, tasks=(task,)))
        assert result.status is AgendaRevisionStatus.READY
        assert result.items == ()
        assert [d.task_id for d in result.deferred] == ["t1"]
        assert result.capacity.remaining_minutes == 0

    def test_three_goals_do_not_reuse_the_same_daily_capacity(self):
        """三个独立目标不能分别重复使用同一日容量（13 号第 9 节）。"""
        goals = tuple(
            GoalSchedulingInput(
                goal_id=f"g{i}",
                paused=False,
                focus=False,
                rank=i,
                weekly_demand_minutes=100,
                weekly_fulfilled_minutes=0,
            )
            for i in range(1, 4)
        )
        tasks = tuple(make_task(f"t{i}", f"g{i}", remaining=100) for i in range(1, 4))
        result = calculate_agenda(make_snapshot(goals=goals, tasks=tasks, daily_quota_minutes=240))
        total = sum(item.allocated_minutes for item in result.items)
        assert total == 240  # 一天的容量只被消耗一次
        assert len(result.items) == 3  # 可拆分任务被部分分配，而不是第三条被挤掉后再补一条


class TestMandatoryItems:
    def test_mandatory_conflict_preserves_all_items(self):
        """必须项超容量时全部出现在冲突中，不按目标顺序静默删除（13 号第 9 节）。"""
        goals = (
            GoalSchedulingInput(
                goal_id="g1", paused=False, focus=False, rank=1, weekly_demand_minutes=0, weekly_fulfilled_minutes=0
            ),
            GoalSchedulingInput(
                goal_id="g2", paused=False, focus=False, rank=2, weekly_demand_minutes=0, weekly_fulfilled_minutes=0
            ),
        )
        tasks = (
            make_task("t1", "g1", remaining=50, can_split=False),
            make_task("t2", "g2", remaining=50, can_split=False),
        )
        snapshot = make_snapshot(
            goals=goals,
            tasks=tasks,
            daily_quota_minutes=60,
            must_do_task_ids=frozenset({"t1", "t2"}),
        )
        result = calculate_agenda(snapshot)
        assert result.status is AgendaRevisionStatus.CONFLICTED
        assert result.items == ()
        conflict = result.conflicts[0]
        assert conflict.code is SchedulingReasonCode.MANDATORY_CAPACITY_CONFLICT
        assert set(conflict.task_ids) == {"t1", "t2"}
        assert conflict.minutes_gap == 40

    def test_in_progress_task_is_mandatory(self):
        task = make_task("t1", execution_status="in_progress", remaining=90)
        flexible = make_task("t2", remaining=60)
        result = calculate_agenda(make_snapshot(tasks=(task, flexible), daily_quota_minutes=120))
        assert result.items[0].task_id == "t1"
        assert result.items[0].allocated_minutes == 90

    def test_focus_cannot_jump_deadline_or_in_progress(self):
        """focus 只改变弹性任务顺序，不越过硬期限或进行中任务（13 号第 9 节）。"""
        goals = (
            GoalSchedulingInput(
                goal_id="g1", paused=False, focus=True, rank=1, weekly_demand_minutes=0, weekly_fulfilled_minutes=0
            ),
            GoalSchedulingInput(
                goal_id="g2", paused=False, focus=False, rank=2, weekly_demand_minutes=0, weekly_fulfilled_minutes=0
            ),
        )
        deadline_today = make_task("deadline", "g2", remaining=60, latest=DAY, can_split=False)
        focused = make_task("focused", "g1", remaining=60)
        result = calculate_agenda(make_snapshot(goals=goals, tasks=(focused, deadline_today)))
        assert result.items[0].task_id == "deadline"


class TestLayeredOrdering:
    def test_focus_changes_flexible_order(self):
        goals = (
            GoalSchedulingInput(
                goal_id="g1", paused=False, focus=True, rank=1, weekly_demand_minutes=100, weekly_fulfilled_minutes=0
            ),
            GoalSchedulingInput(
                goal_id="g2", paused=False, focus=False, rank=2, weekly_demand_minutes=100, weekly_fulfilled_minutes=0
            ),
        )
        t1 = make_task("from-focused", "g1", remaining=60)
        t2 = make_task("from-plain", "g2", remaining=60)
        result = calculate_agenda(make_snapshot(goals=goals, tasks=(t2, t1)))
        assert [item.task_id for item in result.items] == ["from-focused", "from-plain"]

    def test_starving_task_wins_tie_but_not_mandatory(self):
        """低优先级目标连续未安排后获得更高顺序，但不挤掉必须项（13 号第 9 节）。"""
        goals = (
            GoalSchedulingInput(
                goal_id="g1", paused=False, focus=False, rank=1, weekly_demand_minutes=100, weekly_fulfilled_minutes=0
            ),
            GoalSchedulingInput(
                goal_id="g2", paused=False, focus=False, rank=2, weekly_demand_minutes=100, weekly_fulfilled_minutes=0
            ),
        )
        fresh = make_task("fresh", "g1", remaining=40)
        starving = make_task("starving", "g2", remaining=40, unscheduled_days=5)
        result = calculate_agenda(make_snapshot(goals=goals, tasks=(fresh, starving), daily_quota_minutes=240))
        assert [item.task_id for item in result.items] == ["starving", "fresh"]
        # 但都排在必须项之后
        must = make_task("must", "g1", remaining=30, latest=DAY, can_split=False)
        result2 = calculate_agenda(make_snapshot(goals=goals, tasks=(fresh, starving, must), daily_quota_minutes=240))
        assert result2.items[0].task_id == "must"


class TestPackingAndSplitting:
    def test_splittable_task_partial_allocation(self):
        task = make_task("t1", remaining=100, can_split=True, minimum_session=15)
        result = calculate_agenda(make_snapshot(tasks=(task,), daily_quota_minutes=40))
        assert result.items[0].allocated_minutes == 40

    def test_below_minimum_session_no_meaningless_fragment(self):
        """可拆分任务低于 minimum_session_minutes 时不生成无意义片段（13 号第 9 节）。"""
        task = make_task("t1", remaining=100, can_split=True, minimum_session=30)
        filler = make_task("t0", remaining=220, can_split=True, minimum_session=15)
        result = calculate_agenda(make_snapshot(tasks=(filler, task), daily_quota_minutes=240))
        allocated = {item.task_id: item.allocated_minutes for item in result.items}
        assert "t1" not in allocated  # 剩余 20 < 30：不留片段
        assert any(
            d.task_id == "t1" and d.reason_code is SchedulingReasonCode.MINIMUM_SESSION_UNFIT for d in result.deferred
        )

    def test_unsplittable_task_needs_full_fit(self):
        task = make_task("t1", remaining=100, can_split=False)
        result = calculate_agenda(make_snapshot(tasks=(task,), daily_quota_minutes=80))
        assert result.items == ()
        assert result.deferred[0].reason_code is SchedulingReasonCode.MINIMUM_SESSION_UNFIT

    def test_one_item_per_task_per_day(self):
        task = make_task("t1", remaining=500, can_split=True, minimum_session=15)
        result = calculate_agenda(make_snapshot(tasks=(task,), daily_quota_minutes=240))
        assert len([item for item in result.items if item.task_id == "t1"]) == 1


class TestConflictsAndProposals:
    def test_overdue_task_enters_conflict_with_proposal(self):
        """超过 latest_date 的任务进入冲突或变更建议，不静默移动（13 号第 3 节）。"""
        task = make_task("late", remaining=60, latest="2026-09-20")
        result = calculate_agenda(make_snapshot(tasks=(task,)))
        assert any(c.code is SchedulingReasonCode.DEADLINE_RISK and "late" in c.task_ids for c in result.conflicts)
        assert result.change_proposals[0].proposal_type == "deadline_extension"
        assert result.change_proposals[0].requires_confirmation is True

    def test_deadline_risk_by_capacity_forecast(self):
        """按当前容量推算无法在 latest_date 前完成时输出 DEADLINE_RISK 与缺口。

        今日配 240，剩余 260 分钟只有 200 分钟的后续容量——缺口 60。
        """
        task = make_task("tight", remaining=500, latest="2026-09-27")
        result = calculate_agenda(
            make_snapshot(tasks=(task,), daily_capacity_forecast=(("2026-09-25", 100), ("2026-09-26", 100)))
        )
        risk = next(c for c in result.conflicts if c.code is SchedulingReasonCode.DEADLINE_RISK)
        assert risk.minutes_gap == 60
        assert result.change_proposals[0].proposal_type == "deadline_extension"

    def test_no_deadline_risk_when_capacity_suffices(self):
        task = make_task("loose", remaining=500, latest="2026-09-27")
        result = calculate_agenda(make_snapshot(tasks=(task,)))  # 14 天 × 240
        assert not any(c.code is SchedulingReasonCode.DEADLINE_RISK for c in result.conflicts)

    def test_dependency_blocked_defers(self):
        task = make_task("blocked", remaining=60, dependency_satisfied=False)
        result = calculate_agenda(make_snapshot(tasks=(task,)))
        assert result.items == ()
        assert result.deferred[0].reason_code is SchedulingReasonCode.DEPENDENCY_BLOCKED

    def test_weekly_capacity_conflict(self):
        """活动计划周需求超过剩余周容量时输出 WEEKLY_CAPACITY_CONFLICT。"""
        task = make_task("t1", remaining=60, can_split=False)
        result = calculate_agenda(
            make_snapshot(
                tasks=(task,),
                daily_quota_minutes=60,
                weekly_remaining_capacity_minutes=30,
            )
        )
        assert any(c.code is SchedulingReasonCode.WEEKLY_CAPACITY_CONFLICT for c in result.conflicts)


class TestDeterminism:
    def test_same_snapshot_same_result(self):
        """相同快照重复计算得到相同结果（13 号第 9 节）。"""
        goals = (
            GoalSchedulingInput(
                goal_id="g1", paused=False, focus=True, rank=1, weekly_demand_minutes=200, weekly_fulfilled_minutes=50
            ),
            GoalSchedulingInput(
                goal_id="g2", paused=False, focus=False, rank=2, weekly_demand_minutes=100, weekly_fulfilled_minutes=0
            ),
        )
        tasks = tuple(
            make_task(f"t{i}", f"g{i % 2 + 1}", remaining=60 + i * 10, unscheduled_days=i) for i in range(1, 6)
        )
        snapshot = make_snapshot(goals=goals, tasks=tasks)
        first = calculate_agenda(snapshot)
        second = calculate_agenda(deepcopy(snapshot))
        assert first == second
        assert first.scheduling_policy_version == SCHEDULING_POLICY_VERSION

    def test_paused_goal_tasks_are_excluded(self):
        goals = (
            GoalSchedulingInput(
                goal_id="g1", paused=True, focus=False, rank=1, weekly_demand_minutes=0, weekly_fulfilled_minutes=0
            ),
            GoalSchedulingInput(
                goal_id="g2", paused=False, focus=False, rank=2, weekly_demand_minutes=0, weekly_fulfilled_minutes=0
            ),
        )
        paused_task = make_task("paused", "g1", remaining=60)
        active_task = make_task("active", "g2", remaining=60)
        result = calculate_agenda(make_snapshot(goals=goals, tasks=(paused_task, active_task)))
        assert [item.task_id for item in result.items] == ["active"]
        assert all(d.task_id != "paused" for d in result.deferred)
