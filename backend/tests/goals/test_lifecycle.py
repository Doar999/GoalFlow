"""goals.lifecycle 的业务测试：状态机、暂停/恢复、结束/撤销、派生。

时间判定（撤销窗口）通过 monkeypatch lifecycle._now 驱动，不 sleep。
夹具统一走合法路径（make_active_goal）：活动目标的 revision 基线为 2。
"""

from datetime import timedelta

import pytest
from goals_support import close_goal, create_goal, make_active_goal

from goalflow.contracts.enums import ClosureKind
from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.contracts.policies import CLOSURE_UNDO_WINDOW
from goalflow.goals import lifecycle, service
from goalflow.goals.lifecycle import SharedBudget


class TestPause:
    def test_pause_sets_fields_and_keeps_tasks(self, database, user) -> None:
        goal, _plan = make_active_goal(database, user)
        from datetime import UTC, datetime

        from goalflow.goals.models import Task

        with database.write() as session:
            now = datetime.now(UTC)
            session.add(
                Task(
                    id="task-paused",
                    owner_id=user.user_id,
                    goal_id=goal.id,
                    execution_status="in_progress",
                    progress_revision=0,
                    revision=0,
                    created_at=now,
                    updated_at=now,
                )
            )
        view = lifecycle.pause_goal(
            database, user, goal.id, expected_revision=2, reason="出差两周", idempotency_key="pause-1"
        )
        assert view.goal.status == "paused"
        assert view.goal.pause_reason == "出差两周"
        assert view.goal.paused_at is not None
        # 接缝未接入：空列表 + 显式标记（A12）。
        assert view.affected_dependent_tasks == []
        assert view.dependency_check.value == "not_wired"

        with database.read() as session:
            from sqlalchemy import select

            task = session.scalars(select(Task).where(Task.id == "task-paused")).one()
            assert task.execution_status == "in_progress"

    def test_pausing_twice_is_rejected(self, database, user) -> None:
        goal, _plan = make_active_goal(database, user)
        lifecycle.pause_goal(database, user, goal.id, expected_revision=2, reason=None, idempotency_key="p1")
        with pytest.raises(GoalflowError) as excinfo:
            lifecycle.pause_goal(database, user, goal.id, expected_revision=3, reason=None, idempotency_key="p2")
        assert excinfo.value.code == ErrorCode.GOAL_STATE_CONFLICT

    def test_stale_revision_is_rejected(self, database, user) -> None:
        goal, _plan = make_active_goal(database, user)
        with pytest.raises(GoalflowError) as excinfo:
            lifecycle.pause_goal(database, user, goal.id, expected_revision=9, reason=None, idempotency_key="p3")
        assert excinfo.value.code == ErrorCode.REVISION_CONFLICT

    def test_draft_goal_cannot_be_paused(self, database, user) -> None:
        goal = create_goal(database, user)
        with pytest.raises(GoalflowError) as excinfo:
            lifecycle.pause_goal(database, user, goal.id, expected_revision=0, reason=None, idempotency_key="p4")
        assert excinfo.value.code == ErrorCode.GOAL_STATE_CONFLICT


