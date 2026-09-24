"""goals.service 的业务测试：目标创建、档案草稿与确认、路线选择、草稿任务编辑。"""

import pytest

from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.goals import service


def _create_goal(db, user, key: str = "svc-create-key", title: str = "三个月跑完十公里"):
    return service.create_goal(db, user, title=title, initial_description="从零开始跑步", idempotency_key=key)


def _confirm(db, user, goal_id: str, expected_revision: int, key: str):
    return service.confirm_profile(db, user, goal_id, expected_revision=expected_revision, idempotency_key=key)


class TestCreateGoal:
    def test_same_key_creates_one_goal(self, database, user) -> None:
        first = _create_goal(database, user, key="one-key")
        second = _create_goal(database, user, key="one-key")
        assert first.id == second.id
        assert first.status == "draft"
        assert first.revision == 0

    def test_new_key_creates_another_goal(self, database, user) -> None:
        first = _create_goal(database, user, key="key-a")
        second = _create_goal(database, user, key="key-b")
        assert first.id != second.id

    def test_seeds_profile_draft_and_session(self, database, user) -> None:
        goal = _create_goal(database, user)
        draft = service.get_profile_draft(database, user, goal.id)
        assert draft.content["title"] == "三个月跑完十公里"
        assert draft.content["initial_description"] == "从零开始跑步"
        assert draft.source_map["title"] == "user"
        assert draft.readiness == "needs_input"


class TestUpdateProfileDraft:
    def test_edits_merge_and_bump_revision(self, database, user) -> None:
        goal = _create_goal(database, user)
        view = service.update_profile_draft(database, user, goal.id, edits={"weekly_days": 3}, expected_revision=0)
        assert view.revision == 1
        assert view.content["weekly_days"] == 3
        assert view.source_map["weekly_days"] == "user_edited"

    def test_stale_revision_is_rejected(self, database, user) -> None:
        goal = _create_goal(database, user)
        service.update_profile_draft(database, user, goal.id, edits={}, expected_revision=0)
        with pytest.raises(GoalflowError) as excinfo:
            service.update_profile_draft(database, user, goal.id, edits={}, expected_revision=0)
        assert excinfo.value.code == ErrorCode.REVISION_CONFLICT


class TestConfirmProfile:
    def _prepared(self, database, user, time_boundary: str = "fixed_date"):
        goal = _create_goal(database, user)
        service.update_profile_draft(
            database,
            user,
            goal.id,
            edits={
                "result_definition": "10 公里内完成",
                "time_boundary": time_boundary,
                "success_criteria": [{"key": "distance", "met": False}],
            },
            expected_revision=0,
        )
        return goal

    def test_creates_immutable_version_and_updates_goal(self, database, user) -> None:
        goal = self._prepared(database, user)
        profile = _confirm(database, user, goal.id, expected_revision=0, key="confirm-1")
        assert profile.version_no == 1
        assert profile.success_criteria == [{"key": "distance", "met": False}]

        updated = service.get_goal(database, user, goal.id)
        assert updated.active_profile_id == profile.id
        # 时间边界 fixed_date → achievement（D12 第 1 节）。
        assert updated.kind == "achievement"
        assert updated.revision == 1

    def test_maintenance_boundary_derives_maintenance_kind(self, database, user) -> None:
        goal = self._prepared(database, user, time_boundary="ongoing_maintenance")
        _confirm(database, user, goal.id, expected_revision=0, key="confirm-m")
        assert service.get_goal(database, user, goal.id).kind == "maintenance"

    def test_second_confirmation_creates_version_two(self, database, user) -> None:
        goal = self._prepared(database, user)
        first = _confirm(database, user, goal.id, expected_revision=0, key="confirm-a")
        second = _confirm(database, user, goal.id, expected_revision=1, key="confirm-b")
        assert (first.version_no, second.version_no) == (1, 2)

    def test_replay_returns_same_profile(self, database, user) -> None:
        goal = self._prepared(database, user)
        first = _confirm(database, user, goal.id, expected_revision=0, key="same-key")
        second = _confirm(database, user, goal.id, expected_revision=0, key="same-key")
        assert first.id == second.id

    def test_blocked_draft_cannot_be_confirmed(self, database, user) -> None:
        goal = _create_goal(database, user)
        from sqlalchemy import text

        with database.engine.begin() as connection:
            connection.execute(
                text("UPDATE goal_profile_drafts SET readiness = 'blocked' WHERE goal_id = :gid"),
                {"gid": goal.id},
            )
        with pytest.raises(GoalflowError) as excinfo:
            _confirm(database, user, goal.id, expected_revision=0, key="confirm-blocked")
        assert excinfo.value.code == ErrorCode.CONFIRMATION_REQUIRED


