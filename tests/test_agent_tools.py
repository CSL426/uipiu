"""外部 agent：API key 的發放與撤銷、兩種權限、每把 key 的防重送與限流。"""

import secrets
import time

import httpx
import pytest

from app import agent_tools, api_keys
from app import auth as account_auth
from app.agent_tools import ToolError
from app.main import app
from app.routers import live_orders
from tests.test_ocard import GREEN_LINE, _ocard

pytestmark = pytest.mark.usefixtures("authenticated_manager")


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _key(scope="order", owner_id=None):
    created = api_keys.create_key(owner_id or account_auth.first_admin_id(), f"agent-{scope}", scope)
    return api_keys.authenticate(created.key)


def _other_owner() -> str:
    user_id = secrets.token_urlsafe(18)
    with account_auth.auth_connection() as conn:
        conn.execute(
            "INSERT INTO users VALUES (?, ?, ?, 0, ?)", (user_id, f"other-{user_id[:6]}", "x", time.time())
        )
    return user_id


async def _with(patches, coro_factory):
    for p in patches:
        p.start()
    try:
        return await coro_factory()
    finally:
        for p in patches:
            p.stop()


ITEMS = [{"item_id": GREEN_LINE["item_id"], "quantity": 1, "option_ids": GREEN_LINE["selection_option_ids"]}]


# ---- key 的發放與撤銷 -----------------------------------------------------------


def test_key_is_shown_once_stored_hashed_and_stops_working_when_revoked(isolated_store):
    owner = account_auth.first_admin_id()
    created = api_keys.create_key(owner, "我的 agent", "order")
    assert created.key.startswith("mynig_")
    assert all(not hasattr(info, "key") for info in api_keys.list_keys(owner))
    with account_auth.auth_connection() as conn:
        stored = conn.execute("SELECT key_hash FROM api_keys WHERE id=?", (created.id,)).fetchone()[0]
    assert created.key not in stored

    principal = api_keys.authenticate(created.key)
    assert (principal.owner_id, principal.scope) == (owner, "order")
    api_keys.revoke_key(owner, created.id)
    assert api_keys.authenticate(created.key) is None
    assert api_keys.authenticate("mynig_not-a-real-key") is None


@pytest.mark.anyio
async def test_leader_manages_keys_from_the_admin_api(isolated_store):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        created = await client.post("/api-keys", json={"name": "Claude", "scope": "leader"})
        listed = await client.get("/api-keys")
        revoked = await client.delete(f"/api-keys/{created.json()['id']}")

    assert created.status_code == 200 and created.json()["key"].startswith("mynig_")
    assert listed.json()[0]["scope"] == "leader" and "key" not in listed.json()[0]
    assert revoked.status_code == 200


# ---- 點單權限 -------------------------------------------------------------------


@pytest.mark.anyio
async def test_order_key_can_browse_and_order(ocard_session_id):
    principal = _key("order")
    _, patches = _ocard()

    groups = await agent_tools.list_groups(principal)
    menu = await _with(patches, lambda: agent_tools.get_menu(principal, ocard_session_id, search="春芽"))
    _, patches = _ocard()
    placed = await _with(patches, lambda: agent_tools.place_order(principal, ocard_session_id, "小美", ITEMS))

    assert groups["groups"][0]["group_id"] == ocard_session_id and groups["groups"][0]["platform"] == "Ocard"
    assert [item["name"] for item in menu["items"]] == ["春芽綠茶"]
    assert {group["name"] for group in menu["spec_groups"]} >= {"Size", "溫度", "甜度"}
    assert placed["order_id"] == "ORDNEW01"
    assert placed["lines"] == [{"item": "春芽綠茶", "quantity": 1, "specs": "中杯、少冰、微糖", "amount": 35}]


@pytest.mark.anyio
async def test_same_idempotency_key_does_not_order_twice(ocard_session_id):
    principal = _key("order")
    _, patches = _ocard()
    first = await _with(patches, lambda: agent_tools.place_order(principal, ocard_session_id, "小美", ITEMS, idempotency_key="lunch-1"))
    mocks2, patches2 = _ocard()
    again = await _with(patches2, lambda: agent_tools.place_order(principal, ocard_session_id, "小美", ITEMS, idempotency_key="lunch-1"))

    assert again["already_submitted"] is True and again["order_id"] == first["order_id"]
    mocks2["join_group"].assert_not_awaited()