class TestResume:
    def _paused(self, database, user, key: str):
        goal, _plan = make_active_goal(database, user)
        lifecycle.pause_goal(database, user, goal.id, expected_revision=2, reason=None, idempotency_key=key)
        return goal

    def test_resume_without_budget_seam_completes(self, database, user) -> None:
        goal = self._paused(database, user, "rs-setup")
        view = lifecycle.resume_goal(database, user, goal.id, expected_revision=3, idempotency_key="resume-1")
        assert view.goal.status == "active"
        # 暂停字段随恢复清空（它们描述"当前这次暂停"）。
        assert view.goal.paused_at is None
        assert view.goal.pause_reason is None

    def test_resume_when_budget_is_over_is_rejected(self, database, user, monkeypatch) -> None:
        goal = self._paused(database, user, "rs-over")
        # T05 接入后 resolve_shared_budget 的真实现签名为 (db, user_id)；测试保持注入假快照。
        monkeypatch.setattr(
            lifecycle,
            "resolve_shared_budget",
            lambda db, uid: SharedBudget(weekly_capacity_minutes=60, total_demand_minutes=120),
        )
        with pytest.raises(GoalflowError) as excinfo:
            lifecycle.resume_goal(database, user, goal.id, expected_revision=3, idempotency_key="resume-2")
        assert excinfo.value.code == ErrorCode.BUDGET_CONFLICT
        assert excinfo.value.details["over_by_minutes"] == 60

    def test_resume_from_active_is_rejected(self, database, user) -> None:
        goal, _plan = make_active_goal(database, user)
        with pytest.raises(GoalflowError) as excinfo:
            lifecycle.resume_goal(database, user, goal.id, expected_revision=2, idempotency_key="resume-3")
        assert excinfo.value.code == ErrorCode.GOAL_STATE_CONFLICT


class TestClose:
    def test_completed_saves_criteria_snapshot(self, database, user) -> None:
        goal, _plan = make_active_goal(database, user)
        view = lifecycle.close_goal(
            database,
            user,
            goal.id,
            expected_revision=2,
            closure_kind=ClosureKind.COMPLETED,
            note="赛事顺利完成",
            criteria_confirmations=[{"criterion_key": "distance", "met": True, "note": None}],
            idempotency_key="close-1",
        )
        assert view.status == "completed"
        assert view.closed_at is not None
        assert view.closure_kind == "completed"

    def test_maintenance_cannot_complete(self, database, user) -> None:
        goal, _plan = make_active_goal(database, user, key="cl-m", time_boundary="ongoing_maintenance")
        with pytest.raises(GoalflowError) as excinfo:
            lifecycle.close_goal(
                database,
                user,
                goal.id,
                expected_revision=2,
                closure_kind=ClosureKind.COMPLETED,
                note=None,
                criteria_confirmations=[],
                idempotency_key="close-m",
            )
        assert excinfo.value.code == ErrorCode.GOAL_STATE_CONFLICT

    def test_paused_goal_cannot_complete(self, database, user) -> None:
        goal, _plan = make_active_goal(database, user)
        lifecycle.pause_goal(database, user, goal.id, expected_revision=2, reason=None, idempotency_key="pc-p")
        with pytest.raises(GoalflowError) as excinfo:
            lifecycle.close_goal(
                database,
                user,
                goal.id,
                expected_revision=3,
                closure_kind=ClosureKind.COMPLETED,
                note=None,
                criteria_confirmations=[],
                idempotency_key="pc-c",
            )
        assert excinfo.value.code == ErrorCode.GOAL_STATE_CONFLICT

    def test_paused_goal_can_stop(self, database, user) -> None:
        goal, _plan = make_active_goal(database, user)
        lifecycle.pause_goal(database, user, goal.id, expected_revision=2, reason=None, idempotency_key="ps-p")
        view = lifecycle.close_goal(
            database,
            user,
            goal.id,
            expected_revision=3,
            closure_kind=ClosureKind.STOPPED,
            note=None,
            criteria_confirmations=[],
            idempotency_key="ps-c",
        )
        assert view.status == "stopped"

    def test_second_close_is_rejected_as_terminal(self, database, user) -> None:
        goal, _plan = make_active_goal(database, user)
        view = lifecycle.close_goal(
            database,
            user,
            goal.id,
            expected_revision=2,
            closure_kind=ClosureKind.COMPLETED,
            note=None,
            criteria_confirmations=[],
            idempotency_key="close-stat",
        )
        assert view.status == "completed"
        with pytest.raises(GoalflowError) as excinfo:
            lifecycle.close_goal(
                database,
                user,
                goal.id,
                expected_revision=3,
                closure_kind=ClosureKind.COMPLETED,
                note=None,
                criteria_confirmations=[],
                idempotency_key="close-again",
            )
        assert excinfo.value.code == ErrorCode.GOAL_STATE_CONFLICT

    def test_close_is_idempotent_per_key(self, database, user) -> None:
        goal, _plan = make_active_goal(database, user)
        first = lifecycle.close_goal(
            database,
            user,
            goal.id,
            expected_revision=2,
            closure_kind=ClosureKind.STOPPED,
            note=None,
            criteria_confirmations=[],
            idempotency_key="close-key",
        )
        second = lifecycle.close_goal(
            database,
            user,
            goal.id,
            expected_revision=2,
            closure_kind=ClosureKind.STOPPED,
            note=None,
            criteria_confirmations=[],
            idempotency_key="close-key",
        )
        assert first.closed_at == second.closed_at
        assert first.revision == second.revision


