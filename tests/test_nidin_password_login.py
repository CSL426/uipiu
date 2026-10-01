import base64
import json
import sqlite3
import stat
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app import auth as account_auth
from app import credential_crypto
from app.cart_store import CartStore
from app.main import app
from app.nidin import normalize_official_group_buy_info, normalize_menu_from_api
from app.nidin.client import (
    NidinClient,
    NidinLoginError,
    NidinTokenExpired,
    token_expires_at,
)
from tests.fixtures import RAW_GROUP_MENU


pytestmark = pytest.mark.usefixtures("authenticated_manager")

PHONE = "0912345678"
OFFICIAL = normalize_official_group_buy_info(
    {
        "group_buy_head_order_info": {"group_buying_name": "下午茶", "status": 20},
        "group_buy_follower_order_list": [],
    },
    "TESTCODE01",
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def isolate_cart_store(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.delenv(credential_crypto.KEY_ENV, raising=False)
    monkeypatch.setattr("app.dependencies.cart_store", CartStore(tmp_path / "cart.db"))


def _api():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


def _session_id() -> str:
    from app.dependencies import cart_store

    return cart_store.create_or_get_session(
        group_buy_url="https://order.nidin.shop/gb/menu/1717/TESTCODE01",
        store_id="1717",
        group_buy_code="TESTCODE01",
        session_name="測試團購",
        menu=normalize_menu_from_api(RAW_GROUP_MENU, "1717"),
        delivery_summary=None,
        owner_id=_manager_id(),
    ).session_id


def _manager_id() -> str:
    return account_auth.first_admin_id()


@pytest.mark.anyio
async def test_login_web_sends_phone_password_login_and_returns_token():
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["url"] = str(request.url)
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, json={"status": 200, "user_id": 12345678, "token": "tok-abc"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = NidinClient(base_url="https://api.test", client=http)
        token, user_id = await client.login_web(account=PHONE, password="pw", device_id="dev")

    assert (token, user_id) == ("tok-abc", "12345678")
    assert sent["url"] == "https://api.test/user/loginWeb"
    assert sent["body"] == {
        "type": 1,
        "account": PHONE,
        "password": "pw",
        "device_id": "dev",
        "identity": "nidinphone",
    }


@pytest.mark.anyio
async def test_login_web_surfaces_nidin_rejection_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": 1201, "message": "帳號或密碼錯誤"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = NidinClient(base_url="https://api.test", client=http)
        with pytest.raises(NidinLoginError, match="帳號或密碼錯誤"):
            await client.login_web(account=PHONE, password="wrong", device_id="dev")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [httpx.Response(401, json={}), httpx.Response(200, json={"status": 1306, "message": "token fail"})],
)
async def test_official_info_reports_revoked_token(response: httpx.Response):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as http:
        client = NidinClient(base_url="https://api.test", client=http)
        with pytest.raises(NidinTokenExpired):
            await client.fetch_group_buy_official_info(group_buy_code="TESTCODE01", token="old")


@pytest.mark.anyio
async def test_password_login_binds_token_and_stores_password_encrypted(tmp_path):
    with patch.object(NidinClient, "login_web", new=AsyncMock(return_value=("tok-new-123456", "777"))):
        async with _api() as client:
            res = await client.post(
                "/leader/auth/password",
                json={"account": PHONE, "password": "secret-pw-xyz", "remember": True},
            )
            status = await client.get("/leader/auth")

    assert res.status_code == 200
    assert res.json()["auto_relogin"] is True
    assert status.json() == {**res.json(), "is_authenticated": True}
    assert account_auth.get_leader_credentials(_manager_id()) == ("tok-new-123456", "777")
    assert account_auth.get_nidin_login(_manager_id())[:2] == (PHONE, "secret-pw-xyz")

    raw = sqlite3.connect(tmp_path / "cart.db").execute(
        "SELECT password_enc FROM leader_nidin_logins"
    ).fetchone()[0]
    assert "secret-pw-xyz" not in raw
    key_file = tmp_path / credential_crypto.KEY_FILENAME
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600


@pytest.mark.anyio
async def test_password_login_without_remember_keeps_no_password():
    with patch.object(NidinClient, "login_web", new=AsyncMock(return_value=("tok-new-123456", "777"))):
        async with _api() as client:
            res = await client.post(
                "/leader/auth/password",
                json={"account": PHONE, "password": "secret-pw-xyz", "remember": False},
            )

    assert res.json()["auto_relogin"] is False
    assert account_auth.get_nidin_login(_manager_id()) is None


@pytest.mark.anyio
async def test_password_login_rejects_bad_phone_and_reports_nidin_refusal():
    async with _api() as client:
        bad_phone = await client.post("/leader/auth/password", json={"account": "12345", "password": "x"})
        with patch.object(NidinClient, "login_web", new=AsyncMock(side_effect=NidinLoginError("帳號或密碼錯誤"))):
            refused = await client.post("/leader/auth/password", json={"account": PHONE, "password": "x"})

    assert bad_phone.status_code == 422
    assert refused.status_code == 400
    assert refused.json()["detail"] == "帳號或密碼錯誤"
    assert account_auth.get_leader_credentials(_manager_id()) is None


@pytest.mark.anyio
async def test_binding_a_token_another_way_forgets_the_remembered_password():
    account_auth.save_nidin_login(_manager_id(), PHONE, "old-pw", "dev")
    async with _api() as client:
        await client.post("/leader/auth", json={"token": "pasted-token-123", "user_id": "9"})

    assert account_auth.get_nidin_login(_manager_id()) is None


@pytest.mark.anyio
async def test_sync_relogs_in_with_remembered_password_when_token_is_revoked():
    uid = _manager_id()
    account_auth.set_leader_credentials(uid, "revoked-token", "777", keep_login=True)
    account_auth.save_nidin_login(uid, PHONE, "secret-pw-xyz", "dev-1")
    session_id = _session_id()
    fetch = AsyncMock(side_effect=[NidinTokenExpired(), OFFICIAL])
    login = AsyncMock(return_value=("fresh-token-999", "777"))

    with patch.object(NidinClient, "fetch_group_buy_official_info", new=fetch), \
            patch.object(NidinClient, "login_web", new=login):
        async with _api() as client:
            res = await client.post(f"/cart-sessions/{session_id}/sync-official")

    assert res.status_code == 200
    login.assert_awaited_once_with(account=PHONE, password="secret-pw-xyz", device_id="dev-1")
    assert fetch.await_args_list[1].kwargs["token"] == "fresh-token-999"
    assert account_auth.get_leader_credentials(uid) == ("fresh-token-999", "777")
    assert account_auth.get_nidin_login(uid) is not None


@pytest.mark.anyio
async def test_sync_without_remembered_password_asks_to_rebind():
    account_auth.set_leader_credentials(_manager_id(), "revoked-token", "777")
    session_id = _session_id()

    with patch.object(NidinClient, "fetch_group_buy_official_info", new=AsyncMock(side_effect=NidinTokenExpired())):
        async with _api() as client:
            res = await client.get(f"/cart-sessions/{session_id}/official-orders")

    assert res.status_code == 401
    assert "重新綁定" in res.json()["detail"]


@pytest.mark.anyio
async def test_failed_relogin_backs_off_instead_of_hammering_or_forgetting_the_password():
    from app.routers import leader_auth

    leader_auth._relogin_failed_at.clear()
    uid = _manager_id()
    account_auth.set_leader_credentials(uid, "revoked-token", "777", keep_login=True)
    account_auth.save_nidin_login(uid, PHONE, "changed-on-nidin", "dev-1")
    session_id = _session_id()
    login = AsyncMock(side_effect=NidinLoginError("密碼錯誤"))

    with patch.object(NidinClient, "fetch_group_buy_official_info", new=AsyncMock(side_effect=NidinTokenExpired())), \
            patch.object(NidinClient, "login_web", new=login):
        async with _api() as client:
            first = await client.get(f"/cart-sessions/{session_id}/official-orders")
            second = await client.get(f"/cart-sessions/{session_id}/official-orders")

    assert first.status_code == 401 and "密碼錯誤" in first.json()["detail"]
    assert second.status_code == 401 and "最近失敗過" in second.json()["detail"]
    login.assert_awaited_once()
    # 一次暫時性的拒絕不該逼主揪重新綁定
    assert account_auth.get_nidin_login(uid) is not None

    with patch.object(NidinClient, "login_web", new=AsyncMock(return_value=("tok-new-123456", "777"))):
        async with _api() as client:
            await client.post("/leader/auth/password", json={"account": PHONE, "password": "new-pw"})
    assert uid not in leader_auth._relogin_failed_at


def test_undecryptable_password_is_treated_as_not_remembered(tmp_path):
    uid = _manager_id()
    account_auth.save_nidin_login(uid, PHONE, "pw", "dev")
    (tmp_path / credential_crypto.KEY_FILENAME).unlink()

    assert account_auth.get_nidin_login(uid) is None


def _nidin_login_client():
    # 這組端點本來就不需要本站登入，拿掉 conftest 的 manager 覆寫才測得到真實行為
    app.dependency_overrides.pop(account_auth.require_user, None)
    app.dependency_overrides.pop(account_auth.current_user, None)
    return _api()


@pytest.mark.anyio
async def test_nidin_account_can_log_into_the_site_after_binding_once():
    with patch.object(NidinClient, "login_web", new=AsyncMock(return_value=("tok-first-123", "777"))):
        async with _api() as client:
            bind = await client.post("/leader/auth/password", json={"account": PHONE, "password": "pw"})
    assert bind.json()["nidin_login_linked"] is True

    with patch.object(NidinClient, "login_web", new=AsyncMock(return_value=("tok-second-456", "777"))):
        async with _nidin_login_client() as client:
            res = await client.post("/auth/nidin-login", json={"account": PHONE, "password": "pw"})
            me = await client.get("/auth/me")

    assert res.status_code == 200
    assert res.json()["id"] == _manager_id()
    assert me.status_code == 200 and me.json()["id"] == _manager_id()
    # 登入時順便換上新憑證
    assert account_auth.get_leader_credentials(_manager_id()) == ("tok-second-456", "777")


@pytest.mark.anyio
async def test_unlinked_nidin_account_cannot_enter_the_site():
    with patch.object(NidinClient, "login_web", new=AsyncMock(return_value=("tok-stranger-1", "999"))):
        async with _nidin_login_client() as client:
            res = await client.post("/auth/nidin-login", json={"account": "0911111111", "password": "pw"})
            me = await client.get("/auth/me")

    assert res.status_code == 403
    assert "還沒連結" in res.json()["detail"]
    assert me.status_code == 401
    assert account_auth.get_leader_credentials(_manager_id()) is None


@pytest.mark.anyio
async def test_wrong_nidin_password_is_rejected_without_a_session():
    with patch.object(NidinClient, "login_web", new=AsyncMock(side_effect=NidinLoginError("密碼錯誤"))):
        async with _nidin_login_client() as client:
            res = await client.post("/auth/nidin-login", json={"account": PHONE, "password": "bad"})

    assert res.status_code == 401
    assert "nidin_session" not in res.cookies


@pytest.mark.anyio
async def test_manually_pasted_user_id_never_links_a_login():
    async with _api() as client:
        await client.post("/leader/auth", json={"token": "pasted-token-123", "user_id": "777"})
    assert account_auth.has_nidin_account_link(_manager_id()) is False


@pytest.mark.anyio
async def test_unbinding_stops_nidin_login():
    with patch.object(NidinClient, "login_web", new=AsyncMock(return_value=("tok-first-123", "777"))):
        async with _api() as client:
            await client.post("/leader/auth/password", json={"account": PHONE, "password": "pw"})
            await client.delete("/leader/auth")
        async with _nidin_login_client() as client:
            res = await client.post("/auth/nidin-login", json={"account": PHONE, "password": "pw"})

    assert res.status_code == 403


@pytest.mark.anyio
async def test_owner_phone_bootstraps_an_admin_on_an_empty_site(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("NIDIN_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("NIDIN_OWNER_PHONE", PHONE)

    with patch.object(NidinClient, "login_web", new=AsyncMock(return_value=("tok-owner-123", "777"))):
        async with _nidin_login_client() as client:
            res = await client.post("/auth/nidin-login", json={"account": PHONE, "password": "pw"})

    assert res.status_code == 200
    assert res.json()["is_admin"] is True
    admin_id = account_auth.first_admin_id()
    assert res.json()["id"] == admin_id
    assert account_auth.has_nidin_account_link(admin_id)
    assert account_auth.get_leader_credentials(admin_id) == ("tok-owner-123", "777")


@pytest.mark.anyio
async def test_owner_phone_links_to_the_existing_admin():
    with patch.dict("os.environ", {"NIDIN_OWNER_PHONE": PHONE}), \
            patch.object(NidinClient, "login_web", new=AsyncMock(return_value=("tok-owner-123", "777"))):
        async with _nidin_login_client() as client:
            res = await client.post("/auth/nidin-login", json={"account": PHONE, "password": "pw"})

    assert res.status_code == 200
    assert res.json()["id"] == _manager_id()


@pytest.mark.anyio
async def test_other_phones_stay_locked_out_when_an_owner_is_configured():
    with patch.dict("os.environ", {"NIDIN_OWNER_PHONE": PHONE}), \
            patch.object(NidinClient, "login_web", new=AsyncMock(return_value=("tok-x-123456", "888"))):
        async with _nidin_login_client() as client:
            res = await client.post("/auth/nidin-login", json={"account": "0911111111", "password": "pw"})

    assert res.status_code == 403


@pytest.mark.anyio
async def test_owner_phone_with_wrong_nidin_password_gets_nothing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("NIDIN_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("NIDIN_OWNER_PHONE", PHONE)

    with patch.object(NidinClient, "login_web", new=AsyncMock(side_effect=NidinLoginError("密碼錯誤"))):
        async with _nidin_login_client() as client:
            res = await client.post("/auth/nidin-login", json={"account": PHONE, "password": "bad"})

    assert res.status_code == 401
    assert account_auth.first_admin_id() is None



def _jwt(claims: dict) -> str:
    def seg(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{seg({'typ': 'SK_Token', 'alg': 'HS256'})}.{seg(claims)}.signature"


def test_token_expiry_is_read_from_the_jwt_and_garbage_is_tolerated():
    assert token_expires_at(_jwt({"iat": 1790000000, "exp": 1792592000})) == 1792592000.0
    assert token_expires_at("not-a-jwt") is None
    assert token_expires_at("a.!!!.c") is None
    assert token_expires_at(_jwt({"iat": 1})) is None


@pytest.mark.anyio
async def test_fetch_user_info_is_a_read_only_get_with_the_leader_token():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(method=request.method, url=str(request.url), token=request.headers.get("MC-API-Token"))
        return httpx.Response(200, json={"status": 200, "user_id": 777, "name": "主揪"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        info = await NidinClient(base_url="https://api.test", client=http).fetch_user_info(token="tok", user_id="777")

    assert seen == {"method": "GET", "url": "https://api.test/user/777/info", "token": "tok"}
    assert info["name"] == "主揪"


@pytest.mark.anyio
async def test_connection_check_proves_the_token_works_and_reports_expiry():
    token = _jwt({"exp": 1792592000})
    account_auth.set_leader_credentials(_manager_id(), token, "777", keep_login=True)
    account_auth.save_nidin_login(_manager_id(), PHONE, "pw", "dev")
    info = AsyncMock(return_value={"status": 200, "user_id": 777, "nickname": "阿主", "name": "王小明"})

    with patch.object(NidinClient, "fetch_user_info", new=info):
        async with _api() as client:
            res = await client.post("/leader/auth/check")
            status = await client.get("/leader/auth")

    assert res.status_code == 200
    assert res.json() == {
        "nidin_user_id": "777",
        "display_name": "阿主",
        "expires_at": 1792592000.0,
        "relogged_in": False,
        "auto_relogin": True,
    }
    info.assert_awaited_once_with(token=token, user_id="777")
    assert status.json()["expires_at"] == 1792592000.0


@pytest.mark.anyio
async def test_connection_check_relogs_in_when_the_token_was_revoked():
    uid = _manager_id()
    account_auth.set_leader_credentials(uid, "revoked", "777", keep_login=True)
    account_auth.save_nidin_login(uid, PHONE, "pw", "dev")
    fresh = _jwt({"exp": 1792592000})
    info = AsyncMock(side_effect=[NidinTokenExpired(), {"status": 200, "user_id": 777, "name": "王小明"}])

    with patch.object(NidinClient, "fetch_user_info", new=info), \
            patch.object(NidinClient, "login_web", new=AsyncMock(return_value=(fresh, "777"))):
        async with _api() as client:
            res = await client.post("/leader/auth/check")

    assert res.status_code == 200
    assert res.json()["relogged_in"] is True
    assert res.json()["display_name"] == "王小明"
    assert res.json()["expires_at"] == 1792592000.0
    assert account_auth.get_leader_credentials(uid) == (fresh, "777")


@pytest.mark.anyio
async def test_connection_check_without_binding_says_so():
    async with _api() as client:
        res = await client.post("/leader/auth/check")

    assert res.status_code == 401
    assert "尚未連結 Nidin" in res.json()["detail"]


@pytest.mark.anyio
async def test_connection_check_surfaces_an_unexpected_nidin_answer():
    account_auth.set_leader_credentials(_manager_id(), "tok-123456789", "777")

    with patch.object(NidinClient, "fetch_user_info", new=AsyncMock(return_value={"status": 500, "message": "系統維護中"})):
        async with _api() as client:
            res = await client.post("/leader/auth/check")

    assert res.status_code == 502
    assert "系統維護中" in res.json()["detail"]
