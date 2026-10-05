"""/mcp：外部 agent 以 API key 走 MCP（streamable HTTP，JSON-RPC）呼叫點單工具。"""

import httpx
import pytest

from app import api_keys
from app import auth as account_auth
from app.main import app
from tests.test_ocard import GREEN_LINE, _ocard

pytestmark = pytest.mark.usefixtures("authenticated_manager")

HEADERS = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _key(scope="order") -> str:
    return api_keys.create_key(account_auth.first_admin_id(), f"mcp-{scope}", scope).key


async def _rpc(method, params=None, *, key, patches=()):
    for p in patches:
        p.start()
    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.post(
                    "/mcp",
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                    headers={**HEADERS, **({"Authorization": f"Bearer {key}"} if key else {})},
                )
    finally:
        for p in patches:
            p.stop()


async def _call(tool, arguments, *, key, patches=()):
    res = await _rpc("tools/call", {"name": tool, "arguments": arguments}, key=key, patches=patches)
    assert res.status_code == 200, res.text
    return res.json()["result"]


@pytest.mark.anyio
async def test_requests_without_a_valid_key_are_rejected(isolated_store):
    assert (await _rpc("tools/list", key=None)).status_code == 401
    assert (await _rpc("tools/list", key="mynig_wrong")).status_code == 401
    created = api_keys.create_key(account_auth.first_admin_id(), "old", "order")
    api_keys.revoke_key(account_auth.first_admin_id(), created.id)
    assert (await _rpc("tools/list", key=created.key)).status_code == 401


@pytest.mark.anyio
async def test_lists_the_ordering_tools(isolated_store):
    res = await _rpc("tools/list", key=_key())
    names = {tool["name"] for tool in res.json()["result"]["tools"]}
    assert names == {"list_groups", "get_menu", "place_order", "list_orders", "cancel_order", "open_group", "set_paid"}


@pytest.mark.anyio
async def test_agent_orders_through_mcp(ocard_session_id):
    key = _key()
    groups = await _call("list_groups", {}, key=key)
    assert groups["structuredContent"]["groups"][0]["group_id"] == ocard_session_id

    _, patches = _ocard()
    placed = await _call(
        "place_order",
        {
            "group_id": ocard_session_id,
            "name": "小美",
            "items": [{"item_id": GREEN_LINE["item_id"], "option_ids": GREEN_LINE["selection_option_ids"]}],
            "idempotency_key": "mcp-1",
        },
        key=key,
        patches=patches,
    )
    assert placed.get("isError") is not True, placed
    assert placed["structuredContent"]["order_id"] == "ORDNEW01"
    assert placed["structuredContent"]["total"] == 35


@pytest.mark.anyio
async def test_order_key_gets_a_readable_error_for_leader_tools(isolated_store):
    result = await _call(
        "open_group", {"link": "https://order.ocard.co/teabrand/Store01?group=GRP0001"}, key=_key("order")
    )
    assert result["isError"] is True
    assert "不能開團" in result["content"][0]["text"]
