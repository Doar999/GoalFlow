"""排期 HTTP 端点测试：鉴权、契约形状与 generation 的 202 + 去重。"""

from scheduling_support import insert_goal
from sqlalchemy import text


def _db(app):
    from goalflow.api.dependencies import get_database

    return app.dependency_overrides[get_database]()


def _account_user_id(database, account_identifier: str) -> str:
    with database.read() as session:
        return session.scalar(
            text("SELECT id FROM users WHERE account_identifier = :account"),
            {"account": account_identifier},
        )


class TestAuth:
    def test_read_agenda_requires_login(self, client, today):
        response = client.get("/api/agendas/2026-09-24")
        assert response.status_code == 401

    def test_preferences_requires_login(self, client):
        response = client.put(
            "/api/goals/scheduling-preferences",
            json={"expected_revision": 0, "preferences": [{"goal_id": "g", "focus_status": "focused", "rank": 1}]},
        )
        assert response.status_code == 401


class TestAgendaEndpoints:
    def test_read_agenda_missing_state(self, authed_client, app, today):
        response = authed_client.get(f"/api/agendas/{today}")
        assert response.status_code == 200
        body = response.json()
        assert body["local_date"] == today
        assert body["planning_revision"] == 0
        assert body["current"] is None

    def test_availability_roundtrip(self, authed_client, today):
        week_start = _week_start(today)
        response = authed_client.put(
            "/api/availability",
            headers={"Idempotency-Key": "api-av-1"},
            json={"expected_revision": 0, "effective_from": week_start, "weekly_minutes": {"monday": 120}},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["version_no"] == 1
        assert body["weekly_minutes"]["monday"] == 120
        assert body["coordination_required"] is False

    def test_preferences_roundtrip(self, authed_client, app, today):
        database = _db(app)
        user_id = _account_user_id(database, "scheduling-api@local.dev")
        goal_id = insert_goal(database, user_id)
        response = authed_client.put(
            "/api/goals/scheduling-preferences",
            headers={"Idempotency-Key": "api-pref-1"},
            json={
                "expected_revision": 0,
                "preferences": [{"goal_id": goal_id, "focus_status": "focused", "rank": 1}],
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["planning_revision"] == 1
        assert body["preferences"][0]["focus_status"] == "focused"

    def test_generation_returns_202_with_job_and_dedupes(self, authed_client, app, today):
        response = authed_client.post(
            f"/api/agendas/{today}/generation",
            headers={"Idempotency-Key": "api-gen-1"},
            json={"planning_revision": 0},
        )
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["kind"] == "agenda_generation"
        assert body["status"] == "queued"

        published = app.state.published_job_ids
        assert published == [body["id"]]

        # 相同 revision 的重复提交返回原作业（E10），不再投递第二条消息。
        response2 = authed_client.post(
            f"/api/agendas/{today}/generation",
            headers={"Idempotency-Key": "api-gen-2"},
            json={"planning_revision": 0},
        )
        assert response2.status_code == 202
        assert response2.json()["id"] == body["id"]
        assert app.state.published_job_ids == [body["id"]]


def _week_start(iso_day: str) -> str:
    from datetime import date, timedelta

    day = date.fromisoformat(iso_day)
    return (day - timedelta(days=day.weekday())).isoformat()
