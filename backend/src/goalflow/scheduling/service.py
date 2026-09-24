"""排期模块的应用服务：快照组装、短写事务持久化与各命令。

事务纪律（T16 / 13 号第 7 节）：

- **计算在事务外，落库在短写事务里**。先在 `read()` 中组装 `SchedulingSnapshot`，
  调 `engine.calculate_agenda`，再进 `write()` 重查 planning revision——输入已变化
  就放弃重算（作业路径转 stale），未变化才创建新的 agenda revision。
- 写事务里不做业务计算、不调模型、不发 HTTP。

周需求与投入的 v1 定义（13 号第 2 节在执行记录（T11）落地前的最小可用口径）：

- `weekly_demand_minutes`：目标名下 pending 任务的最新规格 expected_minutes 之和
  （限最晚日期不晚于本周日）+ in_progress 任务的剩余投入。
- `weekly_fulfilled_minutes`：本周（周一至周日）当前生效 agenda revision 中
  分配给该目标的分钟数之和；缺失实际耗时不计入（unknown 保持 unknown，不当零投入，
  但也不重复扣）。
- 今日已投入（`spent_minutes`）来自执行记录，T11 交付前恒为 0——总额口径下这会
  高估剩余容量，属于已知限制，随 T11 接入修正。
"""

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from goalflow.auth.service import CurrentUser
from goalflow.contracts.enums import (
    CapacityBasis,
    DailyOverrideKind,
    GoalFocusStatus,
    GoalStatus,
    TaskDayConstraintKind,
    TaskDayConstraintStatus,
    TaskExecutionStatus,
)
from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.db.session import Database
from goalflow.goals.models import Goal, Task, TaskSpec
from goalflow.goals.service import _uuid
from goalflow.scheduling.engine import (
    GoalSchedulingInput,
    SchedulingResult,
    SchedulingSnapshot,
    TaskSchedulingInput,
    calculate_agenda,
)
from goalflow.scheduling.models import (
    AgendaItem,
    AgendaRevision,
    AvailabilityVersion,
    DailyAgenda,
    DailyOverride,
    GoalSchedulingPreference,
    TaskDayConstraint,
    UserPlanningState,
)

_WEEKDAY_KEYS: Final[tuple[str, ...]] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

_TZ_UTC: Final = UTC


def _today_iso() -> str:
    return datetime.now(tz=_TZ_UTC).date().isoformat()


def _now_utc() -> datetime:
    return datetime.now(tz=_TZ_UTC)


def _week_start(day: date) -> date:
    """本周周一（13 号第 2 节周容量以周一至周日为界）。"""
    return day - timedelta(days=day.weekday())


def _load_weekly_minutes(raw: str) -> dict[str, int]:
    data = json.loads(raw)
    return {key: int(data.get(key, 0)) for key in _WEEKDAY_KEYS}


def _day_quota(weekly: dict[str, int], day: date) -> int:
    return weekly[_WEEKDAY_KEYS[day.weekday()]]


# —— 读取辅助（各命令共用）——


def _get_or_create_planning_state(session: Session, owner_id: str) -> UserPlanningState:
    state = session.scalar(select(UserPlanningState).where(UserPlanningState.owner_id == owner_id))
    if state is None:
        now = _now_utc()
        state = UserPlanningState(owner_id=owner_id, revision=0, created_at=now, updated_at=now)
        session.add(state)
        session.flush()
    return state


def planning_revision(database: Database, owner_id: str) -> int:
    """当前 planning revision；无状态行视为 0。"""
    with database.read() as session:
        state = session.scalar(select(UserPlanningState).where(UserPlanningState.owner_id == owner_id))
        return state.revision if state is not None else 0


def _require_planning_revision(session: Session, owner_id: str, expected_revision: int) -> UserPlanningState:
    state = _get_or_create_planning_state(session, owner_id)
    if state.revision != expected_revision:
        raise GoalflowError(ErrorCode.REVISION_CONFLICT, "排期输入已变化，请刷新后重试")
    return state


def _bump_planning_revision(state: UserPlanningState, now: datetime) -> int:
    state.revision += 1
    state.updated_at = now
    return state.revision


