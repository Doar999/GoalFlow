"""links 模块业务测试：建立、无环、解除提案与原子应用（T06 交接卡第 5 节验收场景）。"""

import pytest
from links_support import (
    add_task_to_plan,
    edge_command,
    make_active_goal,
    make_link,
    make_user,
    planning_revision_of,
    set_task_status,
)
from sqlalchemy import select

from goalflow.contracts.errors import ErrorCode, GoalflowError
from goalflow.links import service as links
from goalflow.links.models import GoalLink, TaskDependency
from goalflow.scheduling.service import build_snapshot


def _edge_count(database, link_id: str) -> int:
    with database.read() as session:
        return len(session.scalars(select(TaskDependency).where(TaskDependency.goal_link_id == link_id)).all())


class TestPropose:
    def test_creates_proposed_link_with_normalized_pair(self, database, user) -> None:
        goal_a, _plan_a = make_active_goal(database, user, key="ga")
        goal_b, _plan_b = make_active_goal(database, user, key="gb", title="读完两本书")
        task_a = f"task-{goal_a.id[:8]}-1"
        task_b = f"task-{goal_b.id[:8]}-1"

        view = links.propose_goal_link(
            database,
            user,
            goal_b.id,  # 反向提出：规范化后 goal_a_id 仍应是较小 id 一方
            target_goal_id=goal_a.id,
            dependencies=[edge_command(task_a, task_b)],
            idempotency_key="propose-1",
        )
        assert view.status == "proposed"
        assert view.confirmed_at is None
        assert view.goal_a_id == min(goal_a.id, goal_b.id)
        assert view.goal_b_id == max(goal_a.id, goal_b.id)
        # 依赖边在确认前不落库（03 第 57 行）。
        assert _edge_count(database, view.id) == 0

    def test_self_link_rejected(self, database, user) -> None:
        goal, _plan = make_active_goal(database, user, key="ga")
        task = f"task-{goal.id[:8]}-1"
        with pytest.raises(GoalflowError) as excinfo:
            links.propose_goal_link(
                database,
                user,
                goal.id,
                target_goal_id=goal.id,
                dependencies=[edge_command(task, task)],
                idempotency_key="self-1",
            )
        assert excinfo.value.code == ErrorCode.VALIDATION_FAILED

    def test_edges_must_cross_the_pair(self, database, user) -> None:
        goal_a, _plan_a = make_active_goal(database, user, key="ga")
        goal_b, _plan_b = make_active_goal(database, user, key="gb", title="读完两本书")
        other_task = add_task_to_plan(database, user, _plan_b, "gb-extra")

        with pytest.raises(GoalflowError) as excinfo:
            links.propose_goal_link(
                database,
                user,
                goal_a.id,
                target_goal_id=goal_b.id,
                dependencies=[edge_command(other_task, other_task)],
                idempotency_key="cross-1",
            )
        assert excinfo.value.code == ErrorCode.VALIDATION_FAILED

    def test_duplicate_enabled_pair_rejected(self, database, user) -> None:
        goal_a, _plan_a = make_active_goal(database, user, key="ga")
        goal_b, _plan_b = make_active_goal(database, user, key="gb", title="读完两本书")
        task_a = f"task-{goal_a.id[:8]}-1"
        task_b = f"task-{goal_b.id[:8]}-1"
        make_link(database, user, goal_a, goal_b, [edge_command(task_a, task_b)], key="dup", confirm=False)

        with pytest.raises(GoalflowError) as excinfo:
            links.propose_goal_link(
                database,
                user,
                goal_a.id,
                target_goal_id=goal_b.id,
                dependencies=[edge_command(task_a, task_b)],
                idempotency_key="dup-2",
            )
        assert excinfo.value.code == ErrorCode.VALIDATION_FAILED

    def test_cycle_within_one_propose_rejected(self, database, user) -> None:
        goal_a, _plan_a = make_active_goal(database, user, key="ga")
        goal_b, _plan_b = make_active_goal(database, user, key="gb", title="读完两本书")
        task_a = f"task-{goal_a.id[:8]}-1"
        task_b = f"task-{goal_b.id[:8]}-1"

        with pytest.raises(GoalflowError) as excinfo:
            links.propose_goal_link(
                database,
                user,
                goal_a.id,
                target_goal_id=goal_b.id,
                dependencies=[edge_command(task_a, task_b), edge_command(task_b, task_a)],
                idempotency_key="cycle-1",
            )
        assert excinfo.value.code == ErrorCode.DEPENDENCY_CYCLE

    def test_cycle_across_confirmed_links_rejected_at_propose(self, database, user) -> None:
        goal_a, _plan_a = make_active_goal(database, user, key="ga")
        goal_b, _plan_b = make_active_goal(database, user, key="gb", title="读完两本书")
        goal_c, _plan_c = make_active_goal(database, user, key="gc", title="学会游泳")
        task_a = f"task-{goal_a.id[:8]}-1"
        task_b = f"task-{goal_b.id[:8]}-1"
        task_c = f"task-{goal_c.id[:8]}-1"

        make_link(database, user, goal_a, goal_b, [edge_command(task_a, task_b)], key="l1")
        make_link(database, user, goal_b, goal_c, [edge_command(task_b, task_c)], key="l2")

        with pytest.raises(GoalflowError) as excinfo:
            links.propose_goal_link(
                database,
                user,
                goal_a.id,
                target_goal_id=goal_c.id,
                dependencies=[edge_command(task_c, task_a)],
                idempotency_key="l3",
            )
        assert excinfo.value.code == ErrorCode.DEPENDENCY_CYCLE


