import hashlib
import sqlite3
import time
from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from app import auth


ADMIN = {"username": "admin", "password": "admin-password-123"}
LEADER = {"username": "leader", "password": "leader-password-123"}


@pytest.fixture
def auth_client(monkeypatch, tmp_path):
    path = tmp_path / "auth.db"
    monkeypatch.setenv("NIDIN_ADMIN_USERNAME", ADMIN["username"])
    monkeypatch.setenv("NIDIN_ADMIN_PASSWORD", ADMIN["password"])
    monkeypatch.setenv("NIDIN_COOKIE_SECURE", "false")
    monkeypatch.setattr(auth, "_db_path_factory", None)
    monkeypatch.setattr(auth, "_attempts", {})
    auth.configure_auth(lambda: path)
    app = FastAPI()
    app.include_router(auth.router)

    @app.post("/protected")
    def protected(user=Depends(auth.require_user)):
        return {"owner": user.id}

    with TestClient(app) as client:
        yield client, path


def login(client, account=ADMIN):
    response = client.post("/auth/login", json=account)
    assert response.status_code == 200, response.text
    return response


def create_leader(client):
    response = client.post("/auth/accounts", json=LEADER)
    assert response.status_code == 201, response.text
    return response.json()


def test_login_cookie_hash_storage_and_logout(auth_client):
    client, path = auth_client
    assert client.get("/auth/me").status_code == 401
    response = login(client)
    user = response.json()
    assert set(user) == {"id", "username", "is_admin"}
    assert user["is_admin"] is True
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert "secure" not in cookie
    token = client.cookies.get(auth.COOKIE_NAME)
    with sqlite3.connect(path) as conn:
        password_hash = conn.execute(
            "SELECT password_hash FROM users"
        ).fetchone()[0]
        saved, expires = conn.execute(
            "SELECT token_hash, expires_at FROM login_sessions"
        ).fetchone()
    assert ADMIN["password"] not in password_hash
    assert password_hash.startswith("pbkdf2_sha256$")
    assert saved != token
    assert saved == hashlib.sha256(token.encode()).hexdigest()
    assert 604_790 < expires - time.time() <= auth.SESSION_SECONDS
    assert client.get("/auth/me").json() == user
    assert client.post("/auth/logout").status_code == 200
    assert client.cookies.get(auth.COOKIE_NAME) is None
    client.cookies.set(auth.COOKIE_NAME, token)
    assert client.get("/auth/me").status_code == 401


def test_expired_and_unknown_sessions_are_rejected(auth_client):
    client, path = auth_client
    login(client)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE login_sessions SET expires_at=?", (time.time() - 1,))
    assert client.get("/auth/me").status_code == 401
    client.cookies.clear()
    client.cookies.set(auth.COOKIE_NAME, "forged-token")
    assert client.post("/protected").status_code == 401


@pytest.mark.parametrize("password", ["", "too-short", "x" * 129])
def test_bootstrap_fails_closed_for_missing_or_invalid_password(
    auth_client, monkeypatch, password
):
    client, path = auth_client
    monkeypatch.setenv("NIDIN_ADMIN_PASSWORD", password)
    assert client.post("/auth/login", json=ADMIN).status_code == 503
    assert auth.first_admin_id() is None
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_bootstrap_requires_username_and_does_not_reset_existing_admin(
    auth_client, monkeypatch
):
    client, _ = auth_client
    monkeypatch.delenv("NIDIN_ADMIN_USERNAME")
    assert client.post("/auth/login", json=ADMIN).status_code == 503
    monkeypatch.setenv("NIDIN_ADMIN_USERNAME", "admin")
    original = login(client).json()
    monkeypatch.setenv("NIDIN_ADMIN_USERNAME", "replacement")
    monkeypatch.setenv("NIDIN_ADMIN_PASSWORD", "replacement-password")
    client.post("/auth/logout")
    assert login(client).json() == original
    assert auth.first_admin_id() == original["id"]
    monkeypatch.delenv("NIDIN_ADMIN_PASSWORD")
    assert login(client).json() == original


