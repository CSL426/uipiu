"""Local operator accounts; upstream Nidin credentials belong to one operator."""

from collections import deque
from contextlib import contextmanager
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import sqlite3
from threading import Lock
import time
from typing import Callable, Iterator
from urllib.parse import urlsplit

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from app import credential_crypto


COOKIE_NAME = "nidin_session"
SESSION_SECONDS = 7 * 24 * 60 * 60
QR_TICKET_SECONDS = 300
PASSWORD_ITERATIONS = 600_000
LOGIN_WINDOW_SECONDS = 15 * 60
MAX_RATE_KEYS = 4096
_db_path_factory: Callable[[], Path] | None = None
_attempts: dict[str, deque[float]] = {}
_attempts_lock = Lock()
router = APIRouter(prefix="/auth", tags=["auth"])

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS login_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_sessions_expiry
ON login_sessions(expires_at);
CREATE TABLE IF NOT EXISTS leader_credentials (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    token TEXT NOT NULL,
    nidin_user_id TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS leader_nidin_logins (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    account TEXT NOT NULL,
    password_enc TEXT NOT NULL,
    device_id TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS nidin_account_links (
    nidin_user_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    linked_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS qr_tickets (
    ticket TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_qr_tickets_expiry
ON qr_tickets(expires_at);
CREATE TABLE IF NOT EXISTS leader_credential_revisions (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS nidin_credential_pairings (
    flow_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_token_hash TEXT NOT NULL,
    origin TEXT NOT NULL,
    state_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    initial_revision INTEGER NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    lease_expires_at REAL NOT NULL DEFAULT 0,
    confirmed_user_id TEXT NOT NULL DEFAULT '',
    confirmed_masked_token TEXT NOT NULL DEFAULT '',
    confirmed_revision INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pairings_user_session
ON nidin_credential_pairings(user_id, session_token_hash);
CREATE INDEX IF NOT EXISTS idx_pairings_expiry
ON nidin_credential_pairings(expires_at);
CREATE TABLE IF NOT EXISTS auth_migrations (
    name TEXT PRIMARY KEY,
    applied_at REAL NOT NULL
);
"""


class AuthUser(BaseModel):
    id: str
    username: str
    is_admin: bool


class QRTicketResponse(BaseModel):
    ticket: str
    expires_at: float
    auth_url: str


class QRTicketStatusResponse(BaseModel):
    status: str
    username: str | None = None


class QRTicketRequest(BaseModel):
    ticket: str = Field(min_length=16, max_length=128)


class AccountInput(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        value = value.strip().casefold()
        if not value or len(value) > 64 or any(ord(c) < 32 for c in value):
            raise ValueError("帳號格式無效")
        return value


def configure_auth(db_path_factory: Callable[[], Path]) -> None:
    global _db_path_factory
    _db_path_factory = db_path_factory
    with _attempts_lock:
        _attempts.clear()


def _password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), PASSWORD_ITERATIONS
    ).hex()
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${digest}"


def _password_matches(password: str, encoded: str) -> bool:
    algorithm, iterations, salt, expected = encoded.split("$")
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), int(iterations)
    ).hex()
    return hmac.compare_digest(digest, expected)


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    if _db_path_factory is None:
        raise RuntimeError("configure_auth must be called before using auth")
    path = Path(_db_path_factory())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.executescript(_SCHEMA)
        # Serialize first-account creation across concurrent workers.
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            username = os.getenv("NIDIN_ADMIN_USERNAME", "")
            password = os.getenv("NIDIN_ADMIN_PASSWORD", "")
            try:
                account = AccountInput(username=username, password=password)
            except ValueError:
                account = None
            if account is not None:
                conn.execute(
                    "INSERT INTO users VALUES (?, ?, ?, 1, ?)",
                    (
                        secrets.token_urlsafe(18),
                        account.username,
                        _password_hash(account.password),
                        time.time(),
                    ),
                )
        conn.commit()
        with conn:
            yield conn
    finally:
        conn.close()


auth_connection = _connection


@contextmanager
def _write_connection() -> Iterator[sqlite3.Connection]:
    """需要「先讀後寫」且不能被插隊的流程用這個。

    SQLite 預設 deferred：第一個 SELECT 只開讀取交易，別的 writer 可以插進來 commit，
    之後我們再寫就覆蓋掉對方（lost update）。BEGIN IMMEDIATE 一開始就取得寫入鎖，
    讓檢查與寫入落在同一個不可分割的交易裡。
    """
    with _connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        yield conn


auth_write_connection = _write_connection


def mask_token(token: str) -> str:
    return f"{token[:6]}...{token[-4:]}" if len(token) > 10 else "***"


def get_user(user_id: str) -> AuthUser | None:
    with _connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return _as_user(row) if row else None


def first_admin_id() -> str | None:
    with _connection() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE is_admin=1 "
            "ORDER BY created_at, rowid LIMIT 1"
        ).fetchone()
    return str(row["id"]) if row else None


def _as_user(row: sqlite3.Row) -> AuthUser:
    return AuthUser(
        id=row["id"], username=row["username"], is_admin=bool(row["is_admin"])
    )


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _origin(value: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Invalid origin")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, parsed.hostname.lower(), port


def format_origin(scheme: str, host: str, port: int | None) -> str:
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443) or port is None:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def normalize_request_origin(request: Request) -> str:
    origin = request.headers.get("origin")
    if origin:
        scheme, host, port = _origin(origin)
    else:
        scheme, host, port = _origin(str(request.base_url))
    return format_origin(scheme, host, port)


def _check_csrf(request: Request) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        raise HTTPException(403, "不接受跨站請求")
    origin = request.headers.get("origin")
    if origin is not None:
        try:
            matches = _origin(origin) == _origin(str(request.base_url))
        except ValueError:
            matches = False
        if not matches:
            raise HTTPException(403, "不接受跨站請求")


def get_current_session(request: Request) -> tuple[AuthUser, str] | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token or len(token) > 256:
        return None
    token_hash = _token_hash(token)
    with _connection() as conn:
        row = conn.execute(
            "SELECT users.* FROM login_sessions "
            "JOIN users ON users.id=login_sessions.user_id "
            "WHERE token_hash=? AND expires_at>?",
            (token_hash, time.time()),
        ).fetchone()
    if not row:
        return None
    return _as_user(row), token_hash


def current_user(request: Request) -> AuthUser | None:
    session = get_current_session(request)
    return session[0] if session else None


def require_user(
    request: Request, user: AuthUser | None = Depends(current_user)
) -> AuthUser:
    if user is None:
        raise HTTPException(401, "請先登入")
    _check_csrf(request)
    return user


def require_session(
    request: Request,
    session: tuple[AuthUser, str] | None = Depends(get_current_session),
) -> tuple[AuthUser, str]:
    if session is None:
        raise HTTPException(401, "請先登入")
    _check_csrf(request)
    return session


def require_admin(user: AuthUser = Depends(require_user)) -> AuthUser:
    if not user.is_admin:
        raise HTTPException(403, "需要管理員權限")
    return user


def _limit_login(request: Request, username: str) -> None:
    now = time.monotonic()
    # Trust only the server's resolved peer, never arbitrary forwarding headers.
    peer = request.client.host if request.client else "unknown"
    keys = ((f"ip:{peer}", 30), (f"user:{username}", 10))
    with _attempts_lock:
        for key in list(_attempts):
            attempts = _attempts[key]
            while attempts and attempts[0] <= now - LOGIN_WINDOW_SECONDS:
                attempts.popleft()
            if not attempts:
                del _attempts[key]
        missing = sum(key not in _attempts for key, _ in keys)
        if len(_attempts) + missing > MAX_RATE_KEYS or any(
            len(_attempts.get(key, ())) >= limit for key, limit in keys
        ):
            raise HTTPException(
                429, "登入嘗試過於頻繁，請稍後再試", headers={"Retry-After": "900"}
            )
        for key, _ in keys:
            _attempts.setdefault(key, deque()).append(now)


@router.get("/me", response_model=AuthUser)
def me(user: AuthUser = Depends(require_user)) -> AuthUser:
    return user


@router.post("/login", response_model=AuthUser)
def login(account: AccountInput, request: Request, response: Response) -> AuthUser:
    _check_csrf(request)
    _limit_login(request, account.username)
    with _connection() as conn:
        if not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            raise HTTPException(503, "管理員帳號尚未設定")
        row = conn.execute(
            "SELECT * FROM users WHERE username=?", (account.username,)
        ).fetchone()
        # Unknown accounts still perform the same expensive password derivation.
        encoded = row["password_hash"] if row else (
            f"pbkdf2_sha256${PASSWORD_ITERATIONS}${'00' * 16}${'00' * 32}"
        )
        matches = _password_matches(account.password, encoded)
        if row is None or not matches:
            raise HTTPException(401, "帳號或密碼錯誤")
        _issue_session(conn, response, row["id"])
        return _as_user(row)


def _issue_session(
    conn: sqlite3.Connection, response: Response, user_id: str
) -> None:
    token = secrets.token_urlsafe(32)
    conn.execute("DELETE FROM login_sessions WHERE expires_at<=?", (time.time(),))
    conn.execute(
        "INSERT INTO login_sessions VALUES (?, ?, ?)",
        (_token_hash(token), user_id, time.time() + SESSION_SECONDS),
    )
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_SECONDS,
        httponly=True,
        secure=os.getenv("NIDIN_COOKIE_SECURE", "false").lower() == "true",
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"


def login_linked_nidin_account(
    nidin_user_id: str, response: Response
) -> AuthUser | None:
    """以已連結的 Nidin 會員身分登入本站；未連結回 None。"""
    with _connection() as conn:
        row = conn.execute(
            "SELECT users.* FROM nidin_account_links "
            "JOIN users ON users.id = nidin_account_links.user_id "
            "WHERE nidin_account_links.nidin_user_id=?",
            (nidin_user_id,),
        ).fetchone()
        if row is None:
            return None
        _issue_session(conn, response, row["id"])
        return _as_user(row)


def ensure_owner_admin() -> str:
    """回傳第一位管理員 id；站上還沒有任何管理員時建立一個。

    給 NIDIN_OWNER_PHONE 用：站長只用 Nidin 帳號登入，本站密碼設成沒人知道的
    隨機值，避免多一組要記的帳密。
    """
    with _connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id FROM users WHERE is_admin=1 ORDER BY created_at, rowid LIMIT 1"
        ).fetchone()
        if row is not None:
            return str(row["id"])
        user_id = secrets.token_urlsafe(18)
        username = os.getenv("NIDIN_ADMIN_USERNAME", "").strip().casefold() or "admin"
        conn.execute(
            "INSERT INTO users VALUES (?, ?, ?, 1, ?)",
            (user_id, username, _password_hash(secrets.token_urlsafe(32)), time.time()),
        )
        return user_id


def link_nidin_account(user_id: str, nidin_user_id: str) -> None:
    """一個本站帳號對一個 Nidin 會員。

    呼叫端必須已用 Nidin 密碼驗證過 nidin_user_id（由 Nidin 伺服器回傳），
    不能用使用者自填或擴充功能從 localStorage 讀到的值，否則任何人都能
    把別人的 Nidin ID 連到自己帳號、或把自己的 ID 連到別人帳號。
    """
    with _connection() as conn:
        conn.execute(
            "DELETE FROM nidin_account_links WHERE user_id=? OR nidin_user_id=?",
            (user_id, nidin_user_id),
        )
        conn.execute(
            "INSERT INTO nidin_account_links VALUES (?, ?, ?)",
            (nidin_user_id, user_id, time.time()),
        )


def has_nidin_account_link(user_id: str) -> bool:
    with _connection() as conn:
        return conn.execute(
            "SELECT 1 FROM nidin_account_links WHERE user_id=?", (user_id,)
        ).fetchone() is not None


def unlink_nidin_account(user_id: str) -> None:
    with _connection() as conn:
        conn.execute("DELETE FROM nidin_account_links WHERE user_id=?", (user_id,))


limit_login = _limit_login
check_csrf = _check_csrf


@router.post("/logout")
def logout(request: Request, response: Response) -> dict[str, bool]:
    _check_csrf(request)
    token = request.cookies.get(COOKIE_NAME)
    if token:
        with _connection() as conn:
            conn.execute(
                "DELETE FROM login_sessions WHERE token_hash=?",
                (_token_hash(token),),
            )
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        httponly=True,
        secure=os.getenv("NIDIN_COOKIE_SECURE", "false").lower() == "true",
        samesite="strict",
    )
    return {"ok": True}


@router.post("/accounts", response_model=AuthUser, status_code=201)
def create_account(
    account: AccountInput, admin: AuthUser = Depends(require_admin)
) -> AuthUser:
    user = AuthUser(
        id=secrets.token_urlsafe(18), username=account.username, is_admin=False
    )
    with _connection() as conn:
        try:
            conn.execute(
                "INSERT INTO users VALUES (?, ?, ?, 0, ?)",
                (
                    user.id,
                    user.username,
                    _password_hash(account.password),
                    time.time(),
                ),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "帳號已存在") from None
    return user


@router.post("/qr/ticket", response_model=QRTicketResponse)
def create_qr_ticket(request: Request) -> QRTicketResponse:
    _check_csrf(request)
    ticket = secrets.token_urlsafe(32)
    now = time.time()
    expires_at = now + QR_TICKET_SECONDS
    with _connection() as conn:
        conn.execute("DELETE FROM qr_tickets WHERE expires_at <= ?", (now,))
        conn.execute(
            "INSERT INTO qr_tickets (ticket, user_id, status, created_at, expires_at) "
            "VALUES (?, NULL, 'pending', ?, ?)",
            (ticket, now, expires_at),
        )
    auth_url = f"{str(request.base_url).rstrip('/')}/admin?auth_ticket={ticket}"
    return QRTicketResponse(ticket=ticket, expires_at=expires_at, auth_url=auth_url)


@router.get("/qr/status", response_model=QRTicketStatusResponse)
def get_qr_ticket_status(ticket: str) -> QRTicketStatusResponse:
    now = time.time()
    with _connection() as conn:
        row = conn.execute(
            "SELECT qr_tickets.*, users.username FROM qr_tickets "
            "LEFT JOIN users ON users.id = qr_tickets.user_id "
            "WHERE ticket = ?",
            (ticket,),
        ).fetchone()
        if not row:
            return QRTicketStatusResponse(status="expired")
        if row["expires_at"] <= now:
            conn.execute("UPDATE qr_tickets SET status='expired' WHERE ticket=?", (ticket,))
            return QRTicketStatusResponse(status="expired")
        return QRTicketStatusResponse(
            status=row["status"],
            username=row["username"] if row["username"] else None,
        )


@router.post("/qr/authorize")
def authorize_qr_ticket(
    payload: QRTicketRequest,
    request: Request,
    user: AuthUser = Depends(require_user),
) -> dict[str, str | bool]:
    _check_csrf(request)
    now = time.time()
    with _connection() as conn:
        row = conn.execute(
            "SELECT * FROM qr_tickets WHERE ticket=?", (payload.ticket,)
        ).fetchone()
        if not row or row["expires_at"] <= now or row["status"] != "pending":
            raise HTTPException(400, "QR 碼已失效或過期，請重新整理取得最新 QR 碼")
        conn.execute(
            "UPDATE qr_tickets SET user_id=?, status='authorized' WHERE ticket=?",
            (user.id, payload.ticket),
        )
    return {"ok": True, "username": user.username}


@router.post("/qr/claim", response_model=AuthUser)
def claim_qr_ticket(
    payload: QRTicketRequest,
    request: Request,
    response: Response,
) -> AuthUser:
    _check_csrf(request)
    now = time.time()
    with _connection() as conn:
        row = conn.execute(
            "SELECT qr_tickets.*, users.* FROM qr_tickets "
            "JOIN users ON users.id = qr_tickets.user_id "
            "WHERE qr_tickets.ticket=?",
            (payload.ticket,),
        ).fetchone()
        if not row or row["expires_at"] <= now or row["status"] != "authorized":
            raise HTTPException(400, "登入授權已失效或過期，請重新掃碼")
        conn.execute(
            "UPDATE qr_tickets SET status='claimed' WHERE ticket=?",
            (payload.ticket,),
        )
        _issue_session(conn, response, row["id"])
        return _as_user(row)


@router.post("/qr/quick-ticket", response_model=QRTicketResponse)
def create_quick_login_ticket(
    request: Request,
    user: AuthUser = Depends(require_user),
) -> QRTicketResponse:
    _check_csrf(request)
    ticket = secrets.token_urlsafe(32)
    now = time.time()
    expires_at = now + QR_TICKET_SECONDS
    with _connection() as conn:
        conn.execute("DELETE FROM qr_tickets WHERE expires_at <= ?", (now,))
        conn.execute(
            "INSERT INTO qr_tickets (ticket, user_id, status, created_at, expires_at) "
            "VALUES (?, ?, 'authorized', ?, ?)",
            (ticket, user.id, now, expires_at),
        )
    auth_url = f"{str(request.base_url).rstrip('/')}/admin?claim_ticket={ticket}"
    return QRTicketResponse(ticket=ticket, expires_at=expires_at, auth_url=auth_url)


def get_credential_revision(
    user_id: str, conn: sqlite3.Connection | None = None
) -> int:
    if conn is not None:
        row = conn.execute(
            "SELECT revision FROM leader_credential_revisions WHERE user_id=?",
            (user_id,),
        ).fetchone()
        return int(row["revision"]) if row else 0
    with _connection() as local_conn:
        row = local_conn.execute(
            "SELECT revision FROM leader_credential_revisions WHERE user_id=?",
            (user_id,),
        ).fetchone()
    return int(row["revision"]) if row else 0


def get_leader_credentials(user_id: str) -> tuple[str, str] | None:
    with _connection() as conn:
        row = conn.execute(
            "SELECT token, nidin_user_id FROM leader_credentials WHERE user_id=?",
            (user_id,),
        ).fetchone()
    return (str(row["token"]), str(row["nidin_user_id"])) if row else None


def set_leader_credentials(
    user_id: str, token: str, nidin_user_id: str = "", *, keep_login: bool = False
) -> None:
    """keep_login=False 會一併刪掉記住的 Nidin 密碼。

    手動貼上或擴充功能帶回的可能是另一個 Nidin 帳號；若還留著舊密碼，
    下次憑證失效時自動重登會悄悄把帳號換回去。
    """
    now = time.time()
    with _connection() as conn:
        if not keep_login:
            conn.execute("DELETE FROM leader_nidin_logins WHERE user_id=?", (user_id,))
        conn.execute(
            "INSERT INTO leader_credentials VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET token=excluded.token, "
            "nidin_user_id=excluded.nidin_user_id, updated_at=excluded.updated_at",
            (user_id, token.strip(), nidin_user_id.strip(), now),
        )
        conn.execute(
            "INSERT INTO leader_credential_revisions VALUES (?, 1, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET revision=revision+1, updated_at=excluded.updated_at",
            (user_id, now),
        )


def clear_leader_credentials(user_id: str) -> None:
    now = time.time()
    with _connection() as conn:
        conn.execute("DELETE FROM leader_credentials WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM leader_nidin_logins WHERE user_id=?", (user_id,))
        conn.execute(
            "INSERT INTO leader_credential_revisions VALUES (?, 1, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET revision=revision+1, updated_at=excluded.updated_at",
            (user_id, now),
        )


def _credential_fernet():
    if _db_path_factory is None:
        raise RuntimeError("configure_auth must be called before using auth")
    return credential_crypto.load_fernet(Path(_db_path_factory()).parent)


def save_nidin_login(
    user_id: str, account: str, password: str, device_id: str
) -> None:
    password_enc = _credential_fernet().encrypt(password.encode()).decode()
    with _connection() as conn:
        conn.execute(
            "INSERT INTO leader_nidin_logins VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET account=excluded.account, "
            "password_enc=excluded.password_enc, device_id=excluded.device_id, "
            "updated_at=excluded.updated_at",
            (user_id, account, password_enc, device_id, time.time()),
        )


def has_nidin_login(user_id: str) -> bool:
    with _connection() as conn:
        return conn.execute(
            "SELECT 1 FROM leader_nidin_logins WHERE user_id=?", (user_id,)
        ).fetchone() is not None


def get_nidin_login(user_id: str) -> tuple[str, str, str] | None:
    """回傳 (account, password, device_id)；金鑰換過而解不開時視同沒記住。"""
    with _connection() as conn:
        row = conn.execute(
            "SELECT account, password_enc, device_id FROM leader_nidin_logins "
            "WHERE user_id=?",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    try:
        password = _credential_fernet().decrypt(row["password_enc"].encode()).decode()
    except InvalidToken:
        return None
    return str(row["account"]), password, str(row["device_id"])


def clear_nidin_login(user_id: str) -> None:
    with _connection() as conn:
        conn.execute("DELETE FROM leader_nidin_logins WHERE user_id=?", (user_id,))


def migrate_legacy_credentials(first_admin_id: str) -> None:
    with _connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        admin = conn.execute(
            "SELECT id FROM users WHERE is_admin=1 "
            "ORDER BY created_at, rowid LIMIT 1"
        ).fetchone()
        if admin is None or admin["id"] != first_admin_id:
            raise ValueError("Legacy credentials require the first administrator")
        marker = "legacy_leader_credentials_v1"
        if conn.execute(
            "SELECT 1 FROM auth_migrations WHERE name=?", (marker,)
        ).fetchone():
            return
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='leader_auth'"
        ).fetchone():
            row = conn.execute(
                "SELECT token, user_id FROM leader_auth WHERE id=1"
            ).fetchone()
            if row and row["token"]:
                conn.execute(
                    "INSERT OR IGNORE INTO leader_credentials VALUES (?, ?, ?, ?)",
                    (first_admin_id, row["token"], row["user_id"], time.time()),
                )
        # Preserve the old row for rollback, but never re-import cleared secrets.
        conn.execute(
            "INSERT INTO auth_migrations VALUES (?, ?)", (marker, time.time())
        )