class TestConfirm:
    def test_confirm_creates_edges_and_activates(self, database, user) -> None:
        goal_a, _plan_a = make_active_goal(database, user, key="ga")
        goal_b, _plan_b = make_active_goal(database, user, key="gb", title="读完两本书")
        task_a = f"task-{goal_a.id[:8]}-1"
        task_b = f"task-{goal_b.id[:8]}-1"
        edges = [edge_command(task_a, task_b)]
        revision_before = planning_revision_of(database, user.user_id)

        link = links.propose_goal_link(
            database, user, goal_a.id, target_goal_id=goal_b.id, dependencies=edges, idempotency_key="c1-p"
        )
        view = links.confirm_goal_link(database, user, link.id, dependencies=edges, idempotency_key="c1-c")
        assert view.status == "active"
        assert view.confirmed_at is not None
        assert _edge_count(database, link.id) == 1
        # 依赖图变化影响排期输入：planning revision 递增。
        assert planning_revision_of(database, user.user_id) > revision_before

    def test_confirm_with_cross_link_cycle_rejected_and_nothing_created(self, database, user) -> None:
        goal_a, _plan_a = make_active_goal(database, user, key="ga")
        goal_b, _plan_b = make_active_goal(database, user, key="gb", title="读完两本书")
        goal_c, _plan_c = make_active_goal(database, user, key="gc", title="学会游泳")
        task_a = f"task-{goal_a.id[:8]}-1"
        task_b = f"task-{goal_b.id[:8]}-1"
        task_c = f"task-{goal_c.id[:8]}-1"

        make_link(database, user, goal_a, goal_b, [edge_command(task_a, task_b)], key="l1")
        make_link(database, user, goal_b, goal_c, [edge_command(task_b, task_c)], key="l2")

        task_a2 = add_task_to_plan(database, user, _plan_a, "ga-second")
        # 提案带一条不成环的边（A.t2 → C.t1）；
        # 确认时换成 C.t1 → A.t1，与既有链 A.t1→B.t1→C.t1 构成环——环在确认事务内被拦下。
        link = links.propose_goal_link(
            database,
            user,
            goal_c.id,
            target_goal_id=goal_a.id,
            dependencies=[edge_command(task_a2, task_c)],
            idempotency_key="l3-p",
        )
        with pytest.raises(GoalflowError) as excinfo:
            links.confirm_goal_link(
                database,
                user,
                link.id,
                dependencies=[edge_command(task_c, task_a)],
                idempotency_key="l3-c",
            )
        assert excinfo.value.code == ErrorCode.DEPENDENCY_CYCLE
        assert _edge_count(database, link.id) == 0
        with database.read() as session:
            assert session.scalars(select(GoalLink).where(GoalLink.id == link.id)).one().status == "proposed"

    def test_confirm_twice_rejected(self, database, user) -> None:
        goal_a, _plan_a = make_active_goal(database, user, key="ga")
        goal_b, _plan_b = make_active_goal(database, user, key="gb", title="读完两本书")
        edges = [edge_command(f"task-{goal_a.id[:8]}-1", f"task-{goal_b.id[:8]}-1")]
        link = links.propose_goal_link(
            database, user, goal_a.id, target_goal_id=goal_b.id, dependencies=edges, idempotency_key="c2-p"
        )
        links.confirm_goal_link(database, user, link.id, dependencies=edges, idempotency_key="c2-c1")
        with pytest.raises(GoalflowError) as excinfo:
            links.confirm_goal_link(database, user, link.id, dependencies=edges, idempotency_key="c2-c2")
        assert excinfo.value.code == ErrorCode.GOAL_STATE_CONFLICT


