"""activate_plan 的事务与幂等测试（12 号第 7 节；交接卡 A10）。

启用是"草稿不启用"红线的反面：只有它能把 draft → active、proposed → pending，
且旧活动版本必须让位。生成管道归 T08，这里用 ORM 直接铺草稿数据。
"""

from datetime import UTC, datetime

import pytest
from goals_support import make_active_goal, seed_plan
from sqlalchemy import select

from goalflow.contracts.enums import ClosureKind
from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.goals import lifecycle, service
from goalflow.goals.models import (
    PlanVersion,
    Task,
)

NOW = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)


def _goal_with_plan(database, user) -> tuple[object, str]:
    goal = service.create_goal(
        database, user, title="三个月跑完十公里", initial_description=None, idempotency_key="act-create"
    )
    plan_id = seed_plan(database, user, goal)
    return goal, plan_id


class TestActivatePlan:
    def test_activation_switches_goal_plan_and_tasks_atomically(self, database, user) -> None:
        goal, plan_id = _goal_with_plan(database, user)
        outcome = lifecycle.activate_plan(
            database,
            user,
            goal.id,
            draft_plan_id=plan_id,
            expected_revision=0,
            planning_revision=None,
            idempotency_key="act-1",
        )
        assert outcome.plan_version_id == plan_id
        assert outcome.goal.status == "active"
        assert outcome.goal.current_plan_version_id == plan_id
        assert outcome.batch_window == {"window_start": "2026-10-01", "window_end": "2026-10-07"}

        with database.read() as session:
            plan = session.scalars(select(PlanVersion).where(PlanVersion.id == plan_id)).one()
            assert plan.status == "active"
            task = session.scalars(select(Task).where(Task.id == f"task-{goal.id[:8]}-1")).one()
            # 草稿不启用红线的反面：启用后 proposed → pending（R05）。
            assert task.execution_status == "pending"

    def test_activation_is_idempotent_per_key(self, database, user) -> None:
        goal, plan_id = _goal_with_plan(database, user)
        first = lifecycle.activate_plan(
            database,
            user,
            goal.id,
            draft_plan_id=plan_id,
            expected_revision=0,
            planning_revision=None,
            idempotency_key="act-key",
        )
        second = lifecycle.activate_plan(
            database,
            user,
            goal.id,
            draft_plan_id=plan_id,
            expected_revision=0,
            planning_revision=None,
            idempotency_key="act-key",
        )
        assert first.plan_version_id == second.plan_version_id == plan_id

    def test_stale_goal_revision_is_rejected(self, database, user) -> None:
        goal, plan_id = _goal_with_plan(database, user)
        with pytest.raises(GoalflowError) as excinfo:
            lifecycle.activate_plan(
                database,
                user,
                goal.id,
                draft_plan_id=plan_id,
                expected_revision=5,
                planning_revision=None,
                idempotency_key="act-stale",
            )
        assert excinfo.value.code == ErrorCode.REVISION_CONFLICT

    def test_paused_goal_cannot_activate(self, database, user) -> None:
        goal, plan_id = make_active_goal(database, user)
        lifecycle.pause_goal(database, user, goal.id, expected_revision=2, reason=None, idempotency_key="act-p")
        with pytest.raises(GoalflowError) as excinfo:
            lifecycle.activate_plan(
                database,
                user,
                goal.id,
                draft_plan_id=plan_id,
                expected_revision=3,
                planning_revision=None,
                idempotency_key="act-paused",
            )
        assert excinfo.value.code == ErrorCode.GOAL_STATE_CONFLICT

    def test_non_draft_plan_cannot_activate(self, database, user) -> None:
        goal, _plan_id = _goal_with_plan(database, user)
        active_plan_id = seed_plan(database, user, goal, version_no=2, status="active")
        with pytest.raises(GoalflowError) as excinfo:
            lifecycle.activate_plan(
                database,
                user,
                goal.id,
                draft_plan_id=active_plan_id,
                expected_revision=0,
                planning_revision=None,
                idempotency_key="act-nondraft",
            )
        assert excinfo.value.code == ErrorCode.GOAL_STATE_CONFLICT

    def test_new_activation_supersedes_previous_active_version(self, database, user) -> None:
        goal, first_plan_id = _goal_with_plan(database, user)
        lifecycle.activate_plan(
            database,
            user,
            goal.id,
            draft_plan_id=first_plan_id,
            expected_revision=0,
            planning_revision=None,
            idempotency_key="act-first",
        )
        second_plan_id = seed_plan(database, user, goal, version_no=2)
        # 启用第二版需携带当前 revision（第一版启用后目标 revision +1）。
        second = lifecycle.activate_plan(
            database,
            user,
            goal.id,
            draft_plan_id=second_plan_id,
            expected_revision=1,
            planning_revision=None,
            idempotency_key="act-second",
        )
        assert second.plan_version_id == second_plan_id
        with database.read() as session:
            old = session.scalars(select(PlanVersion).where(PlanVersion.id == first_plan_id)).one()
            new = session.scalars(select(PlanVersion).where(PlanVersion.id == second_plan_id)).one()
            assert old.status == "superseded"
            assert new.status == "active"
            # 一个目标最多一个当前执行版本（03 第 1 节部分唯一索引）。
            actives = session.scalars(
                select(PlanVersion).where(PlanVersion.goal_id == goal.id, PlanVersion.status == "active")
            ).all()
            assert len(actives) == 1

    def test_closed_goal_cannot_activate(self, database, user) -> None:
        goal, plan_id = make_active_goal(database, user)
        lifecycle.close_goal(
            database,
            user,
            goal.id,
            expected_revision=2,
            closure_kind=ClosureKind.STOPPED,
            note=None,
            criteria_confirmations=[],
            idempotency_key="act-close",
        )
        with pytest.raises(GoalflowError) as excinfo:
            lifecycle.activate_plan(
                database,
                user,
                goal.id,
                draft_plan_id=plan_id,
                expected_revision=3,
                planning_revision=None,
                idempotency_key="act-closed",
            )
        assert excinfo.value.code == ErrorCode.GOAL_STATE_CONFLICT
