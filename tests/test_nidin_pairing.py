import hashlib
import sqlite3
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import auth, nidin_pairing
from app.routers import leader_auth, nidin_pairing as pairing_router

ADMIN = {"username": "admin", "password": "admin-password-123"}
LEADER = {"username": "leader", "password": "leader-password-123"}


@pytest.fixture
def pairing_app(monkeypatch, tmp_path):
    path = tmp_path / "test_pairing.db"
    monkeypatch.setenv("NIDIN_ADMIN_USERNAME", ADMIN["username"])
    monkeypatch.setenv("NIDIN_ADMIN_PASSWORD", ADMIN["password"])
    monkeypatch.setenv("NIDIN_COOKIE_SECURE", "false")
    monkeypatch.setattr(auth, "_db_path_factory", None)
    monkeypatch.setattr(auth, "_attempts", {})
    auth.configure_auth(lambda: path)

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(leader_auth.router)
    app.include_router(pairing_router.router)

    return app, path


def login(client, account=ADMIN):
    res = client.post("/auth/login", json=account)
    assert res.status_code == 200, res.text
    return res.json()


def create_user(client, account=LEADER):
    res = client.post("/auth/accounts", json=account)
    assert res.status_code == 201, res.text
    return res.json()


def test_pairing_lifecycle_and_security(pairing_app):
    app, db_path = pairing_app
    with TestClient(app, base_url="http://testserver") as client:
        # 1. 未登入嘗試建立配對 -> 401
        res = client.post("/leader/auth/pairings")
        assert res.status_code == 401

        # 登入為管理員
        login(client, ADMIN)

        # 2. 正常建立配對
        res = client.post("/leader/auth/pairings")
        assert res.status_code == 200
        assert res.headers.get("cache-control") == "no-store"
        data = res.json()
        flow_id = data["flow_id"]
        state = data["state"]
        expires_at = data["expires_at"]
        assert len(state) >= 32
        assert expires_at > time.time() + 290
        assert data["credential_revision"] == 0

        # 確認資料庫只存 state 的 hash，不存 plain state
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM nidin_credential_pairings WHERE flow_id=?",
                (flow_id,),
            ).fetchone()
            assert row is not None
            assert row["state_hash"] == hashlib.sha256(state.encode()).hexdigest()
            assert row["state_hash"] != state
            assert row["status"] == "pending"

        # 3. 測試 Lease 採集許可
        # 3a. 錯誤 state -> 403
        lease_res = client.post(
            f"/leader/auth/pairings/{flow_id}/lease",
            json={"state": "wrong_state_0000000000000000"},
        )
        assert lease_res.status_code == 403

        # 3b. 正常取得 Lease -> status pending, lease_expires_at <= now + 10
        lease_res = client.post(
            f"/leader/auth/pairings/{flow_id}/lease",
            json={"state": state},
        )
        assert lease_res.status_code == 200
        lease_data = lease_res.json()
        assert lease_data["status"] == "pending"
        assert lease_data["lease_expires_at"] <= time.time() + 10.1

        # 4. 測試 Confirm 綁定
        # 4a. 格式驗證：空 token -> 422
        bad_confirm = client.post(
            f"/leader/auth/pairings/{flow_id}/confirm",
            json={"state": state, "token": "   ", "user_id": "123"},
        )
        assert bad_confirm.status_code == 422

        # 4b. 格式驗證：超長 token (>16 KiB) -> 422
        bad_confirm = client.post(
            f"/leader/auth/pairings/{flow_id}/confirm",
            json={"state": state, "token": "x" * 17000, "user_id": "123"},
        )
        assert bad_confirm.status_code == 422

        # 4c. 正常 Confirm
        test_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.testpayload"
        test_user_id = "12345678"
        confirm_res = client.post(
            f"/leader/auth/pairings/{flow_id}/confirm",
            json={"state": state, "token": test_token, "user_id": test_user_id},
        )
        assert confirm_res.status_code == 200
        confirm_data = confirm_res.json()
        assert confirm_data["is_authenticated"] is True
        assert confirm_data["user_id"] == test_user_id
        assert confirm_data["masked_token"] == f"{test_token[:6]}...{test_token[-4:]}"

        # 4d. 驗證 credentials 已寫入且 revision 遞增
        assert auth.get_credential_revision(ADMIN["username"]) or auth.get_credential_revision(row["user_id"]) == 1
        status_res = client.get("/leader/auth")
        assert status_res.status_code == 200
        assert status_res.json()["is_authenticated"] is True

        # 4e. 回應遺失重試（同一 session 重新請求同一 consumed flow）-> 回傳成功與遮罩結果
        retry_res = client.post(
            f"/leader/auth/pairings/{flow_id}/confirm",
            json={"state": state, "token": test_token, "user_id": test_user_id},
        )
        assert retry_res.status_code == 200
        assert retry_res.json()["masked_token"] == confirm_data["masked_token"]

        # 4f. 若憑證之後被手動更新（revision 變更），原 consumed flow 重試應拒絕 409
        client.post("/leader/auth", json={"token": "brand_new_manual_token_123", "user_id": "new_uid"})
        stale_retry_res = client.post(
            f"/leader/auth/pairings/{flow_id}/confirm",
            json={"state": state, "token": test_token, "user_id": test_user_id},
        )
        assert stale_retry_res.status_code == 409
        assert "另有更新" in stale_retry_res.json()["detail"]