class TestSelectRoute:
    def _route_fixture(self, database, user, goal, *, route_status: str = "current", set_status: str = "current"):
        from datetime import UTC, datetime

        from goalflow.goals.models import GoalProfile, Route, RouteSet

        now = datetime.now(UTC)
        profile_id = "profile-" + goal.id[:8]
        with database.write() as session:
            session.add(
                GoalProfile(
                    id=profile_id,
                    owner_id=user.user_id,
                    goal_id=goal.id,
                    version_no=1,
                    result_definition="10 公里",
                    success_criteria_json="[]",
                    baseline_json="{}",
                    constraints_json="{}",
                    facts_json="{}",
                    confirmed_at=now,
                )
            )
        set_id = "rset-" + goal.id[:8]
        route_id = "route-" + goal.id[:8]
        with database.write() as session:
            session.add(
                RouteSet(
                    id=set_id,
                    owner_id=user.user_id,
                    goal_id=goal.id,
                    profile_id=profile_id,
                    planning_revision=0,
                    availability_revision=0,
                    generation_policy_version="route-policy-v1",
                    input_snapshot_json="{}",
                    input_hash="hash-" + goal.id[:8],
                    status=set_status,
                    created_at=now,
                )
            )
            session.flush()
            session.add(
                Route(
                    id=route_id,
                    owner_id=user.user_id,
                    route_set_id=set_id,
                    status=route_status,
                    title="渐进跑量",
                    approach="每周加 10%",
                    difference_keys_json='["pace"]',
                    duration_range_json='{"weeks": [10, 12]}',
                    phase_outline_json="[]",
                    weekly_minutes=90,
                    tradeoffs_json="[]",
                    risks_json="[]",
                    assumptions_json="[]",
                    resources_json="[]",
                    derived_metrics_json=(
                        '{"total_estimated_minutes": 1080, "peak_weekly_minutes": 120,'
                        ' "available_weekly_minutes": 150, "budget_gap_minutes": 0,'
                        ' "recommendation_eligible": true, "conflicts": []}'
                    ),
                    created_at=now,
                )
            )
        return route_id

    def test_selection_records_route(self, database, user) -> None:
        goal = _create_goal(database, user)
        route_id = self._route_fixture(database, user, goal)
        view = service.select_route(
            database, user, goal.id, route_id=route_id, expected_revision=0, idempotency_key="sel-1"
        )
        assert view.route_id == route_id
        current = service.get_current_route_set(database, user, goal.id)
        assert [route.id for route in current.routes] == [route_id]
        assert current.routes[0].derived_metrics["recommendation_eligible"] is True

    def test_route_outside_current_set_is_stale(self, database, user) -> None:
        goal = _create_goal(database, user)
        route_id = self._route_fixture(database, user, goal, set_status="superseded")
        with pytest.raises(GoalflowError) as excinfo:
            service.select_route(
                database, user, goal.id, route_id=route_id, expected_revision=0, idempotency_key="sel-2"
            )
        assert excinfo.value.code == ErrorCode.INPUT_STALE

    def test_superseded_route_cannot_be_selected(self, database, user) -> None:
        goal = _create_goal(database, user)
        route_id = self._route_fixture(database, user, goal, route_status="superseded")
        with pytest.raises(GoalflowError) as excinfo:
            service.select_route(
                database, user, goal.id, route_id=route_id, expected_revision=0, idempotency_key="sel-3"
            )
        assert excinfo.value.code == ErrorCode.NOT_FOUND