def _latest_availability(session: Session, owner_id: str) -> AvailabilityVersion | None:
    return session.scalar(
        select(AvailabilityVersion)
        .where(AvailabilityVersion.owner_id == owner_id)
        .order_by(AvailabilityVersion.effective_from.desc(), AvailabilityVersion.version_no.desc())
        .limit(1)
    )


def _effective_capacity_for(session: Session, owner_id: str, day: date) -> tuple[CapacityBasis, int]:
    """某天的有效额度与口径：当日 override 优先，否则取周额度的当日值。"""
    weekly = _latest_availability(session, owner_id)
    default_minutes = _day_quota(_load_weekly_minutes(weekly.weekly_minutes_json), day) if weekly else 0
    override = session.scalar(
        select(DailyOverride).where(DailyOverride.owner_id == owner_id, DailyOverride.local_date == day.isoformat())
    )
    if override is None:
        return CapacityBasis.TOTAL, default_minutes
    kind = DailyOverrideKind(override.override_kind)
    if kind is DailyOverrideKind.TOTAL:
        return CapacityBasis.TOTAL, override.minutes
    return CapacityBasis.REMAINING, override.minutes


# —— 快照组装（只读）——


def build_snapshot(database: Database, owner_id: str, timezone: str, local_date: str) -> SchedulingSnapshot:
    """在只读事务里组装排期快照；组装期间的数据一致性由读事务快照保证（T01 D2）。"""
    with database.read() as session:
        revision = planning_revision(database, owner_id)
        today = date.fromisoformat(local_date)
        week_start = _week_start(today)
        week_end = week_start + timedelta(days=6)

        goals = session.scalars(
            select(Goal).where(Goal.owner_id == owner_id, Goal.status == GoalStatus.ACTIVE.value)
        ).all()
        prefs = {
            pref.goal_id: pref
            for pref in session.scalars(
                select(GoalSchedulingPreference).where(GoalSchedulingPreference.owner_id == owner_id)
            )
        }

        goal_inputs: list[GoalSchedulingInput] = []
        demand_by_goal: dict[str, int] = {goal.id: 0 for goal in goals}
        for goal in goals:
            pref = prefs.get(goal.id)
            goal_inputs.append(
                GoalSchedulingInput(
                    goal_id=goal.id,
                    paused=False,
                    focus=(pref.focus_status == GoalFocusStatus.FOCUSED.value) if pref else False,
                    rank=pref.rank if pref else len(goals),
                    weekly_demand_minutes=0,
                    weekly_fulfilled_minutes=0,
                )
            )

        # 任务与其最新规格。
        rows = session.execute(
            select(Task, TaskSpec)
            .join(TaskSpec, TaskSpec.task_id == Task.id)
            .where(
                Task.owner_id == owner_id,
                Task.execution_status.in_([TaskExecutionStatus.PENDING.value, TaskExecutionStatus.IN_PROGRESS.value]),
            )
        ).all()
        task_rows = _latest_spec_pairs(rows)

        # 本周生效的 agenda item：投入满足与连续未安排天数都要用。
        agenda_rows = session.execute(
            select(DailyAgenda, AgendaRevision)
            .join(AgendaRevision, AgendaRevision.id == DailyAgenda.current_revision_id)
            .where(
                DailyAgenda.owner_id == owner_id,
                DailyAgenda.local_date >= week_start.isoformat(),
                DailyAgenda.local_date <= today.isoformat(),
            )
        ).all()
        current_revision_by_date: dict[str, str] = {row.local_date: rev.id for row, rev in agenda_rows}
        item_minutes_by_task: dict[str, int] = {}
        item_goal_by_task: dict[str, str] = {}
        item_task_ids_by_date: dict[str, set[str]] = {}
        if agenda_rows:
            revision_ids = [rev.id for _, rev in agenda_rows]
            items = session.execute(
                select(AgendaItem, Task.goal_id)
                .join(Task, Task.id == AgendaItem.task_id)
                .where(AgendaItem.agenda_revision_id.in_(revision_ids))
            ).all()
            for item, goal_id in items:
                item_minutes_by_task[item.task_id] = item_minutes_by_task.get(item.task_id, 0) + item.allocated_minutes
                item_goal_by_task[item.task_id] = goal_id
                item_task_ids_by_date.setdefault(
                    next(rev.local_date for row, rev in agenda_rows if rev.id == item.agenda_revision_id), set()
                ).add(item.task_id)

        task_inputs: list[TaskSchedulingInput] = []
        for task, spec in task_rows:
            if task.goal_id not in demand_by_goal:
                continue  # 非活动目标的任务不参与
            if task.execution_status == TaskExecutionStatus.IN_PROGRESS.value:
                remaining = (
                    task.remaining_minutes_estimate
                    if task.remaining_minutes_estimate is not None
                    else spec.expected_minutes
                )
                demand_by_goal[task.goal_id] += remaining
            else:
                remaining = spec.expected_minutes
                if spec.latest_date <= week_end.isoformat():
                    demand_by_goal[task.goal_id] += spec.expected_minutes
            locked_minutes: int | None = None
            if task.id in _active_constraint_ids(session, owner_id, today, TaskDayConstraintKind.LOCKED):
                locked_minutes = item_minutes_by_task.get(task.id, remaining)
            unscheduled_days = _unscheduled_days(
                week_start, today, current_revision_by_date, item_task_ids_by_date, task.id
            )
            task_inputs.append(
                TaskSchedulingInput(
                    task_id=task.id,
                    task_spec_id=spec.id,
                    goal_id=task.goal_id,
                    execution_status=task.execution_status,
                    remaining_minutes=remaining,
                    can_split=bool(spec.can_split),
                    minimum_session_minutes=spec.minimum_session_minutes,
                    earliest_date=spec.earliest_date,
                    latest_date=spec.latest_date,
                    locked_minutes=locked_minutes,
                    unscheduled_days=unscheduled_days,
                )
            )

        rebuilt_goals = tuple(
            GoalSchedulingInput(
                goal_id=goal.goal_id,
                paused=goal.paused,
                focus=goal.focus,
                rank=goal.rank,
                weekly_demand_minutes=demand_by_goal[goal.goal_id],
                weekly_fulfilled_minutes=sum(
                    minutes
                    for task_id, minutes in item_minutes_by_task.items()
                    if item_goal_by_task.get(task_id) == goal.goal_id
                ),
            )
            for goal in goal_inputs
        )

        basis, quota = _effective_capacity_for(session, owner_id, today)
        forecast: list[tuple[str, int]] = []
        weekly_remaining = 0
        for offset in range(14):
            day = today + timedelta(days=offset)
            _, capacity = _effective_capacity_for(session, owner_id, day)
            if offset == 0 or day <= week_end:
                weekly_remaining += capacity
            if offset > 0:
                forecast.append((day.isoformat(), capacity))

        must_do = _active_constraint_ids(session, owner_id, today, TaskDayConstraintKind.MUST_DO_TODAY)
        locked = _active_constraint_ids(session, owner_id, today, TaskDayConstraintKind.LOCKED)

        return SchedulingSnapshot(
            local_date=local_date,
            timezone=timezone,
            planning_revision=revision,
            capacity_basis=basis,
            daily_quota_minutes=quota,
            spent_minutes=0,  # 执行记录归 T11；接入前恒为 0（模块 docstring 的已知限制）。
            weekly_remaining_capacity_minutes=weekly_remaining,
            daily_capacity_forecast=tuple(forecast),
            goals=rebuilt_goals,
            tasks=tuple(task_inputs),
            must_do_task_ids=frozenset(must_do),
            locked_task_ids=frozenset(locked),
        )


