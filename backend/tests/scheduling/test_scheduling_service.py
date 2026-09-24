"""排期应用服务的验收测试：命令、持久化、并发重查、数据隔离与 resume 接缝。

数据库用例由迁移建出真实库文件（WAL、foreign_keys=ON、busy_timeout=5000），不使用内存库。
"""

import pytest
from scheduling_support import (
    insert_availability,
    insert_goal,
    insert_task,
    set_planning_revision,
    week_dates_from,
)
from sqlalchemy import text

from goalflow.auth.service import CurrentUser
from goalflow.contracts.enums import DailyOverrideKind, GoalFocusStatus, TaskDayConstraintKind, TaskDayConstraintStatus
from goalflow.contracts.errors import GoalflowError
from goalflow.db.session import Database
from goalflow.jobs.handlers import InputStale
from goalflow.scheduling import service
from goalflow.scheduling.models import TaskDayConstraint

TODAY = None


@pytest.fixture
def full_week_availability(database: Database, user: CurrentUser, today: str):
    """每天 240 分钟的周额度版本。"""
    return insert_availability(
        database,
        user.user_id,
        effective_from=week_dates_from(today)[0],
        weekly_minutes=dict.fromkeys(
            ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"),
            240,
        ),
    )


class TestUpdatePreferences:
    def test_saves_preferences_and_bumps_revision(self, database, user, today):
        goal_id = insert_goal(database, user.user_id)
        revision, preferences = service.update_preferences(
            database,
            user,
            [{"goal_id": goal_id, "focus_status": GoalFocusStatus.FOCUSED.value, "rank": 1}],
            expected_revision=0,
            idempotency_key="pref-1",
        )
        assert revision == 1
        assert preferences == [{"goal_id": goal_id, "focus_status": GoalFocusStatus.FOCUSED, "rank": 1}]
        assert service.planning_revision(database, user.user_id) == 1

    def test_stale_revision_is_rejected(self, database, user, today):
        goal_id = insert_goal(database, user.user_id)
        service.update_preferences(
            database,
            user,
            [{"goal_id": goal_id, "focus_status": GoalFocusStatus.NORMAL.value, "rank": 1}],
            expected_revision=0,
            idempotency_key="pref-2",
        )
        with pytest.raises(GoalflowError) as excinfo:
            service.update_preferences(
                database,
                user,
                [{"goal_id": goal_id, "focus_status": GoalFocusStatus.NORMAL.value, "rank": 2}],
                expected_revision=0,
                idempotency_key="pref-3",
            )
        assert excinfo.value.code.value == "REVISION_CONFLICT"

    def test_unknown_goal_is_not_found(self, database, user):
        with pytest.raises(GoalflowError) as excinfo:
            service.update_preferences(
                database,
                user,
                [{"goal_id": "missing", "focus_status": GoalFocusStatus.NORMAL.value, "rank": 1}],
                expected_revision=0,
                idempotency_key="pref-4",
            )
        assert excinfo.value.code.value == "NOT_FOUND"


class TestAvailabilityAndOverride:
    def test_availability_versions_accumulate(self, database, user, today):
        week_start, _ = week_dates_from(today)
        result = service.update_availability(
            database,
            user,
            effective_from=week_start,
            weekly_minutes={"monday": 120},
            expected_revision=0,
            idempotency_key="av-1",
        )
        assert result["version_no"] == 1
        assert result["weekly_minutes"]["monday"] == 120
        assert result["coordination_required"] is False

        result2 = service.update_availability(
            database,
            user,
            effective_from=week_start,
            weekly_minutes={"monday": 180},
            expected_revision=1,
            idempotency_key="av-2",
        )
        assert result2["version_no"] == 2

        with pytest.raises(GoalflowError) as excinfo:
            service.update_availability(
                database,
                user,
                effective_from=week_start,
                weekly_minutes={"monday": 200},
                expected_revision=1,
                idempotency_key="av-3",
            )
        assert excinfo.value.code.value == "REVISION_CONFLICT"

    def test_override_day_upserts_with_own_revision(self, database, user, today):
        result = service.override_day(
            database,
            user,
            local_date=today,
            override_kind=DailyOverrideKind.REMAINING,
            minutes=90,
            expected_revision=0,
            idempotency_key="ov-1",
        )
        assert result["revision"] == 1
        assert result["override_kind"] is DailyOverrideKind.REMAINING

        result2 = service.override_day(
            database,
            user,
            local_date=today,
            override_kind=DailyOverrideKind.TOTAL,
            minutes=300,
            expected_revision=1,
            idempotency_key="ov-2",
        )
        assert result2["revision"] == 2

        with pytest.raises(GoalflowError):
            service.override_day(
                database,
                user,
                local_date=today,
                override_kind=DailyOverrideKind.TOTAL,
                minutes=300,
                expected_revision=1,
                idempotency_key="ov-3",
            )