class TestUnlink:
    def _confirmed_link(self, database, user, *, predecessor_status: str = "pending"):
        goal_a, _plan_a = make_active_goal(database, user, key="ga")
        goal_b, _plan_b = make_active_goal(database, user, key="gb", title="读完两本书")
        task_a = f"task-{goal_a.id[:8]}-1"
        task_b = f"task-{goal_b.id[:8]}-1"
        link_id = make_link(database, user, goal_a, goal_b, [edge_command(task_a, task_b)], key="u")
        if predecessor_status != "pending":
            set_task_status(database, task_a, predecessor_status)
        return goal_a, goal_b, task_a, task_b, link_id

    def test_proposal_lists_unsatisfied_edges(self, database, user) -> None:
        _goal_a, _goal_b, _task_a, task_b, link_id = self._confirmed_link(database, user)
        proposal = links.propose_unlink(database, user, link_id, idempotency_key="u-p1")
        assert proposal.change_class == "confirmation_required"
        assert proposal.status == "pending"
        assert len(proposal.impact.unsatisfied_edges) == 1
        entry = proposal.impact.unsatisfied_edges[0]
        assert entry["successor_task_id"] == task_b
        assert entry["successor_status"] == "pending"
        assert proposal.impact.satisfied_edges == []

    def test_completed_predecessor_edge_archived_as_satisfied(self, database, user) -> None:
        _goal_a, _goal_b, _task_a, task_b, link_id = self._confirmed_link(
            database, user, predecessor_status="completed"
        )
        proposal = links.propose_unlink(database, user, link_id, idempotency_key="u-p2")
        assert proposal.impact.unsatisfied_edges == []
        assert len(proposal.impact.satisfied_edges) == 1
        assert proposal.impact.satisfied_edges[0]["successor_task_id"] == task_b

    def test_proposed_link_cannot_be_unlinked(self, database, user) -> None:
        goal_a, _plan_a = make_active_goal(database, user, key="ga")
        goal_b, _plan_b = make_active_goal(database, user, key="gb", title="读完两本书")
        link_id = make_link(
            database,
            user,
            goal_a,
            goal_b,
            [edge_command(f"task-{goal_a.id[:8]}-1", f"task-{goal_b.id[:8]}-1")],
            key="u3",
            confirm=False,
        )
        with pytest.raises(GoalflowError) as excinfo:
            links.propose_unlink(database, user, link_id, idempotency_key="u3-p")
        assert excinfo.value.code == ErrorCode.GOAL_STATE_CONFLICT


