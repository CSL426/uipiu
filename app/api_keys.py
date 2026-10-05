"""外部 agent 用的 API key：主揪發給 agent，agent 帶著它呼叫 /mcp。

key 只在發出當下顯示一次，資料庫只存 SHA-256（key 本身是 32 bytes 隨機值，不需要慢雜湊）。
每把 key 屬於發它的主揪；leader 權限只能操作該主揪自己的團。
"""

import hashlib
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app import auth as account_auth
from app.auth import AuthUser, require_user

KEY_PREFIX = "mynig_"
MAX_KEYS_PER_USER = 50
Scope = Literal["order", "leader"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    scope TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    hint TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_used_at REAL,
    revoked_at REAL
);
CREATE INDEX IF NOT EXISTS idx_api_keys_owner ON api_keys(owner_id);
"""

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


@dataclass(frozen=True)
class ApiKeyPrincipal:
    key_id: str
    owner_id: str
    name: str
    scope: Scope

    @property
    def is_leader(self) -> bool:
        return self.scope == "leader"


class ApiKeyInfo(BaseModel):
    id: str
    name: str
    scope: Scope
    hint: str
    created_at: float
    last_used_at: float | None = None
    revoked: bool = False


class CreateApiKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    scope: Scope = "order"


class CreatedApiKey(ApiKeyInfo):
    # 只在建立時回傳這一次
    key: str


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    """auth 的連線加上本模組的資料表。"""
    with account_auth.auth_connection() as conn:
        conn.executescript(_SCHEMA)
        yield conn


def _info(row) -> ApiKeyInfo:
    return ApiKeyInfo(
        id=row["id"],
        name=row["name"],
        scope=row["scope"],
        hint=row["hint"],
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
        revoked=row["revoked_at"] is not None,
    )


def create_key(owner_id: str, name: str, scope: Scope) -> CreatedApiKey:
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    key_id = secrets.token_urlsafe(9)
    now = time.time()
    with _connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM api_keys WHERE owner_id=? AND revoked_at IS NULL", (owner_id,)
        ).fetchone()[0]
        if count >= MAX_KEYS_PER_USER:
            raise ValueError(f"有效的 key 最多 {MAX_KEYS_PER_USER} 把，請先撤銷用不到的。")
        conn.execute(
            "INSERT INTO api_keys (id, owner_id, name, scope, key_hash, hint, created_at) VALUES (?,?,?,?,?,?,?)",
            (key_id, owner_id, name.strip(), scope, _hash(key), key[-4:], now),
        )
    return CreatedApiKey(id=key_id, name=name.strip(), scope=scope, hint=key[-4:], created_at=now, key=key)


def list_keys(owner_id: str) -> list[ApiKeyInfo]:
    with _connection() as conn:
        rows = conn.execute(
            "SELECT * FROM api_keys WHERE owner_id=? ORDER BY created_at DESC", (owner_id,)
        ).fetchall()
    return [_info(row) for row in rows]


def revoke_key(owner_id: str, key_id: str) -> None:
    with _connection() as conn:
        updated = conn.execute(
            "UPDATE api_keys SET revoked_at=? WHERE id=? AND owner_id=? AND revoked_at IS NULL",
            (time.time(), key_id, owner_id),
        ).rowcount
    if not updated:
        raise KeyError("找不到這把 key，或已經撤銷了。")


def authenticate(key: str) -> ApiKeyPrincipal | None:
    if not key.startswith(KEY_PREFIX) or len(key) > 128:
        return None
    with _connection() as conn:
        row = conn.execute(
            "SELECT k.* FROM api_keys k JOIN users u ON u.id = k.owner_id "
            "WHERE k.key_hash=? AND k.revoked_at IS NULL",
            (_hash(key),),
        ).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (time.time(), row["id"]))
    return ApiKeyPrincipal(key_id=row["id"], owner_id=row["owner_id"], name=row["name"], scope=row["scope"])


def bearer_key(authorization: str | None) -> str:
    scheme, _, value = (authorization or "").partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


@router.get("", response_model=list[ApiKeyInfo])
def get_keys(user: AuthUser = Depends(require_user)) -> list[ApiKeyInfo]:
    return list_keys(user.id)


@router.post("", response_model=CreatedApiKey)
def post_key(payload: CreateApiKeyRequest, request: Request, user: AuthUser = Depends(require_user)) -> CreatedApiKey:
    account_auth.check_csrf(request)
    try:
        return create_key(user.id, payload.name, payload.scope)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete("/{key_id}")
def delete_key(key_id: str, request: Request, user: AuthUser = Depends(require_user)) -> dict[str, bool]:
    account_auth.check_csrf(request)
    try:
        revoke_key(user.id, key_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"revoked": True}