class TestUndoClosure:
    def _stopped(self, database, user) -> object:
        goal, _plan = make_active_goal(database, user)
        close_goal(database, user, goal.id, expected_revision=2, key="uc-close")
        return goal

    def test_undo_within_window_restores_prior_status(self, database, user) -> None:
        goal = self._stopped(database, user)
        view = lifecycle.undo_closure(database, user, goal.id, expected_revision=3, idempotency_key="uc-undo")
        # 结束前是 active（快照的 pre_closure_status），撤销回到结束前状态。
        assert view.status == "active"
        assert view.closed_at is None
        assert view.closure_kind is None

    def test_undo_outside_window_is_rejected(self, database, user, monkeypatch) -> None:
        goal = self._stopped(database, user)
        real_now = lifecycle._now
        monkeypatch.setattr(lifecycle, "_now", lambda: real_now() + CLOSURE_UNDO_WINDOW + timedelta(minutes=1))
        with pytest.raises(GoalflowError) as excinfo:
            lifecycle.undo_closure(database, user, goal.id, expected_revision=3, idempotency_key="uc-late")
        assert excinfo.value.code == ErrorCode.GOAL_STATE_CONFLICT

    def test_undo_from_active_is_rejected(self, database, user) -> None:
        goal, _plan = make_active_goal(database, user)
        with pytest.raises(GoalflowError) as excinfo:
            lifecycle.undo_closure(database, user, goal.id, expected_revision=2, idempotency_key="uc-active")
        assert excinfo.value.code == ErrorCode.GOAL_STATE_CONFLICT


class TestDeriveGoal:
    def _terminal_source(self, database, user) -> object:
        goal, _plan = make_active_goal(database, user)
        close_goal(database, user, goal.id, expected_revision=2, key="dv-close")
        return goal

    def test_derive_copies_profile_draft_but_not_records(self, database, user) -> None:
        source = self._terminal_source(database, user)
        view = lifecycle.derive_goal(database, user, source.id, expected_revision=3, idempotency_key="dv-1")
        assert view.status == "draft"
        assert view.source_goal_id == source.id
        assert view.id != source.id

        draft = service.get_profile_draft(database, user, view.id)
        assert draft.content["result_definition"] == "10 公里"
        assert draft.source_map["result_definition"] == "derived"

        # 源目标保持终态。
        assert service.get_goal(database, user, source.id).status == "stopped"

    def test_derive_requires_terminal_source(self, database, user) -> None:
        goal, _plan = make_active_goal(database, user)
        with pytest.raises(GoalflowError) as excinfo:
            lifecycle.derive_goal(database, user, goal.id, expected_revision=2, idempotency_key="dv-active")
        assert excinfo.value.code == ErrorCode.GOAL_STATE_CONFLICT

    def test_derived_goal_is_independent(self, database, user) -> None:
        source = self._terminal_source(database, user)
        derived = lifecycle.derive_goal(database, user, source.id, expected_revision=3, idempotency_key="dv-2")
        # 派生目标是独立的 draft：编辑它不影响源目标（源保持终态）。
        service.update_profile_draft(
            database, user, derived.id, edits={"result_definition": "半马"}, expected_revision=0
        )
        assert service.get_goal(database, user, source.id).status == "stopped"
        assert service.get_profile_draft(database, user, derived.id).content["result_definition"] == "半马"
