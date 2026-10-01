"""Nidin 憑證自動配對後端邏輯與 SQLite 原子交易。"""

import hashlib
import hmac
import secrets
import time

from fastapi import HTTPException

from app.auth import (
    auth_write_connection,
    get_credential_revision,
    mask_token,
)

PAIRING_TTL_SECONDS = 300.0  # 5 分鐘有效期限
LEASE_TTL_SECONDS = 10.0      # 單次許可最長 10 秒


def _hash_state(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def init_pairing(
    user_id: str,
    username: str,
    session_token_hash: str,
    origin: str,
) -> tuple[str, str, float, int]:
    now = time.time()
    expires_at = now + PAIRING_TTL_SECONDS
    flow_id = secrets.token_urlsafe(16)
    state = secrets.token_hex(32)
    state_hash = _hash_state(state)

    with auth_write_connection() as conn:
        # 清除過久以前已結束或逾期的配對紀錄（保留 1 小時以供重試冪等查詢）
        conn.execute(
            "DELETE FROM nidin_credential_pairings WHERE expires_at < ?",
            (now - 3600,),
        )
        # 同一 session 建立新配對時，自動撤銷舊的 pending 配對
        conn.execute(
            "UPDATE nidin_credential_pairings SET status='cancelled' "
            "WHERE user_id=? AND session_token_hash=? AND status='pending'",
            (user_id, session_token_hash),
        )
        initial_revision = get_credential_revision(user_id, conn)
        conn.execute(
            "INSERT INTO nidin_credential_pairings ("
            "flow_id, user_id, session_token_hash, origin, state_hash, "
            "status, initial_revision, created_at, expires_at, lease_expires_at"
            ") VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, 0)",
            (
                flow_id,
                user_id,
                session_token_hash,
                origin,
                state_hash,
                initial_revision,
                now,
                expires_at,
            ),
        )

    return flow_id, state, expires_at, initial_revision


def grant_lease(
    flow_id: str,
    user_id: str,
    session_token_hash: str,
    origin: str,
    state: str,
) -> float:
    now = time.time()
    state_hash = _hash_state(state)

    with auth_write_connection() as conn:
        row = conn.execute(
            "SELECT * FROM nidin_credential_pairings WHERE flow_id=?",
            (flow_id,),
        ).fetchone()

        if not row or row["user_id"] != user_id:
            raise HTTPException(404, "未知或無權存取的配對流程")
        if not hmac.compare_digest(row["session_token_hash"], session_token_hash):
            raise HTTPException(403, "登入工作階段不符")
        if row["origin"] != origin:
            raise HTTPException(403, "請求來源 Origin 不符")
        if not hmac.compare_digest(row["state_hash"], state_hash):
            raise HTTPException(403, "配對驗證碼 state 不符")

        if row["status"] == "cancelled":
            raise HTTPException(409, "配對流程已取消")
        if row["expires_at"] <= now:
            raise HTTPException(410, "配對流程已逾期")
        if row["status"] != "pending":
            raise HTTPException(409, "配對流程狀態無效")

        lease_expires_at = min(now + LEASE_TTL_SECONDS, row["expires_at"])
        conn.execute(
            "UPDATE nidin_credential_pairings SET lease_expires_at=? WHERE flow_id=?",
            (lease_expires_at, flow_id),
        )

    return lease_expires_at


def confirm_pairing(
    flow_id: str,
    user_id: str,
    session_token_hash: str,
    origin: str,
    state: str,
    token: str,
    nidin_user_id: str,
) -> tuple[str, str]:
    now = time.time()
    clean_token = token.strip()
    clean_user_id = nidin_user_id.strip()

    if not clean_token or len(clean_token.encode()) > 16384:
        raise HTTPException(422, "Token 格式無效或超過長度上限")
    if len(clean_user_id) > 128:
        raise HTTPException(422, "User ID 超過長度上限")

    state_hash = _hash_state(state)

    with auth_write_connection() as conn:
        row = conn.execute(
            "SELECT * FROM nidin_credential_pairings WHERE flow_id=?",
            (flow_id,),
        ).fetchone()

        if not row or row["user_id"] != user_id:
            raise HTTPException(404, "未知或無權存取的配對流程")
        if not hmac.compare_digest(row["session_token_hash"], session_token_hash):
            raise HTTPException(403, "登入工作階段不符")
        if row["origin"] != origin:
            raise HTTPException(403, "請求來源 Origin 不符")
        if not hmac.compare_digest(row["state_hash"], state_hash):
            raise HTTPException(403, "配對驗證碼 state 不符")

        if row["status"] == "cancelled":
            raise HTTPException(409, "配對流程已取消")
        if row["expires_at"] <= now:
            raise HTTPException(410, "配對流程已逾期")

        # 處理重試情況（已 consumed 但回應遺失）
        if row["status"] == "consumed":
            current_revision = get_credential_revision(user_id, conn)
            if current_revision == row["confirmed_revision"]:
                return row["confirmed_masked_token"], row["confirmed_user_id"]
            raise HTTPException(409, "配對已完成，憑證另有更新")

        if row["status"] != "pending":
            raise HTTPException(409, "配對流程狀態無效")

        # 再次檢查該配對與 revision，確保無並發覆蓋
        locked_flow = conn.execute(
            "SELECT * FROM nidin_credential_pairings WHERE flow_id=?",
            (flow_id,),
        ).fetchone()
        if locked_flow["status"] != "pending":
            raise HTTPException(409, "配對流程已被處理")

        rev_row = conn.execute(
            "SELECT revision FROM leader_credential_revisions WHERE user_id=?",
            (user_id,),
        ).fetchone()
        current_rev = int(rev_row["revision"]) if rev_row else 0
        if current_rev != row["initial_revision"]:
            raise HTTPException(409, "憑證版本已變更，請重新配對")

        new_rev = current_rev + 1
        masked = mask_token(clean_token)

        # 擴充功能帶回的可能是另一個 Nidin 帳號，舊密碼留著會在自動重登時把帳號換回去
        conn.execute("DELETE FROM leader_nidin_logins WHERE user_id=?", (user_id,))
        conn.execute(
            "INSERT INTO leader_credentials VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET token=excluded.token, "
            "nidin_user_id=excluded.nidin_user_id, updated_at=excluded.updated_at",
            (user_id, clean_token, clean_user_id, now),
        )
        conn.execute(
            "INSERT INTO leader_credential_revisions VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET revision=excluded.revision, "
            "updated_at=excluded.updated_at",
            (user_id, new_rev, now),
        )
        conn.execute(
            "UPDATE nidin_credential_pairings SET "
            "status='consumed', confirmed_user_id=?, confirmed_masked_token=?, "
            "confirmed_revision=? WHERE flow_id=?",
            (clean_user_id, masked, new_rev, flow_id),
        )

    return masked, clean_user_id


def cancel_pairing(
    flow_id: str,
    user_id: str,
    session_token_hash: str,
    origin: str,
    state: str,
) -> None:
    state_hash = _hash_state(state)

    with auth_write_connection() as conn:
        row = conn.execute(
            "SELECT * FROM nidin_credential_pairings WHERE flow_id=?",
            (flow_id,),
        ).fetchone()

        if not row or row["user_id"] != user_id:
            raise HTTPException(404, "未知或無權存取的配對流程")
        if not hmac.compare_digest(row["session_token_hash"], session_token_hash):
            raise HTTPException(403, "登入工作階段不符")
        if row["origin"] != origin:
            raise HTTPException(403, "請求來源 Origin 不符")
        if not hmac.compare_digest(row["state_hash"], state_hash):
            raise HTTPException(403, "配對驗證碼 state 不符")

        if row["status"] == "pending":
            conn.execute(
                "UPDATE nidin_credential_pairings SET status='cancelled' "
                "WHERE flow_id=?",
                (flow_id,),
            )
