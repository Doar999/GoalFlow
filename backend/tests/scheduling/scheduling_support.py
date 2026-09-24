"""排期测试的领域数据辅助：直接落最小合法行，绕过 goals 模块的完整流程。

与 tests/goals/goals_support.py 分开的理由同 conftest：测试目录没有 `__init__.py`，
跨目录 import 不可靠。这里只造"排期视角"需要的最小数据（活动目标 + 任务规格），
不驱动档案/计划/启用流程。
"""

import uuid
from datetime import UTC, datetime, timedelta

from goalflow.db.session import Database
from goalflow.goals.models import Goal, Task, TaskSpec
from goalflow.scheduling.models import AvailabilityVersion


def _now() -> datetime:
    return datetime.now(UTC)


def insert_goal(
    database: Database,
    owner_id: str,
    *,
    goal_id: str | None = None,
    status: str = "active",
    kind: str = "achievement",
) -> str:
    goal_id = goal_id or str(uuid.uuid4())
    now = _now()
    with database.write() as session:
        session.add(
            Goal(
                id=goal_id,
                owner_id=owner_id,
                title="测试目标",
                domain="general",
                kind=kind,
                status=status,
                review_period="weekly",
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
    return goal_id


def insert_task(
    database: Database,
    owner_id: str,
    goal_id: str,
    *,
    task_id: str | None = None,
    execution_status: str = "pending",
    spec_no: int = 1,
    expected_minutes: int = 60,
    can_split: bool = True,
    minimum_session_minutes: int | None = None,
    earliest_date: str | None = None,
    latest_date: str,
    remaining_minutes_estimate: int | None = None,
) -> tuple[str, str]:
    """落一个任务及其规格，返回 (task_id, spec_id)。"""
    task_id = task_id or str(uuid.uuid4())
    spec_id = str(uuid.uuid4())
    now = _now()
    with database.write() as session:
        session.add(
            Task(
                id=task_id,
                owner_id=owner_id,
                goal_id=goal_id,
                execution_status=execution_status,
                remaining_minutes_estimate=remaining_minutes_estimate,
                progress_revision=0,
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            TaskSpec(
                id=spec_id,
                owner_id=owner_id,
                task_id=task_id,
                spec_no=spec_no,
                title="测试任务",
                instructions="",
                completion_criteria_json="{}",
                executor="user",
                expected_minutes=expected_minutes,
                minimum_minutes=0,
                maximum_minutes=max(expected_minutes, 1),
                earliest_date=earliest_date,
                latest_date=latest_date,
                can_split=can_split,
                minimum_session_minutes=minimum_session_minutes,
                created_at=now,
            )
        )
    return task_id, spec_id


def insert_availability(
    database: Database,
    owner_id: str,
    *,
    effective_from: str,
    weekly_minutes: dict[str, int],
    version_no: int = 1,
) -> str:
    availability_id = str(uuid.uuid4())
    with database.write() as session:
        session.add(
            AvailabilityVersion(
                id=availability_id,
                owner_id=owner_id,
                effective_from=effective_from,
                weekly_minutes_json=_dump_weekly(weekly_minutes),
                version_no=version_no,
                created_at=_now(),
            )
        )
    return availability_id


def set_planning_revision(database: Database, owner_id: str, revision: int) -> None:
    from goalflow.scheduling.models import UserPlanningState

    with database.write() as session:
        state = session.get(UserPlanningState, owner_id)
        if state is None:
            now = _now()
            session.add(UserPlanningState(owner_id=owner_id, revision=revision, created_at=now, updated_at=now))
        else:
            state.revision = revision
            state.updated_at = _now()


def _dump_weekly(weekly: dict[str, int]) -> str:
    import json

    keys = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    return json.dumps({key: int(weekly.get(key, 0)) for key in keys})


def week_dates_from(iso_day: str) -> tuple[str, str]:
    """包含 iso_day 的那一周的周一与周日（ISO 日期）。"""
    day = datetime.fromisoformat(iso_day).date()
    monday = day - timedelta(days=day.weekday())
    return monday.isoformat(), (monday + timedelta(days=6)).isoformat()