@pytest.mark.anyio
async def test_order_key_can_only_cancel_its_own_orders(ocard_session_id):
    mine, theirs = _key("order"), _key("order")
    _, patches = _ocard()
    placed = await _with(patches, lambda: agent_tools.place_order(mine, ocard_session_id, "小美", ITEMS))

    _, patches = _ocard()
    with pytest.raises(ToolError, match="自己送出"):
        await _with(patches, lambda: agent_tools.cancel_order(theirs, ocard_session_id, placed["order_id"]))
    mocks, patches = _ocard()
    result = await _with(patches, lambda: agent_tools.cancel_order(mine, ocard_session_id, placed["order_id"]))
    assert result["cancelled"] == "ORDNEW01"
    mocks["leave_group"].assert_awaited_once()


@pytest.mark.anyio
async def test_order_key_cannot_do_leader_things(ocard_session_id):
    principal = _key("order")
    with pytest.raises(ToolError, match="不能開團"):
        await agent_tools.open_group(principal, "https://order.ocard.co/teabrand/Store01?group=GRP0002")
    with pytest.raises(ToolError, match="只有點單權限"):
        await agent_tools.set_paid(principal, ocard_session_id, "小美", True)


@pytest.mark.anyio
async def test_each_key_has_its_own_submission_limit(ocard_session_id, monkeypatch):
    monkeypatch.setattr(live_orders, "SUBMIT_LIMIT_PER_IP", 1)
    live_orders._submits_by_ip.clear()
    busy, fresh = _key("order"), _key("order")
    try:
        _, patches = _ocard()
        await _with(patches, lambda: agent_tools.place_order(busy, ocard_session_id, "小美", ITEMS))
        _, patches = _ocard()
        with pytest.raises(ToolError, match="太頻繁"):
            await _with(patches, lambda: agent_tools.place_order(busy, ocard_session_id, "小美", ITEMS))
        _, patches = _ocard()
        assert (await _with(patches, lambda: agent_tools.place_order(fresh, ocard_session_id, "阿華", ITEMS)))["order_id"]
    finally:
        live_orders._submits_by_ip.clear()


# ---- 主揪權限 -------------------------------------------------------------------


@pytest.mark.anyio
async def test_leader_key_manages_only_its_owners_groups(ocard_session_id):
    leader, outsider = _key("leader"), _key("leader", owner_id=_other_owner())
    orderer = _key("order")
    _, patches = _ocard()
    placed = await _with(patches, lambda: agent_tools.place_order(orderer, ocard_session_id, "小美", ITEMS))

    with pytest.raises(ToolError, match="不是這把 key 的主揪"):
        await agent_tools.set_paid(outsider, ocard_session_id, "小美", True)
    assert (await agent_tools.set_paid(leader, ocard_session_id, "小美", True)) == {"name": "小美", "paid": True}

    _, patches = _ocard()
    listed = await _with(patches, lambda: agent_tools.list_orders(leader, ocard_session_id))
    guest = next(person for person in listed["people"] if person["name"] == "小美")
    assert guest["paid"] is True

    _, patches = _ocard()
    result = await _with(patches, lambda: agent_tools.cancel_order(leader, ocard_session_id, placed["order_id"]))
    assert result["cancelled"] == placed["order_id"]


@pytest.mark.anyio
async def test_leader_key_opens_a_group_from_a_link(isolated_store, authenticated_manager):
    leader = _key("leader")
    _, patches = _ocard()
    opened = await _with(
        patches, lambda: agent_tools.open_group(leader, "https://order.ocard.co/teabrand/Store01?group=GRP0001")
    )
    assert opened["platform"] == "Ocard" and opened["name"] == "測試茶行 測試店"
    groups = await agent_tools.list_groups(leader)
    assert groups["groups"][0]["yours"] is True