class TestUpdateDraftTask:
    def _draft_fixture(self, database, user):
        """goal + draft 计划 + 阶段 + 批次 + proposed 任务 + 规格 + 归属。"""
        from datetime import UTC, datetime

        from goalflow.goals.models import (
            GoalProfile,
            PlanPhase,
            PlanTaskMembership,
            PlanVersion,
            Route,
            RouteSet,
            Task,
            TaskBatch,
            TaskSpec,
        )

        goal = _create_goal(database, user)
        now = datetime.now(UTC)
        profile_id = "profile-" + goal.id[:8]
        route_set_id = "rset-" + goal.id[:8]
        route_id = "route-" + goal.id[:8]
        plan_id = "plan-" + goal.id[:8]
        phase_id = "phase-" + goal.id[:8]
        batch_id = "batch-" + goal.id[:8]
        task_id = "task-" + goal.id[:8]
        spec_id = "spec-" + goal.id[:8]
        with database.write() as session:
            session.add(
                GoalProfile(
                    id=profile_id,
                    owner_id=user.user_id,
                    goal_id=goal.id,
                    version_no=1,
                    result_definition="10 公里",
                    success_criteria_json="[]",
                    baseline_json="{}",
                    constraints_json="{}",
                    facts_json="{}",
                    confirmed_at=now,
                )
            )
            session.flush()
            session.add(
                RouteSet(
                    id=route_set_id,
                    owner_id=user.user_id,
                    goal_id=goal.id,
                    profile_id=profile_id,
                    planning_revision=0,
                    availability_revision=0,
                    generation_policy_version="route-policy-v1",
                    input_snapshot_json="{}",
                    input_hash="hash-" + goal.id[:8],
                    status="current",
                    created_at=now,
                )
            )
            session.flush()
            session.add(
                Route(
                    id=route_id,
                    owner_id=user.user_id,
                    route_set_id=route_set_id,
                    status="current",
                    title="渐进跑量",
                    approach="每周加 10%",
                    difference_keys_json="[]",
                    duration_range_json="{}",
                    phase_outline_json="[]",
                    weekly_minutes=90,
                    tradeoffs_json="[]",
                    risks_json="[]",
                    assumptions_json="[]",
                    resources_json="[]",
                    derived_metrics_json="{}",
                    created_at=now,
                )
            )
            session.flush()
            session.add(
                PlanVersion(
                    id=plan_id,
                    owner_id=user.user_id,
                    goal_id=goal.id,
                    version_no=1,
                    profile_id=profile_id,
                    route_id=route_id,
                    status="draft",
                    start_date="2026-10-01",
                    horizon_end="2026-12-31",
                    strategy_version="plan-v1",
                    created_at=now,
                )
            )
            # 循环外键使 ORM 无法保证 flush 顺序，依赖父行的插入显式 flush 控序。
            session.flush()
            session.add(
                PlanPhase(
                    id=phase_id,
                    owner_id=user.user_id,
                    plan_version_id=plan_id,
                    phase_key="base",
                    rank=1,
                    title="打基础",
                    outcome="能连续跑 3 公里",
                    exit_criteria_json="[]",
                    duration_estimate_json='{"weeks": [3, 4]}',
                    created_at=now,
                )
            )
            session.flush()
            session.add(
                TaskBatch(
                    id=batch_id,
                    owner_id=user.user_id,
                    plan_version_id=plan_id,
                    window_start="2026-10-01",
                    window_end="2026-10-07",
                    input_progress_revision=0,
                    generation_policy_version="batch-v1",
                    status="active",
                    created_at=now,
                )
            )
            session.flush()
            session.add(
                Task(
                    id=task_id,
                    owner_id=user.user_id,
                    goal_id=goal.id,
                    execution_status="proposed",
                    progress_revision=0,
                    revision=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.flush()
            session.add(
                TaskSpec(
                    id=spec_id,
                    owner_id=user.user_id,
                    task_id=task_id,
                    spec_no=1,
                    title="轻松跑 20 分钟",
                    instructions="配速 conversation pace",
                    completion_criteria_json="[]",
                    executor="user",
                    expected_minutes=20,
                    minimum_minutes=15,
                    maximum_minutes=30,
                    latest_date="2026-10-03",
                    can_split=False,
                    created_at=now,
                )
            )
            session.flush()
            session.add(
                PlanTaskMembership(
                    id="member-" + goal.id[:8],
                    owner_id=user.user_id,
                    plan_version_id=plan_id,
                    task_batch_id=batch_id,
                    phase_id=phase_id,
                    task_id=task_id,
                    task_spec_id=spec_id,
                    created_at=now,
                )
            )
        return goal, plan_id, task_id

    def test_edit_creates_new_spec_and_keeps_old(self, database, user) -> None:
        goal, _plan_id, task_id = self._draft_fixture(database, user)
        view = service.update_draft_task(
            database,
            user,
            goal.id,
            task_id,
            expected_revision=0,
            title="轻松跑 25 分钟",
            instructions=None,
            expected_minutes=25,
            minimum_minutes=None,
            maximum_minutes=None,
        )
        assert view.spec_no == 2
        assert view.title == "轻松跑 25 分钟"
        assert view.expected_minutes == 25
        # 原 spec 保留（03 第 3 节：内容更新创建新 spec）。
        from sqlalchemy import select

        from goalflow.goals.models import TaskSpec

        with database.read() as session:
            specs = session.scalars(select(TaskSpec).where(TaskSpec.task_id == task_id)).all()
            assert len(specs) == 2

    def test_stale_task_revision_is_rejected(self, database, user) -> None:
        goal, _plan_id, task_id = self._draft_fixture(database, user)
        with pytest.raises(GoalflowError) as excinfo:
            service.update_draft_task(
                database,
                user,
                goal.id,
                task_id,
                expected_revision=7,
                title=None,
                instructions=None,
                expected_minutes=None,
                minimum_minutes=None,
                maximum_minutes=None,
            )
        assert excinfo.value.code == ErrorCode.REVISION_CONFLICT

    def test_minutes_ordering_is_enforced(self, database, user) -> None:
        goal, _plan_id, task_id = self._draft_fixture(database, user)
        with pytest.raises(GoalflowError) as excinfo:
            service.update_draft_task(
                database,
                user,
                goal.id,
                task_id,
                expected_revision=0,
                title=None,
                instructions=None,
                expected_minutes=40,
                minimum_minutes=45,
                maximum_minutes=None,
            )
        assert excinfo.value.code == ErrorCode.VALIDATION_FAILED