class TestConstrainTask:
    def test_set_and_clear_keeps_history_row(self, database, user, today, full_week_availability):
        goal_id = insert_goal(database, user.user_id)
        task_id, _ = insert_task(database, user.user_id, goal_id, expected_minutes=60, latest_date=today)

        created = service.constrain_task(
            database,
            user,
            local_date=today,
            task_id=task_id,
            constraint_kind=TaskDayConstraintKind.MUST_DO_TODAY,
            expected_revision=0,
            idempotency_key="ct-1",
        )
        assert created["status"] == TaskDayConstraintStatus.ACTIVE.value

        cleared = service.constrain_task(
            database,
            user,
            local_date=today,
            task_id=task_id,
            constraint_kind=None,
            expected_revision=created["revision"],
            idempotency_key="ct-2",
        )
        assert cleared["status"] == TaskDayConstraintStatus.CLEARED.value

        with database.read() as session:
            rows = session.query(TaskDayConstraint).all()
        assert len(rows) == 1  # 清除保留行，不删除（A8）

    def test_capacity_conflict_when_mandatory_exceeds_day(self, database, user, today, full_week_availability):
        from datetime import UTC, datetime, timedelta

        goal_id = insert_goal(database, user.user_id)
        first, _ = insert_task(database, user.user_id, goal_id, expected_minutes=200, latest_date=today)
        # 第二条任务期限不在今天：否则它本身就是必须项，第一条就会因"必须项合计"被拒。
        later = (datetime.now(UTC).date() + timedelta(days=7)).isoformat()
        second, _ = insert_task(database, user.user_id, goal_id, expected_minutes=200, latest_date=later)
        service.constrain_task(
            database,
            user,
            local_date=today,
            task_id=first,
            constraint_kind=TaskDayConstraintKind.MUST_DO_TODAY,
            expected_revision=0,
            idempotency_key="ct-3",
        )
        with pytest.raises(GoalflowError) as excinfo:
            service.constrain_task(
                database,
                user,
                local_date=today,
                task_id=second,
                constraint_kind=TaskDayConstraintKind.MUST_DO_TODAY,
                expected_revision=0,
                idempotency_key="ct-4",
            )
        assert excinfo.value.code.value == "BUDGET_CONFLICT"
        assert excinfo.value.details["minutes_gap"] == 160

    def test_other_users_task_is_not_found(self, database, user, second_user, today):
        goal_id = insert_goal(database, user.user_id)
        task_id, _ = insert_task(database, user.user_id, goal_id, latest_date=today)
        with pytest.raises(GoalflowError) as excinfo:
            service.constrain_task(
                database,
                second_user,
                local_date=today,
                task_id=task_id,
                constraint_kind=TaskDayConstraintKind.MUST_DO_TODAY,
                expected_revision=0,
                idempotency_key="ct-5",
            )
        assert excinfo.value.code.value == "NOT_FOUND"


@pytest.fixture
def second_user(database: Database) -> CurrentUser:
    import uuid
    from datetime import UTC, datetime, timedelta

    from goalflow.contracts.enums import UserRole

    user_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat(timespec="microseconds")
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, account_identifier, role, status, timezone, revision, created_at, updated_at)"
                " VALUES (:id, 'scheduling-other@local.dev', 'user', 'active', 'UTC', 0, :now, :now)"
            ),
            {"id": user_id, "now": now},
        )
    moment = datetime.now(UTC)
    return CurrentUser(
        user_id=user_id,
        account_identifier="scheduling-other@local.dev",
        role=UserRole.USER,
        timezone="UTC",
        session_id=str(uuid.uuid4()),
        session_created_at=moment,
        session_expires_at=moment + timedelta(days=1),
    )


class TestAgendaGeneration:
    def test_generation_persists_items_and_current_pointer(self, database, user, today, full_week_availability):
        goal_id = insert_goal(database, user.user_id)
        task_id, _ = insert_task(database, user.user_id, goal_id, expected_minutes=120, latest_date=today)
        set_planning_revision(database, user.user_id, 5)

        result = service.run_agenda_generation(database, user.user_id, today, input_revision=5)

        assert result.items[0].task_id == task_id
        payload = service.get_agenda(database, user, today)
        assert payload is not None
        assert payload["revision"] is not None
        assert payload["revision"]["status"] == "ready"
        assert [item["task_id"] for item in payload["revision"]["items"]] == [task_id]
        assert payload["revision"]["input_planning_revision"] == 5

    def test_stale_input_revision_raises_input_stale(self, database, user, today, full_week_availability):
        insert_goal(database, user.user_id)
        set_planning_revision(database, user.user_id, 9)
        with pytest.raises(InputStale):
            service.run_agenda_generation(database, user.user_id, today, input_revision=8)

    def test_missing_state_returns_none_revision(self, database, user, today):
        payload = service.get_agenda(database, user, today)
        assert payload == {"local_date": today, "planning_revision": 0, "revision": None}


class TestSharedBudgetSeam:
    def test_no_active_goals_returns_none(self, database, user):
        assert service.resolve_shared_budget(database, user.user_id) is None

    def test_capacity_and_demand_sums(self, database, user, today, full_week_availability):
        goal_id = insert_goal(database, user.user_id)
        insert_task(database, user.user_id, goal_id, expected_minutes=60, latest_date=week_dates_from(today)[1])
        insert_task(database, user.user_id, goal_id, expected_minutes=90, latest_date=week_dates_from(today)[1])
        snapshot = service.resolve_shared_budget(database, user.user_id)
        assert snapshot is not None
        assert snapshot["weekly_capacity_minutes"] == 240 * 7
        assert snapshot["total_demand_minutes"] == 150