def _active_constraint_ids(session: Session, owner_id: str, day: date, kind: TaskDayConstraintKind) -> set[str]:
    rows = session.scalars(
        select(TaskDayConstraint.task_id).where(
            TaskDayConstraint.owner_id == owner_id,
            TaskDayConstraint.local_date == day.isoformat(),
            TaskDayConstraint.status == TaskDayConstraintStatus.ACTIVE.value,
            TaskDayConstraint.constraint_kind == kind.value,
        )
    ).all()
    return set(rows)


def _latest_spec_pairs(rows: Sequence[Any]) -> list[tuple[Task, TaskSpec]]:
    """(task, spec) 行去重到每任务的最新规格（spec_no 最大）。

    rows 是 execute() 返回的 Row 序列；Row 在类型上不等同于裸 tuple，故取 Any。
    """
    latest: dict[str, TaskSpec] = {}
    tasks: dict[str, Task] = {}
    for task, spec in rows:
        kept = latest.get(task.id)
        if kept is None or spec.spec_no > kept.spec_no:
            latest[task.id] = spec
        tasks[task.id] = task
    return [(tasks[task_id], spec) for task_id, spec in latest.items()]


def _unscheduled_days(
    week_start: date,
    today: date,
    current_revision_by_date: dict[str, str],
    item_task_ids_by_date: dict[str, set[str]],
    task_id: str,
) -> int:
    """本周内"已有排期但没有该任务"的天数；尚无排期的日期不计。"""
    days = 0
    for offset in range((today - week_start).days):
        day = (week_start + timedelta(days=offset)).isoformat()
        revision_id = current_revision_by_date.get(day)
        if revision_id is None:
            continue
        if task_id not in item_task_ids_by_date.get(day, set()):
            days += 1
    return days