def test_accounts_require_admin_and_cannot_grant_admin(auth_client):
    client, _ = auth_client
    assert client.post("/auth/accounts", json=LEADER).status_code == 401
    login(client)
    leader = client.post(
        "/auth/accounts", json={**LEADER, "is_admin": True}
    ).json()
    assert leader["is_admin"] is False
    assert client.post("/auth/accounts", json=LEADER).status_code == 409
    client.post("/auth/logout")
    assert login(client, LEADER).json() == leader
    assert client.post("/auth/accounts", json=ADMIN).status_code == 403


@pytest.mark.parametrize("password", ["short", "x" * 129])
def test_new_accounts_validate_password_length(auth_client, password):
    client, _ = auth_client
    login(client)
    response = client.post(
        "/auth/accounts", json={**LEADER, "password": password}
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Origin": "http://testserver:1234"},
        {"Origin": "http://testserver@evil.example"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_csrf_blocks_login_logout_and_authenticated_mutations(
    auth_client, headers
):
    client, _ = auth_client
    assert client.post("/auth/login", json=ADMIN, headers=headers).status_code == 403
    login(client)
    for endpoint in ("/auth/logout", "/protected", "/auth/accounts"):
        assert client.post(endpoint, json=LEADER, headers=headers).status_code == 403
    assert client.get("/auth/me").status_code == 200


def test_same_origin_with_default_port_is_allowed(auth_client):
    client, _ = auth_client
    response = client.post(
        "/auth/login", json=ADMIN, headers={"Origin": "http://testserver:80"}
    )
    assert response.status_code == 200
    assert client.post(
        "/protected", headers={"Origin": "http://testserver"}
    ).status_code == 200


def test_secure_cookie_setting(auth_client, monkeypatch):
    client, _ = auth_client
    monkeypatch.setenv("NIDIN_COOKIE_SECURE", "true")
    assert "Secure" in login(client).headers["set-cookie"]
    assert "Secure" in client.post("/auth/logout").headers["set-cookie"]


def test_login_errors_do_not_disclose_account_existence(auth_client):
    client, _ = auth_client
    unknown = client.post(
        "/auth/login", json={**ADMIN, "username": "nobody"}
    )
    wrong = client.post(
        "/auth/login", json={**ADMIN, "password": "incorrect-password"}
    )
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


def test_rate_limit_by_username_and_bounded_key_capacity(auth_client, monkeypatch):
    client, _ = auth_client
    monkeypatch.setattr(auth, "_password_matches", lambda *_: False)
    for _ in range(10):
        assert client.post("/auth/login", json=ADMIN).status_code == 401
    assert client.post("/auth/login", json=ADMIN).status_code == 429
    auth._attempts.clear()
    monkeypatch.setattr(auth, "MAX_RATE_KEYS", 2)
    assert client.post("/auth/login", json=ADMIN).status_code == 401
    assert client.post("/auth/login", json=LEADER).status_code == 429
    assert len(auth._attempts) == 2


def test_rate_limit_by_ip_and_expired_entries_are_reclaimed(
    auth_client, monkeypatch
):
    client, _ = auth_client
    monkeypatch.setattr(auth, "_password_matches", lambda *_: False)
    now = [1000.0]
    monkeypatch.setattr(
        auth,
        "time",
        SimpleNamespace(time=time.time, monotonic=lambda: now[0]),
    )
    for i in range(30):
        response = client.post(
            "/auth/login", json={**ADMIN, "username": f"user-{i}"}
        )
        assert response.status_code == 401
    assert client.post("/auth/login", json=ADMIN).status_code == 429
    now[0] += auth.LOGIN_WINDOW_SECONDS + 1
    assert client.post("/auth/login", json=ADMIN).status_code == 401


def test_credentials_are_isolated_per_operator(auth_client):
    client, _ = auth_client
    admin = login(client).json()
    leader = create_leader(client)
    auth.set_leader_credentials(admin["id"], "admin-token", "upstream-admin")
    auth.set_leader_credentials(leader["id"], "leader-token", "upstream-leader")
    assert auth.get_leader_credentials(admin["id"]) == (
        "admin-token", "upstream-admin"
    )
    assert auth.get_leader_credentials(leader["id"]) == (
        "leader-token", "upstream-leader"
    )
    auth.clear_leader_credentials(leader["id"])
    assert auth.get_leader_credentials(leader["id"]) is None
    assert auth.get_leader_credentials(admin["id"])[0] == "admin-token"


def test_legacy_credentials_migrate_once_to_first_admin_only(auth_client):
    client, path = auth_client
    admin = login(client).json()
    leader = create_leader(client)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE leader_auth (id INTEGER PRIMARY KEY, "
            "token TEXT, user_id TEXT)"
        )
        conn.execute("INSERT INTO leader_auth VALUES (1, 'legacy', 'upstream')")
    with pytest.raises(ValueError):
        auth.migrate_legacy_credentials(leader["id"])
    auth.migrate_legacy_credentials(admin["id"])
    assert auth.get_leader_credentials(admin["id"]) == ("legacy", "upstream")
    assert auth.get_leader_credentials(leader["id"]) is None
    auth.clear_leader_credentials(admin["id"])
    auth.migrate_legacy_credentials(admin["id"])
    assert auth.get_leader_credentials(admin["id"]) is None
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT token FROM leader_auth").fetchone()[0] == "legacy"


def test_legacy_migration_preserves_existing_operator_credentials(auth_client):
    client, path = auth_client
    admin = login(client).json()
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE leader_auth (id INTEGER, token TEXT, user_id TEXT)")
        conn.execute("INSERT INTO leader_auth VALUES (1, 'old-token', 'old-user')")
    auth.set_leader_credentials(admin["id"], "new-token", "new-user")
    auth.migrate_legacy_credentials(admin["id"])
    assert auth.get_leader_credentials(admin["id"]) == ("new-token", "new-user")


def test_qr_ticket_flow_authorize_and_claim(auth_client):
    client, _ = auth_client
    ticket_res = client.post("/auth/qr/ticket")
    assert ticket_res.status_code == 200
    data = ticket_res.json()
    ticket = data["ticket"]
    assert "admin?auth_ticket=" in data["auth_url"]

    # Pending status initially
    status_res = client.get(f"/auth/qr/status?ticket={ticket}")
    assert status_res.status_code == 200
    assert status_res.json()["status"] == "pending"

    # Unauthorized client cannot authorize
    assert client.post("/auth/qr/authorize", json={"ticket": ticket}).status_code == 401

    # Login as leader to authorize
    login(client)
    auth_res = client.post("/auth/qr/authorize", json={"ticket": ticket})
    assert auth_res.status_code == 200
    assert auth_res.json()["ok"] is True

    # Status is now authorized
    status_res = client.get(f"/auth/qr/status?ticket={ticket}")
    assert status_res.status_code == 200
    assert status_res.json()["status"] == "authorized"
    assert status_res.json()["username"] == ADMIN["username"]

    # Claim using a separate client
    from fastapi.testclient import TestClient
    claim_client = TestClient(client.app)
    claim_res = claim_client.post("/auth/qr/claim", json={"ticket": ticket})
    assert claim_res.status_code == 200
    assert claim_res.json()["username"] == ADMIN["username"]
    assert claim_client.get("/auth/me").status_code == 200

    # Cannot claim twice
    assert claim_client.post("/auth/qr/claim", json={"ticket": ticket}).status_code == 400


def test_qr_quick_login_ticket(auth_client):
    client, _ = auth_client
    login(client)
    quick_res = client.post("/auth/qr/quick-ticket")
    assert quick_res.status_code == 200
    data = quick_res.json()
    ticket = data["ticket"]
    assert "admin?claim_ticket=" in data["auth_url"]

    from fastapi.testclient import TestClient
    mobile_client = TestClient(client.app)
    claim_res = mobile_client.post("/auth/qr/claim", json={"ticket": ticket})
    assert claim_res.status_code == 200
    assert claim_res.json()["username"] == ADMIN["username"]
    assert mobile_client.get("/auth/me").status_code == 200


def test_qr_ticket_expiry(auth_client, tmp_path):
    client, path = auth_client
    ticket_res = client.post("/auth/qr/ticket")
    ticket = ticket_res.json()["ticket"]

    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE qr_tickets SET expires_at=? WHERE ticket=?", (time.time() - 1, ticket))

    status_res = client.get(f"/auth/qr/status?ticket={ticket}")
    assert status_res.status_code == 200
    assert status_res.json()["status"] == "expired"

    login(client)
    assert client.post("/auth/qr/authorize", json={"ticket": ticket}).status_code == 400