class TestAccept:
    def _pending_proposal(self, database, user):
        goal_a, _plan_a = make_active_goal(database, user, key="ga")
        goal_b, _plan_b = make_active_goal(database, user, key="gb", title="读完两本书")
        task_a = f"task-{goal_a.id[:8]}-1"
        task_b = f"task-{goal_b.id[:8]}-1"
        link_id = make_link(database, user, goal_a, goal_b, [edge_command(task_a, task_b)], key="a")
        proposal = links.propose_unlink(database, user, link_id, idempotency_key="a-p")
        return link_id, task_a, task_b, proposal

    def test_accept_applies_atomically(self, database, user) -> None:
        link_id, _task_a, _task_b, proposal = self._pending_proposal(database, user)
        revision_before = planning_revision_of(database, user.user_id)

        view = links.accept_change_proposal(database, user, proposal.id, idempotency_key="a-acc")
        assert view.status == "applied"
        assert view.accepted_at is not None and view.applied_at is not None
        with database.read() as session:
            assert session.scalars(select(GoalLink).where(GoalLink.id == link_id)).one().status == "removed"
        assert _edge_count(database, link_id) == 0
        assert planning_revision_of(database, user.user_id) > revision_before

    def test_accept_is_idempotent(self, database, user) -> None:
        _link_id, _task_a, _task_b, proposal = self._pending_proposal(database, user)
        first = links.accept_change_proposal(database, user, proposal.id, idempotency_key="a-idem")
        again = links.accept_change_proposal(database, user, proposal.id, idempotency_key="a-idem")
        assert again.status == first.status == "applied"
        assert again.id == first.id

    def test_stale_proposal_rejected_and_marked(self, database, user) -> None:
        link_id, _task_a, _task_b, proposal = self._pending_proposal(database, user)
        # 提案生成后排期输入变化（如 availability 保存等任何 bump planning revision 的操作）。
        from goalflow.scheduling.models import UserPlanningState

        with database.write() as session:
            state = session.scalar(select(UserPlanningState).where(UserPlanningState.owner_id == user.user_id))
            assert state is not None
            state.revision += 5

        with pytest.raises(GoalflowError) as excinfo:
            links.accept_change_proposal(database, user, proposal.id, idempotency_key="a-stale")
        assert excinfo.value.code == ErrorCode.INPUT_STALE
        with database.read() as session:
            row = session.scalars(select(GoalLink).where(GoalLink.id == link_id)).one()
            assert row.status == "active"
        assert _edge_count(database, link_id) == 1
        # 提案被显式标记 stale，不沿用旧确认（18 号第 3 节）。
        reread = links.get_change_proposal(database, user, proposal.id)
        assert reread.status == "stale"

    def test_reject_keeps_link_and_edges(self, database, user) -> None:
        link_id, _task_a, _task_b, proposal = self._pending_proposal(database, user)
        view = links.reject_change_proposal(database, user, proposal.id, idempotency_key="a-rej")
        assert view.status == "rejected"
        with database.read() as session:
            assert session.scalars(select(GoalLink).where(GoalLink.id == link_id)).one().status == "active"
        assert _edge_count(database, link_id) == 1


class TestIsolation:
    def test_second_user_cannot_operate_foreign_resources(self, database, user) -> None:
        goal_a, _plan_a = make_active_goal(database, user, key="ga")
        goal_b, _plan_b = make_active_goal(database, user, key="gb", title="读完两本书")
        link_id = make_link(
            database,
            user,
            goal_a,
            goal_b,
            [edge_command(f"task-{goal_a.id[:8]}-1", f"task-{goal_b.id[:8]}-1")],
            key="iso",
        )
        proposal = links.propose_unlink(database, user, link_id, idempotency_key="iso-p")
        intruder = make_user(database, "intruder@local.dev")

        with pytest.raises(GoalflowError) as excinfo:
            links.propose_unlink(database, intruder, link_id, idempotency_key="iso-u1")
        assert excinfo.value.code == ErrorCode.NOT_FOUND
        with pytest.raises(GoalflowError) as excinfo:
            links.get_change_proposal(database, intruder, proposal.id)
        assert excinfo.value.code == ErrorCode.NOT_FOUND
        with pytest.raises(GoalflowError) as excinfo:
            links.confirm_goal_link(
                database,
                intruder,
                link_id,
                dependencies=[edge_command(f"task-{goal_a.id[:8]}-1", f"task-{goal_b.id[:8]}-1")],
                idempotency_key="iso-u2",
            )
        assert excinfo.value.code == ErrorCode.NOT_FOUND

    def test_proposal_cannot_reference_foreign_goal(self, database, user) -> None:
        foreign_goal, _plan = make_active_goal(database, user, key="ga")
        intruder = make_user(database, "intruder2@local.dev")
        with pytest.raises(GoalflowError) as excinfo:
            links.propose_goal_link(
                database,
                intruder,
                foreign_goal.id,
                target_goal_id=foreign_goal.id,
                dependencies=[],
                idempotency_key="iso2",
            )
        assert excinfo.value.code == ErrorCode.NOT_FOUND


