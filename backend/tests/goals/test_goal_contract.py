"""T04 契约面的行为保护。

本 PR 只交付契约形状（路由、schema、迁移），处理函数是桩。这里保护三件不会随业务
实现而改变的事：身份门槛、桩的错误形状、数据库 CHECK 对脏值的兜底。
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _insert_user(connection: object, user_id: str) -> None:
    connection.execute(
        text(
            "INSERT INTO users (id, account_identifier, role, status, timezone, revision, created_at, updated_at)"
            " VALUES (:id, 'user@example.com', 'user', 'active', 'UTC', 0, :now, :now)"
        ),
        {"id": user_id, "now": _now()},
    )


class TestStubEndpoints:
    """桩也必须站在身份依赖后面；契约要求的错误形状不能等实现时才补。"""

    def test_goals_require_a_session(self, client) -> None:
        response = client.get("/api/goals/00000000-0000-0000-0000-000000000001")
        assert response.status_code == 401
        body = response.json()
        assert body["code"] == "UNAUTHENTICATED"
        assert body["retryable"] is False

    def test_create_goal_over_http_is_idempotent(self, authed_client) -> None:
        """契约里"相同 key 重放返回同一资源"在 HTTP 层的行为（01 第 5 节）。"""
        payload = {"title": "三个月跑完十公里"}
        headers = {"Idempotency-Key": "t04-contract-stub"}
        first = authed_client.post("/api/goals", json=payload, headers=headers)
        assert first.status_code == 201, first.text
        body = first.json()
        assert body["status"] == "draft"
        second = authed_client.post("/api/goals", json=payload, headers=headers)
        assert second.status_code == 201
        assert second.json()["id"] == body["id"]


class TestGoalsCheckConstraints:
    """应用层状态机落地前，CHECK 是挡脏值的最后防线（03 第 1 节）。"""

    def test_goal_status_rejects_unknown_values(self, raw_engine) -> None:
        with raw_engine.begin() as connection:
            user_id = str(uuid.uuid4())
            _insert_user(connection, user_id)
            goal_id = str(uuid.uuid4())
            connection.execute(
                text(
                    "INSERT INTO goals (id, owner_id, title, domain, kind, status, review_period,"
                    " revision, created_at, updated_at)"
                    " VALUES (:id, :owner, '目标', 'general', 'achievement', 'draft', 'weekly',"
                    " 0, :now, :now)"
                ),
                {"id": goal_id, "owner": user_id, "now": _now()},
            )
            with pytest.raises(IntegrityError):
                connection.execute(text("UPDATE goals SET status = 'doing' WHERE id = :id"), {"id": goal_id})

    def test_completed_closure_is_rejected_for_maintenance_goals(self, raw_engine) -> None:
        """maintenance 永不进入 completed（D12）：这里验证库级兜底存在。"""
        with raw_engine.begin() as connection:
            user_id = str(uuid.uuid4())
            _insert_user(connection, user_id)
            goal_id = str(uuid.uuid4())
            connection.execute(
                text(
                    "INSERT INTO goals (id, owner_id, title, domain, kind, status, review_period,"
                    " revision, created_at, updated_at)"
                    " VALUES (:id, :owner, '保持体能', 'fitness', 'maintenance', 'active', 'weekly',"
                    " 0, :now, :now)"
                ),
                {"id": goal_id, "owner": user_id, "now": _now()},
            )
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "UPDATE goals SET status = 'completed', closed_at = :now,"
                        " closure_kind = 'completed' WHERE id = :id"
                    ),
                    {"id": goal_id, "now": _now()},
                )

    def test_all_goal_family_tables_exist(self, raw_engine) -> None:
        tables = set(inspect(raw_engine).get_table_names())
        expected = {
            "goals",
            "goal_profile_drafts",
            "goal_profiles",
            "planning_sessions",
            "route_sets",
            "routes",
            "plan_versions",
            "plan_phases",
            "plan_milestones",
            "task_batches",
            "tasks",
            "task_specs",
            "plan_task_memberships",
        }
        assert expected <= tables