def test_pairing_revision_conflict(pairing_app):
    """測試配對期間，若有人手動更新憑證，confirm 會被 revision 檢查擋住。"""
    app, _ = pairing_app
    with TestClient(app, base_url="http://testserver") as client:
        login(client, ADMIN)

        # 建立配對 (initial_revision = 0)
        res = client.post("/leader/auth/pairings")
        assert res.status_code == 200
        flow_id = res.json()["flow_id"]
        state = res.json()["state"]

        # 模擬在另一分頁或手動更新了憑證 (revision 變為 1)
        client.post("/leader/auth", json={"token": "manual_token_abc_123", "user_id": "manual_u"})

        # 此時嘗試 confirm 舊配對 -> 必須回傳 409 衝突
        confirm_res = client.post(
            f"/leader/auth/pairings/{flow_id}/confirm",
            json={"state": state, "token": "candidate_token_xyz_456", "user_id": "auto_u"},
        )
        assert confirm_res.status_code == 409
        assert "憑證版本已變更" in confirm_res.json()["detail"]

        # 驗證既有憑證沒有被覆蓋
        status = client.get("/leader/auth").json()
        assert status["user_id"] == "manual_u"


def test_pairing_cancellation_and_auto_cancel_old_pending(pairing_app):
    """測試取消配對，以及同 session 建立新配對時自動撤銷舊 pending 配對。"""
    app, db_path = pairing_app
    with TestClient(app, base_url="http://testserver") as client:
        login(client, ADMIN)

        # 建立第一個配對
        res1 = client.post("/leader/auth/pairings")
        flow1 = res1.json()["flow_id"]
        state1 = res1.json()["state"]

        # 主動取消 flow1
        cancel_res = client.post(
            f"/leader/auth/pairings/{flow1}/cancel",
            json={"state": state1},
        )
        assert cancel_res.status_code == 200

        # 取消後嘗試 lease 或 confirm -> 409
        assert client.post(
            f"/leader/auth/pairings/{flow1}/lease", json={"state": state1}
        ).status_code == 409
        assert client.post(
            f"/leader/auth/pairings/{flow1}/confirm",
            json={"state": state1, "token": "tok_1234567890", "user_id": "uid"},
        ).status_code == 409

        # 建立第二個配對 flow2
        res2 = client.post("/leader/auth/pairings")
        flow2 = res2.json()["flow_id"]
        state2 = res2.json()["state"]

        # 在同一 session 建立第三個配對 flow3 -> flow2 應被自動更新為 cancelled
        res3 = client.post("/leader/auth/pairings")
        flow3 = res3.json()["flow_id"]

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row2 = conn.execute(
                "SELECT status FROM nidin_credential_pairings WHERE flow_id=?",
                (flow2,),
            ).fetchone()
            assert row2["status"] == "cancelled"

            row3 = conn.execute(
                "SELECT status FROM nidin_credential_pairings WHERE flow_id=?",
                (flow3,),
            ).fetchone()
            assert row3["status"] == "pending"


