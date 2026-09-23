"""账号 HTTP 接口：Cookie、状态码、错误体、来源校验与用户隔离。"""

from dataclasses import replace

from auth_support import COOKIE, ORIGIN, PASSWORD, browser, build_app, login, register
from fastapi import FastAPI

from goalflow.auth.service import AuthConfig, AuthService
from goalflow.db.session import Database

NEW_PASSWORD = "a brand new passphrase"


def _set_cookie(response) -> str:
    return response.headers["set-cookie"]


def test_register_sets_an_httponly_lax_cookie_and_never_returns_the_token(client):
    response = register(client, "Alice", timezone="Asia/Shanghai")

    assert response.status_code == 201
    header = _set_cookie(response)
    assert header.startswith(f"{COOKIE}=")
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert "Path=/" in header
    assert "Secure" not in header  # 本地 http 环境
    token = client.cookies[COOKIE]
    assert token not in response.text

    body = response.json()
    assert body["user"]["account_identifier"] == "alice"
    assert body["user"]["role"] == "user"
    assert body["user"]["timezone"] == "Asia/Shanghai"

    session = client.get("/api/auth/session")
    assert session.status_code == 200
    assert session.json()["user"]["id"] == body["user"]["id"]
    assert token not in session.text


def test_production_cookie_uses_host_prefix_and_secure(service: AuthService, database: Database, config: AuthConfig):
    https_origin = "https://testserver"
    production = AuthService(database, replace(config, secure_cookies=True, public_origin=https_origin))
    app = build_app(production)
    client = browser(app)
    client.headers["Origin"] = https_origin

    response = register(client, "alice")

    header = _set_cookie(response)
    assert header.startswith("__Host-goalflow_session=")
    assert "Secure" in header
    assert "Domain" not in header


def test_duplicate_registration_is_rejected_without_a_second_session(client):
    assert register(client, "alice").status_code == 201

    other = browser(client.app)
    response = register(other, "ALICE")

    assert response.status_code == 409
    assert response.json()["code"] == "ACCOUNT_IDENTIFIER_UNAVAILABLE"
    assert "set-cookie" not in response.headers


def test_closed_registration_is_consistent_and_existing_users_can_still_log_in(
    service: AuthService, database: Database, config: AuthConfig
):
    open_client = browser(build_app(service))
    register(open_client, "alice")

    closed = build_app(AuthService(database, replace(config, allow_registration=False)))
    client = browser(closed)

    assert client.get("/api/auth/registration").json() == {"registration_open": False}
    response = register(client, "bob")
    assert response.status_code == 403
    assert response.json()["code"] == "REGISTRATION_CLOSED"
    assert login(client, "alice").status_code == 200


def test_registration_status_is_open_by_default(client):
    assert client.get("/api/auth/registration").json() == {"registration_open": True}


def test_login_issues_a_new_session_and_revokes_the_one_it_replaces(client):
    register(client, "alice")
    before = client.cookies[COOKIE]

    response = login(client, "alice")

    assert response.status_code == 200
    after = client.cookies[COOKIE]
    assert after != before
    stale = browser(client.app)
    stale.cookies.set(COOKIE, before)
    assert stale.get("/api/auth/session").status_code == 401


def test_login_failure_uses_a_generic_error(client):
    register(client, "alice")

    wrong = login(client, "alice", "not the password")
    missing = login(client, "nobody", PASSWORD)

    assert wrong.status_code == missing.status_code == 401
    assert wrong.json()["code"] == missing.json()["code"] == "INVALID_CREDENTIALS"
    assert wrong.json()["message"] == missing.json()["message"]


def test_logout_revokes_the_session_and_clears_the_cookie(client):
    register(client, "alice")
    token = client.cookies[COOKIE]

    response = client.post("/api/auth/logout")

    assert response.status_code == 204
    assert f'{COOKIE}=""' in _set_cookie(response) or "Max-Age=0" in _set_cookie(response)
    replay = browser(client.app)
    replay.cookies.set(COOKIE, token)
    assert replay.get("/api/auth/session").status_code == 401
    # 已退出再退出，仍然 204。
    assert browser(client.app).post("/api/auth/logout").status_code == 204


def test_logout_all_revokes_every_device(client):
    register(client, "alice")
    phone = browser(client.app)
    login(phone, "alice")

    assert client.post("/api/auth/logout-all").status_code == 204

    assert phone.get("/api/auth/session").status_code == 401
    assert client.get("/api/auth/session").status_code == 401