class TestPauseImpactBackfill:
    def test_pause_reports_dependent_tasks_of_other_goals(self, database, user) -> None:
        from goalflow.goals import lifecycle

        goal_a, _plan_a = make_active_goal(database, user, key="ga")
        goal_b, _plan_b = make_active_goal(database, user, key="gb", title="读完两本书")
        task_a = f"task-{goal_a.id[:8]}-1"
        task_b = f"task-{goal_b.id[:8]}-1"
        make_link(database, user, goal_a, goal_b, [edge_command(task_a, task_b)], key="pb")

        view = lifecycle.pause_goal(database, user, goal_a.id, expected_revision=2, reason=None, idempotency_key="pb-1")
        assert view.dependency_check.value == "wired"
        assert [(item.goal_id, item.task_id) for item in view.affected_dependent_tasks] == [(goal_b.id, task_b)]
        assert view.affected_dependent_tasks[0].task_title == "轻松跑 20 分钟"

    def test_pause_without_dependents_reports_wired_and_empty(self, database, user) -> None:
        from goalflow.goals import lifecycle

        goal_a, _plan_a = make_active_goal(database, user, key="ga")
        view = lifecycle.pause_goal(database, user, goal_a.id, expected_revision=2, reason=None, idempotency_key="pb-2")
        assert view.dependency_check.value == "wired"
        assert view.affected_dependent_tasks == []


class TestSchedulingBackfill:
    def _snapshot_task(self, database, user, snapshot, task_id: str):
        return next(task for task in snapshot.tasks if task.task_id == task_id)

    def test_unmet_dependency_blocks_task_in_snapshot(self, database, user) -> None:
        goal_a, _plan_a = make_active_goal(database, user, key="ga")
        goal_b, _plan_b = make_active_goal(database, user, key="gb", title="读完两本书")
        task_a = f"task-{goal_a.id[:8]}-1"
        task_b = f"task-{goal_b.id[:8]}-1"
        make_link(database, user, goal_a, goal_b, [edge_command(task_a, task_b)], key="sb")

        snapshot = build_snapshot(database, user.user_id, "UTC", "2026-10-01")
        assert self._snapshot_task(database, user, snapshot, task_b).dependency_satisfied is False

        set_task_status(database, task_a, "completed")
        snapshot = build_snapshot(database, user.user_id, "UTC", "2026-10-01")
        assert self._snapshot_task(database, user, snapshot, task_b).dependency_satisfied is True

    def test_proposed_link_edges_do_not_block(self, database, user) -> None:
        goal_a, _plan_a = make_active_goal(database, user, key="ga")
        goal_b, _plan_b = make_active_goal(database, user, key="gb", title="读完两本书")
        task_b = f"task-{goal_b.id[:8]}-1"
        make_link(
            database,
            user,
            goal_a,
            goal_b,
            [edge_command(f"task-{goal_a.id[:8]}-1", task_b)],
            key="sb2",
            confirm=False,
        )
        snapshot = build_snapshot(database, user.user_id, "UTC", "2026-10-01")
        assert self._snapshot_task(database, user, snapshot, task_b).dependency_satisfied is True

    def test_verification_passed_edge_counts_as_unmet_until_t11(self, database, user) -> None:
        goal_a, _plan_a = make_active_goal(database, user, key="ga")
        goal_b, _plan_b = make_active_goal(database, user, key="gb", title="读完两本书")
        task_a = f"task-{goal_a.id[:8]}-1"
        task_b = f"task-{goal_b.id[:8]}-1"
        make_link(
            database,
            user,
            goal_a,
            goal_b,
            [edge_command(task_a, task_b, required_outcome="verification_passed")],
            key="sb3",
        )
        set_task_status(database, task_a, "completed")
        snapshot = build_snapshot(database, user.user_id, "UTC", "2026-10-01")
        # 验证记录归 T11：即使前驱已完成，"验证通过"边在落地前视为未满足（不伪装已检查）。
        assert self._snapshot_task(database, user, snapshot, task_b).dependency_satisfied is False