def test_pairing_cross_user_isolation(pairing_app):
    """測試不同使用者無法存取彼此的配對流程。"""
    app, _ = pairing_app
    with TestClient(app, base_url="http://testserver") as client_admin:
        login(client_admin, ADMIN)
        create_user(client_admin, LEADER)

        res = client_admin.post("/leader/auth/pairings")
        admin_flow_id = res.json()["flow_id"]
        admin_state = res.json()["state"]

    with TestClient(app, base_url="http://testserver") as client_leader:
        login(client_leader, LEADER)

        # Leader 嘗試存取 Admin 的 flow -> 404 (避免洩漏他人流程)
        assert client_leader.post(
            f"/leader/auth/pairings/{admin_flow_id}/lease",
            json={"state": admin_state},
        ).status_code == 404
        assert client_leader.post(
            f"/leader/auth/pairings/{admin_flow_id}/confirm",
            json={"state": admin_state, "token": "some_token_123456", "user_id": "u1"},
        ).status_code == 404
        assert client_leader.post(
            f"/leader/auth/pairings/{admin_flow_id}/cancel",
            json={"state": admin_state},
        ).status_code == 404


def test_confirm_holds_write_lock_against_interleaved_update(pairing_app, monkeypatch):
    """confirm 讀到寫入之間若有人 commit，必須偵測衝突而非靜默覆蓋。

    這是 lost update：deferred transaction 下 confirm 先開讀取交易，
    競爭者可以插進來 commit，confirm 再寫入就覆蓋掉對方的 revision。
    規格要求整段檢查與寫入在同一個寫入交易內完成。
    """
    app, db_path = pairing_app
    with TestClient(app, base_url="http://testserver") as client:
        login(client, ADMIN)
        res = client.post("/leader/auth/pairings")
        flow_id, state = res.json()["flow_id"], res.json()["state"]

        # 在 confirm 已讀完 revision、尚未寫入的空檔插入競爭寫入
        real_mask = nidin_pairing.mask_token
        fired = []

        def mask_then_race(token: str) -> str:
            masked = real_mask(token)
            if not fired:
                fired.append(True)
                other = sqlite3.connect(str(db_path), timeout=1)
                try:
                    other.execute("BEGIN IMMEDIATE")
                    other.execute(
                        "INSERT INTO leader_credential_revisions VALUES (?, 99, ?) "
                        "ON CONFLICT(user_id) DO UPDATE SET revision=99",
                        (
                            other.execute("SELECT id FROM users LIMIT 1").fetchone()[0],
                            time.time(),
                        ),
                    )
                    other.commit()
                    fired.append("committed")
                except sqlite3.OperationalError:
                    fired.append("blocked")
                finally:
                    other.close()
            return masked

        monkeypatch.setattr(nidin_pairing, "mask_token", mask_then_race)

        confirm = client.post(
            f"/leader/auth/pairings/{flow_id}/confirm",
            json={"state": state, "token": "candidate_token_xyz_456", "user_id": "auto_u"},
        )

    # confirm 持有寫入鎖時，競爭者應該被擋掉（拿不到鎖）
    assert fired[1:] == ["blocked"], f"競爭寫入未被阻擋：{fired[1:]}"
    # 既然沒有人插進來，這次 confirm 本身應該成功
    assert confirm.status_code == 200, confirm.text