def test_change_password_rotates_the_cookie_and_logs_out_other_devices(client):
    register(client, "alice")
    old = client.cookies[COOKIE]
    phone = browser(client.app)
    login(phone, "alice")

    response = client.post(
        "/api/auth/password/change",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 200
    assert client.cookies[COOKIE] != old
    assert client.get("/api/auth/session").status_code == 200
    assert phone.get("/api/auth/session").status_code == 401
    assert login(browser(client.app), "alice", NEW_PASSWORD).status_code == 200


def test_reset_with_token_works_once_without_logging_in(client, service: AuthService):
    register(client, "alice")
    reset = service.issue_password_reset_token("alice", issued_by="test")
    anonymous = browser(client.app)

    response = anonymous.post(
        "/api/auth/password/reset-with-token",
        json={"token": reset.token, "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 204
    assert not anonymous.cookies.get(COOKIE)
    assert client.get("/api/auth/session").status_code == 401
    replay = anonymous.post(
        "/api/auth/password/reset-with-token",
        json={"token": reset.token, "new_password": "yet another passphrase"},
    )
    assert replay.status_code == 401
    assert replay.json()["code"] == "INVALID_CREDENTIALS"


def test_users_only_ever_see_their_own_session(app: FastAPI):
    alice = browser(app)
    bob = browser(app)
    alice_id = register(alice, "alice").json()["user"]["id"]
    bob_id = register(bob, "bob").json()["user"]["id"]

    assert alice.get("/api/auth/session").json()["user"]["id"] == alice_id
    assert bob.get("/api/auth/session").json()["user"]["id"] == bob_id

    assert bob.post("/api/auth/logout-all").status_code == 204
    assert alice.get("/api/auth/session").status_code == 200


def test_forged_and_tampered_cookies_are_rejected(client):
    register(client, "alice")
    token = client.cookies[COOKIE]

    for forged in ("", "not-a-token", token[:-1] + ("A" if token[-1] != "A" else "B"), "x" * 5000):
        probe = browser(client.app)
        probe.cookies.set(COOKIE, forged)
        response = probe.get("/api/auth/session")
        assert response.status_code == 401
        assert response.json()["code"] == "UNAUTHENTICATED"


def test_write_requests_from_other_origins_are_refused(app: FastAPI):
    victim = browser(app)
    register(victim, "alice")
    token = victim.cookies[COOKIE]

    for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"}, {}):
        attacker = browser(app)
        attacker.headers.pop("Origin")
        attacker.headers.update(headers)
        attacker.cookies.set(COOKIE, token)
        response = attacker.post("/api/auth/logout-all")
        assert response.status_code == 403, headers
        assert response.json()["code"] == "FORBIDDEN"

    assert victim.get("/api/auth/session").status_code == 200


def test_login_csrf_is_refused_too(app: FastAPI):
    register(browser(app), "alice")
    attacker = browser(app, Origin="https://evil.example")

    assert login(attacker, "alice").status_code == 403


def test_same_origin_fetch_without_origin_header_is_accepted(app: FastAPI):
    register(browser(app), "alice")
    client = browser(app, **{"Sec-Fetch-Site": "same-origin"})
    client.headers.pop("Origin")

    assert login(client, "alice").status_code == 200


def test_reads_do_not_require_an_origin_header(client):
    register(client, "alice")
    client.headers.pop("Origin")

    assert client.get("/api/auth/session").status_code == 200


def test_rate_limited_response_carries_retry_after(client):
    for n in range(5):
        assert register(client, f"user-{n}").status_code == 201

    response = register(client, "user-5")

    assert response.status_code == 429
    body = response.json()
    assert body["code"] == "RATE_LIMITED"
    assert body["retryable"] is True
    assert response.headers["Retry-After"] == str(body["details"]["retry_after_seconds"])


def test_validation_errors_do_not_echo_the_password(client):
    secret = "p" * 1100  # 超过传输层上限，由 Pydantic 拒绝
    response = register(client, "alice", password=secret)

    assert response.status_code == 422
    assert secret not in response.text


def test_rule_violations_name_the_field(client):
    response = register(client, "alice", password="short")

    assert response.status_code == 422
    assert response.json()["details"]["field"] == "password"


def test_origin_is_compared_exactly(app: FastAPI):
    client = browser(app, Origin=ORIGIN + "/")

    assert register(client, "alice").status_code == 403
