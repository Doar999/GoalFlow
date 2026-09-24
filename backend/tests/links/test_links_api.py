"""目标关联 HTTP 端点测试：鉴权、契约形状与影响分析读取。"""

from datetime import UTC, datetime

from links_support import planning_revision_of


def _db(app):
    from goalflow.api.dependencies import get_database

    return app.dependency_overrides[get_database]()


def _account_user_id(database, account_identifier: str) -> str:
    from sqlalchemy import text

    with database.read() as session:
        return session.scalar(
            text("SELECT id FROM users WHERE account_identifier = :account"),
            {"account": account_identifier},
        )


def _seed_two_goals(app):
    """用业务层支撑铺两个活动目标与任务，返回 (database, user, goal_a, goal_b, task_a, task_b)。"""
    from links_support import make_active_goal

    from goalflow.auth.service import CurrentUser
    from goalflow.contracts.enums import UserRole

    database = _db(app)
    user_id = _account_user_id(database, "links-contract@local.dev")
    moment = datetime.now(UTC)
    user = CurrentUser(
        user_id=user_id,
        account_identifier="links-contract@local.dev",
        role=UserRole.USER,
        timezone="UTC",
        session_id="api",
        session_created_at=moment,
        session_expires_at=moment,
    )
    goal_a, _plan_a = make_active_goal(database, user, key="api-ga")
    goal_b, _plan_b = make_active_goal(database, user, key="api-gb", title="读完两本书")
    return database, user, goal_a.id, goal_b.id, f"task-{goal_a.id[:8]}-1", f"task-{goal_b.id[:8]}-1"


class TestAuth:
    def test_propose_requires_login(self, client):
        response = client.post(
            "/api/goals/some-goal/link-proposals",
            json={"target_goal_id": "other", "dependencies": []},
        )
        assert response.status_code == 401


class TestLinkEndpoints:
    @staticmethod
    def _edges(task_a: str, task_b: str) -> list[dict[str, str]]:
        return [
            {
                "predecessor_task_id": task_a,
                "successor_task_id": task_b,
                "required_outcome": "execution_completed",
            }
        ]

    def test_propose_and_confirm_roundtrip(self, authed_client, app):
        database, _user, goal_a, goal_b, task_a, task_b = _seed_two_goals(app)
        revision_before = planning_revision_of(database, _user.user_id)

        response = authed_client.post(
            f"/api/goals/{goal_a}/link-proposals",
            headers={"Idempotency-Key": "api-propose-1"},
            json={"target_goal_id": goal_b, "dependencies": self._edges(task_a, task_b)},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "proposed"
        assert body["confirmed_at"] is None
        assert body["goal_a_id"] == min(goal_a, goal_b)
        link_id = body["id"]

        confirm = authed_client.post(
            f"/api/goal-links/{link_id}/confirm",
            headers={"Idempotency-Key": "api-confirm-1"},
            json={"dependencies": self._edges(task_a, task_b)},
        )
        assert confirm.status_code == 200, confirm.text
        assert confirm.json()["status"] == "active"
        assert confirm.json()["confirmed_at"] is not None
        assert planning_revision_of(database, _user.user_id) > revision_before

    def test_propose_without_dependencies_is_rejected(self, authed_client, app):
        _database, _user, goal_a, goal_b, _task_a, _task_b = _seed_two_goals(app)
        response = authed_client.post(
            f"/api/goals/{goal_a}/link-proposals",
            headers={"Idempotency-Key": "api-propose-empty"},
            json={"target_goal_id": goal_b, "dependencies": []},
        )
        assert response.status_code == 422

    def test_propose_unknown_goal_returns_404(self, authed_client):
        response = authed_client.post(
            "/api/goals/00000000-0000-0000-0000-000000000000/link-proposals",
            headers={"Idempotency-Key": "api-propose-404"},
            json={
                "target_goal_id": "00000000-0000-0000-0000-000000000001",
                "dependencies": [
                    {
                        "predecessor_task_id": "00000000-0000-0000-0000-000000000002",
                        "successor_task_id": "00000000-0000-0000-0000-000000000003",
                        "required_outcome": "execution_completed",
                    }
                ],
            },
        )
        assert response.status_code == 404

    def test_unlink_proposal_read_and_accept(self, authed_client, app):
        _database, _user, goal_a, goal_b, task_a, task_b = _seed_two_goals(app)
        edges = self._edges(task_a, task_b)
        link_id = authed_client.post(
            f"/api/goals/{goal_a}/link-proposals",
            headers={"Idempotency-Key": "api-u-p"},
            json={"target_goal_id": goal_b, "dependencies": edges},
        ).json()["id"]
        authed_client.post(
            f"/api/goal-links/{link_id}/confirm",
            headers={"Idempotency-Key": "api-u-c"},
            json={"dependencies": edges},
        )

        proposal = authed_client.post(
            f"/api/goal-links/{link_id}/unlink-proposals",
            headers={"Idempotency-Key": "api-u-up"},
            json={},
        )
        assert proposal.status_code == 201, proposal.text
        body = proposal.json()
        assert body["change_class"] == "confirmation_required"
        assert body["status"] == "pending"
        assert len(body["impact"]["unsatisfied_edges"]) == 1
        assert body["impact"]["unsatisfied_edges"][0]["successor_task_id"] == task_b

        read = authed_client.get(f"/api/change-proposals/{body['id']}")
        assert read.status_code == 200
        assert read.json()["impact"]["unsatisfied_edges"][0]["predecessor_task_id"] == task_a

        accept = authed_client.post(
            f"/api/change-proposals/{body['id']}/accept",
            headers={"Idempotency-Key": "api-u-acc"},
            json={},
        )
        assert accept.status_code == 200, accept.text
        assert accept.json()["status"] == "applied"

        reject_late = authed_client.post(
            f"/api/change-proposals/{body['id']}/reject",
            headers={"Idempotency-Key": "api-u-rej"},
            json={},
        )
        assert reject_late.status_code == 409