# —— 持久化（写事务内调用）——


def persist_agenda(
    session: Session,
    *,
    owner_id: str,
    local_date: str,
    timezone: str,
    input_planning_revision: int,
    result: SchedulingResult,
    now: datetime,
) -> str:
    """把一次计算结果写成新的 agenda revision，并切换 daily_agendas 的当前指针。

    旧 revision 保留可查（Q08）；同一天的并发写入由"事务内重查 planning revision +
    (agenda_id, version_no) 唯一约束"挡住（13 号第 7 节）。返回新 revision 的 id。
    """
    agenda = session.scalar(
        select(DailyAgenda).where(DailyAgenda.owner_id == owner_id, DailyAgenda.local_date == local_date)
    )
    if agenda is None:
        agenda = DailyAgenda(
            id=_uuid(),
            owner_id=owner_id,
            local_date=local_date,
            timezone_snapshot_json=json.dumps({"timezone": timezone, "local_date": local_date}),
            current_revision_id=None,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        session.add(agenda)
        session.flush()
    else:
        agenda.revision += 1
        agenda.updated_at = now

    last_version = session.scalar(
        select(AgendaRevision.version_no)
        .where(AgendaRevision.agenda_id == agenda.id)
        .order_by(AgendaRevision.version_no.desc())
        .limit(1)
    )
    revision_id = _uuid()
    session.add(
        AgendaRevision(
            id=revision_id,
            owner_id=owner_id,
            agenda_id=agenda.id,
            version_no=(last_version or 0) + 1,
            input_planning_revision=input_planning_revision,
            scheduling_policy_version=result.scheduling_policy_version,
            capacity_snapshot_json=json.dumps(
                {
                    "basis": result.capacity.basis.value,
                    "quota_minutes": result.capacity.quota_minutes,
                    "spent_minutes": result.capacity.spent_minutes,
                    "reserved_minutes": result.capacity.reserved_minutes,
                    "remaining_minutes": result.capacity.remaining_minutes,
                }
            ),
            deferred_json=json.dumps(
                [
                    {"task_id": d.task_id, "reason_code": d.reason_code.value, "detail": d.detail}
                    for d in result.deferred
                ]
            ),
            # A9：conflicts 与 change_proposals 同存本列，建议放在 change_proposals 键下。
            conflict_json=json.dumps(
                {
                    "conflicts": [
                        {
                            "code": c.code.value,
                            "task_ids": list(c.task_ids),
                            "message": c.message,
                            "minutes_gap": c.minutes_gap,
                        }
                        for c in result.conflicts
                    ],
                    "change_proposals": [
                        {
                            "proposal_type": p.proposal_type,
                            "task_ids": list(p.task_ids),
                            "description": p.description,
                            "requires_confirmation": p.requires_confirmation,
                        }
                        for p in result.change_proposals
                    ],
                }
            ),
            status=result.status.value,
            reason=None,
            created_at=now,
        )
    )
    session.flush()

    for item in result.items:
        session.add(
            AgendaItem(
                id=_uuid(),
                owner_id=owner_id,
                agenda_revision_id=revision_id,
                task_id=item.task_id,
                task_spec_id=item.task_spec_id,
                rank=item.rank,
                allocated_minutes=item.allocated_minutes,
                reason_codes_json=json.dumps([code.value for code in item.reason_codes]),
                created_at=now,
            )
        )

    agenda.current_revision_id = revision_id
    agenda.updated_at = now
    return revision_id


# —— 命令 ——


def update_preferences(
    database: Database,
    user: CurrentUser,
    entries: list[dict[str, Any]],
    *,
    expected_revision: int,
    idempotency_key: str,
) -> tuple[int, list[dict[str, Any]]]:
    """保存目标调度偏好：一次提交全部活动目标，产生新 planning revision（13 号第 8 节）。"""
    del idempotency_key  # 幂等由全局 Idempotency-Key 形状保证；本命令可整体重放
    now = _now_utc()
    with database.write() as session:
        state = _require_planning_revision(session, user.user_id, expected_revision)
        for entry in entries:
            goal = session.scalar(select(Goal).where(Goal.id == entry["goal_id"], Goal.owner_id == user.user_id))
            if goal is None:
                raise GoalflowError(ErrorCode.NOT_FOUND, "目标不存在")
            if goal.status != GoalStatus.ACTIVE.value:
                raise GoalflowError(ErrorCode.VALIDATION_FAILED, "只能为活动目标设置调度偏好")
            pref = session.scalar(
                select(GoalSchedulingPreference).where(
                    GoalSchedulingPreference.owner_id == user.user_id,
                    GoalSchedulingPreference.goal_id == goal.id,
                )
            )
            if pref is None:
                pref = GoalSchedulingPreference(
                    id=_uuid(),
                    owner_id=user.user_id,
                    goal_id=goal.id,
                    focus_status=entry["focus_status"],
                    rank=entry["rank"],
                    revision=1,
                    created_at=now,
                    updated_at=now,
                )
                session.add(pref)
            else:
                pref.focus_status = entry["focus_status"]
                pref.rank = entry["rank"]
                pref.revision += 1
                pref.updated_at = now
        new_revision = _bump_planning_revision(state, now)
        session.flush()
        rows = session.scalars(
            select(GoalSchedulingPreference).where(GoalSchedulingPreference.owner_id == user.user_id)
        ).all()
        preferences = [
            {
                "goal_id": row.goal_id,
                "focus_status": GoalFocusStatus(row.focus_status),
                "rank": row.rank,
            }
            for row in sorted(rows, key=lambda item: item.rank)
        ]
        return new_revision, preferences


def update_availability(
    database: Database,
    user: CurrentUser,
    *,
    effective_from: str,
    weekly_minutes: dict[str, int],
    expected_revision: int,
    idempotency_key: str,
) -> dict[str, Any]:
    """保存新的周额度版本：不覆盖旧版本；expected_revision 指当前版本号，无版本时为 0。"""
    del idempotency_key
    now = _now_utc()
    with database.write() as session:
        current = _latest_availability(session, user.user_id)
        current_no = current.version_no if current else 0
        if current_no != expected_revision:
            raise GoalflowError(ErrorCode.REVISION_CONFLICT, "周额度已被更新，请刷新后重试")
        normalized = {key: int(weekly_minutes.get(key, 0)) for key in _WEEKDAY_KEYS}
        version = AvailabilityVersion(
            id=_uuid(),
            owner_id=user.user_id,
            effective_from=effective_from,
            weekly_minutes_json=json.dumps(normalized),
            version_no=current_no + 1,
            created_at=now,
        )
        session.add(version)
        session.flush()
        state = _get_or_create_planning_state(session, user.user_id)
        _bump_planning_revision(state, now)
        coordination_required = session.scalar(
            select(DailyAgenda.id).where(DailyAgenda.owner_id == user.user_id, DailyAgenda.local_date >= effective_from)
        )
        return {
            "id": version.id,
            "effective_from": date.fromisoformat(version.effective_from),
            "weekly_minutes": normalized,
            "version_no": version.version_no,
            "created_at": version.created_at,
            "coordination_required": coordination_required is not None,
        }


def override_day(
    database: Database,
    user: CurrentUser,
    *,
    local_date: str,
    override_kind: DailyOverrideKind,
    minutes: int,
    expected_revision: int,
    idempotency_key: str,
) -> dict[str, Any]:
    """声明单日额度（总额或剩余）：当天唯一，重复声明递增行 revision。"""
    del idempotency_key
    now = _now_utc()
    with database.write() as session:
        row = session.scalar(
            select(DailyOverride).where(DailyOverride.owner_id == user.user_id, DailyOverride.local_date == local_date)
        )
        current_revision = row.revision if row else 0
        if current_revision != expected_revision:
            raise GoalflowError(ErrorCode.REVISION_CONFLICT, "当日额度已被更新，请刷新后重试")
        if row is None:
            row = DailyOverride(
                id=_uuid(),
                owner_id=user.user_id,
                local_date=local_date,
                override_kind=override_kind.value,
                minutes=minutes,
                measured_at=now,
                revision=1,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        else:
            row.override_kind = override_kind.value
            row.minutes = minutes
            row.measured_at = now
            row.revision += 1
            row.updated_at = now
        session.flush()
        state = _get_or_create_planning_state(session, user.user_id)
        _bump_planning_revision(state, now)
        agenda_exists = session.scalar(
            select(DailyAgenda.id).where(DailyAgenda.owner_id == user.user_id, DailyAgenda.local_date == local_date)
        )
        return {
            "local_date": date.fromisoformat(row.local_date),
            "override_kind": DailyOverrideKind(row.override_kind),
            "minutes": row.minutes,
            "measured_at": row.measured_at,
            "revision": row.revision,
            "coordination_required": agenda_exists is not None,
        }


def _mandatory_minutes_excluding(session: Session, user: CurrentUser, day: date, exclude_task_id: str) -> int:
    """当日必须项的分钟合计（不含指定任务）：用于约束保存前的容量检查。"""
    total = 0
    rows = session.execute(
        select(Task, TaskSpec)
        .join(TaskSpec, TaskSpec.task_id == Task.id)
        .join(Goal, Goal.id == Task.goal_id)
        .where(
            Task.owner_id == user.user_id,
            Goal.status == GoalStatus.ACTIVE.value,
            Task.execution_status.in_([TaskExecutionStatus.PENDING.value, TaskExecutionStatus.IN_PROGRESS.value]),
        )
    ).all()
    must_do = _active_constraint_ids(session, user.user_id, day, TaskDayConstraintKind.MUST_DO_TODAY)
    locked = _active_constraint_ids(session, user.user_id, day, TaskDayConstraintKind.LOCKED)
    for task, spec in _latest_spec_pairs(rows):
        if task.id == exclude_task_id:
            continue
        if task.execution_status == TaskExecutionStatus.IN_PROGRESS.value:
            remaining = (
                task.remaining_minutes_estimate
                if task.remaining_minutes_estimate is not None
                else spec.expected_minutes
            )
            total += remaining
        elif task.id in must_do or task.id in locked or spec.latest_date == day.isoformat():
            total += spec.expected_minutes
    return total


def constrain_task(
    database: Database,
    user: CurrentUser,
    *,
    local_date: str,
    task_id: str,
    constraint_kind: TaskDayConstraintKind | None,
    expected_revision: int,
    idempotency_key: str,
) -> dict[str, Any]:
    """设置或清除单日任务约束；设置前做必须项容量检查（05-module-contracts）。"""
    del idempotency_key
    now = _now_utc()
    day = date.fromisoformat(local_date)
    with database.write() as session:
        row = session.scalar(select(Task).where(Task.id == task_id, Task.owner_id == user.user_id))
        if row is None:
            raise GoalflowError(ErrorCode.NOT_FOUND, "任务不存在")
        constraint = session.scalar(
            select(TaskDayConstraint).where(
                TaskDayConstraint.owner_id == user.user_id,
                TaskDayConstraint.task_id == task_id,
                TaskDayConstraint.local_date == local_date,
            )
        )
        current_revision = constraint.revision if constraint else 0
        if current_revision != expected_revision:
            raise GoalflowError(ErrorCode.REVISION_CONFLICT, "约束已被更新，请刷新后重试")

        if constraint_kind is None:
            if constraint is None or constraint.status != TaskDayConstraintStatus.ACTIVE.value:
                raise GoalflowError(ErrorCode.VALIDATION_FAILED, "该任务在此日期没有生效的约束")
            constraint.status = TaskDayConstraintStatus.CLEARED.value
            constraint.revision += 1
            constraint.updated_at = now
        else:
            spec = session.scalar(
                select(TaskSpec).where(TaskSpec.task_id == task_id).order_by(TaskSpec.spec_no.desc()).limit(1)
            )
            need = spec.expected_minutes if spec else 0
            basis, quota = _effective_capacity_for(session, user.user_id, day)
            # 已投入未知（T11 前），total 口径按保守值 max(0, quota) 只看其他必须项。
            remaining = max(0, quota) if basis is CapacityBasis.TOTAL else quota
            others = _mandatory_minutes_excluding(session, user, day, task_id)
            if others + need > remaining:
                raise GoalflowError(
                    ErrorCode.BUDGET_CONFLICT,
                    "设置为必须后，当日必须事项将超过可用时间",
                    details={"minutes_gap": others + need - remaining},
                )
            if constraint is None:
                session.add(
                    TaskDayConstraint(
                        id=_uuid(),
                        owner_id=user.user_id,
                        task_id=task_id,
                        local_date=local_date,
                        constraint_kind=constraint_kind.value,
                        status=TaskDayConstraintStatus.ACTIVE.value,
                        revision=1,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                constraint.constraint_kind = constraint_kind.value
                constraint.status = TaskDayConstraintStatus.ACTIVE.value
                constraint.revision += 1
                constraint.updated_at = now

        state = _get_or_create_planning_state(session, user.user_id)
        _bump_planning_revision(state, now)
        session.flush()
        final = session.scalars(
            select(TaskDayConstraint).where(
                TaskDayConstraint.owner_id == user.user_id,
                TaskDayConstraint.task_id == task_id,
                TaskDayConstraint.local_date == local_date,
            )
        ).one()
        return {
            "task_id": task_id,
            "local_date": day,
            "constraint_kind": TaskDayConstraintKind(final.constraint_kind),
            "status": final.status,
            "revision": final.revision,
        }


def get_agenda(database: Database, user: CurrentUser, local_date: str) -> dict[str, Any]:
    """读取某日当前安排或缺失状态；读取不创建任何作业（05-module-contracts）。

    尚无排期结果时 `revision` 为 None，前端据此调用 generation 端点。
    """
    with database.read() as session:
        revision = planning_revision(database, user.user_id)
        agenda = session.scalar(
            select(DailyAgenda).where(DailyAgenda.owner_id == user.user_id, DailyAgenda.local_date == local_date)
        )
        if agenda is None or agenda.current_revision_id is None:
            return {"local_date": local_date, "planning_revision": revision, "revision": None}
        rev = session.scalar(select(AgendaRevision).where(AgendaRevision.id == agenda.current_revision_id))
        if rev is None:
            return {"local_date": local_date, "planning_revision": revision, "revision": None}
        items = session.execute(
            select(AgendaItem, Task.goal_id)
            .join(Task, Task.id == AgendaItem.task_id)
            .where(AgendaItem.agenda_revision_id == rev.id)
            .order_by(AgendaItem.rank)
        ).all()
        capacity = json.loads(rev.capacity_snapshot_json)
        conflict_payload = json.loads(rev.conflict_json)
        deferred_payload = json.loads(rev.deferred_json)
        return {
            "local_date": local_date,
            "planning_revision": revision,
            "revision": {
                "id": rev.id,
                "version_no": rev.version_no,
                "status": rev.status,
                "input_planning_revision": rev.input_planning_revision,
                "scheduling_policy_version": rev.scheduling_policy_version,
                "capacity": capacity,
                "items": [
                    {
                        "task_id": item.task_id,
                        "task_spec_id": item.task_spec_id,
                        "goal_id": goal_id,
                        "rank": item.rank,
                        "allocated_minutes": item.allocated_minutes,
                        "reason_codes": json.loads(item.reason_codes_json),
                    }
                    for item, goal_id in items
                ],
                "deferred": deferred_payload,
                "conflicts": conflict_payload.get("conflicts", []),
                "change_proposals": conflict_payload.get("change_proposals", []),
                "created_at": rev.created_at,
            },
        }


# —— 共享周预算（T04 决策 A11 的接缝真实现）——


def resolve_shared_budget(database: Database, user_id: str) -> dict[str, int] | None:
    """resume 提示层的共享周预算快照；用户没有活动目标时返回 None。

    权威层是排期计算的冲突原因码（A11）；这里的 over_by_minutes 只用于提示取舍。
    """
    with database.read() as session:
        goals = session.scalars(
            select(Goal).where(Goal.owner_id == user_id, Goal.status == GoalStatus.ACTIVE.value)
        ).all()
        if not goals:
            return None
        today = _now_utc().date()
        week_start = _week_start(today)
        capacity = 0
        for offset in range(7):
            day = week_start + timedelta(days=offset)
            _, day_capacity = _effective_capacity_for(session, user_id, day)
            capacity += day_capacity
        demand = 0
        rows = session.execute(
            select(Task, TaskSpec)
            .join(TaskSpec, TaskSpec.task_id == Task.id)
            .where(
                Task.owner_id == user_id,
                Task.execution_status.in_([TaskExecutionStatus.PENDING.value, TaskExecutionStatus.IN_PROGRESS.value]),
            )
        ).all()
        for task, spec in _latest_spec_pairs(rows):
            if task.execution_status == TaskExecutionStatus.IN_PROGRESS.value:
                demand += (
                    task.remaining_minutes_estimate
                    if task.remaining_minutes_estimate is not None
                    else spec.expected_minutes
                )
            else:
                demand += spec.expected_minutes
        return {"weekly_capacity_minutes": capacity, "total_demand_minutes": demand}


# —— 生成作业 ——


AGENDA_GENERATION_KIND: Final = "agenda_generation"


def ensure_agenda(
    database: Database,
    user: CurrentUser,
    *,
    local_date: str,
    planning_revision_hint: int | None,
    idempotency_key: str,
    publisher: Any,
) -> Any:
    """保证某日存在基于最新 planning revision 的安排（05-module-contracts ensure_agenda）。

    作业在写事务里提交（与 outbox、queued 事件同生共死，E16），事务提交后投递；
    相同 (owner, kind, 日期+revision) 的有效作业去重返回（E10）。返回 JobView。
    """
    from goalflow.idempotency import IdempotentRequest, ResultRef, compute_request_hash, run_idempotent
    from goalflow.jobs.models import Job
    from goalflow.jobs.service import dispatch_outbox, submit_job
    from goalflow.jobs.store import view_of

    now = _now_utc()
    with database.write() as session:
        state = _get_or_create_planning_state(session, user.user_id)
        # planning_revision_hint 只用于客户端对齐提示；去重键始终取服务端当前值。
        del planning_revision_hint
        current = state.revision

        def execute() -> tuple[ResultRef, int]:
            submitted = submit_job(
                session,
                owner_id=user.user_id,
                kind=AGENDA_GENERATION_KIND,
                dedupe_key=f"{local_date}:{current}",
                input_refs={"local_date": local_date},
                input_revision=current,
                now=now,
            )
            return ResultRef("job", submitted.job_id), 202

        request = IdempotentRequest(
            owner_id=user.user_id,
            operation="scheduling.ensure_agenda",
            key=idempotency_key,
            request_hash=compute_request_hash("scheduling.ensure_agenda", body=None),
        )
        outcome = run_idempotent(session, request, execute, now=now)
        job_row = session.scalars(select(Job).where(Job.id == outcome.result.id)).one()
        view = view_of(job_row)

    dispatch_outbox(database, publisher, now=now, job_ids=[outcome.result.id])
    return view


def run_agenda_generation(database: Database, owner_id: str, local_date: str, input_revision: int) -> SchedulingResult:
    """作业处理函数的执行体：事务外组装快照并计算，事务内重查 revision 后落库。

    输入已变化时抛 `InputStale`（作业转 stale，不落业务结果），由调用方转译。
    """
    from goalflow.jobs.handlers import InputStale  # 延迟导入：避免 jobs ↔ scheduling 环

    snapshot = build_snapshot(database, owner_id, _user_timezone(database, owner_id), local_date)
    result = calculate_agenda(snapshot)
    with database.write() as session:
        state = session.scalar(select(UserPlanningState).where(UserPlanningState.owner_id == owner_id))
        if state is None or state.revision != input_revision:
            raise InputStale()
        persist_agenda(
            session,
            owner_id=owner_id,
            local_date=local_date,
            timezone=_user_timezone(database, owner_id),
            input_planning_revision=input_revision,
            result=result,
            now=_now_utc(),
        )
    return result


def _user_timezone(database: Database, owner_id: str) -> str:
    from goalflow.auth.models import User

    with database.read() as session:
        value = session.scalar(select(User.timezone).where(User.id == owner_id))
        return value or "UTC"
