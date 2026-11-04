"""python -m app.cli：skill 依賴的主揪指令。輸出是 JSON，失敗 exit 1，且走得到和 MCP 相同的流程。"""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app import cli
from app.nidin import NidinClient
from app.ocard import OcardClient
from tests.fixtures_ocard import OCARD_GROUP
from tests.test_ocard import GREEN_LINE, _ocard

pytestmark = pytest.mark.usefixtures("authenticated_manager")


def _run(capsys, *argv, patches=()):
    for p in patches:
        p.start()
    try:
        code = cli.main(list(argv))
    finally:
        for p in patches:
            p.stop()
    return code, json.loads(capsys.readouterr().out)


def test_order_list_and_cancel_through_the_cli(ocard_session_id, capsys):
    item = json.dumps({"item_id": GREEN_LINE["item_id"], "option_ids": GREEN_LINE["selection_option_ids"]})
    code, placed = _run(capsys, "order", ocard_session_id, "--name", "小美", "--item", item, patches=_ocard()[1])
    assert code == 0 and placed["order_id"] == "ORDNEW01" and placed["total"] == 35

    code, groups = _run(capsys, "groups")
    assert code == 0 and groups["groups"][0]["group_id"] == ocard_session_id

    code, cancelled = _run(capsys, "cancel", ocard_session_id, "ORDNEW01", patches=_ocard()[1])
    assert code == 0 and cancelled["cancelled"] == "ORDNEW01"


def test_errors_are_json_with_a_nonzero_exit(isolated_store, capsys):
    code, body = _run(capsys, "status", "no-such-group")
    assert code == 1 and "找不到" in body["error"]

    code, body = _run(capsys, "order", "x", "--name", "小美", "--item", "not-json")
    assert code == 1 and "JSON" in body["error"]


def test_status_reports_pickup_without_printing_the_host_location(ocard_session_id, capsys):
    code, body = _run(capsys, "status", ocard_session_id, patches=_ocard()[1])
    assert code == 0
    assert body["open"] is True and body["pickup"] == "delivery" and body["has_delivery_address"] is True
    assert "121.5" not in json.dumps(body)


def test_status_of_a_closed_group_says_why(ocard_session_id, capsys):
    closed = AsyncMock(return_value=({**OCARD_GROUP, "status": "LOCKED"}, ""))
    code, body = _run(capsys, "status", ocard_session_id, patches=_ocard(fetch_group=closed)[1])
    assert code == 0 and body["open"] is False and "截止" in body["closed_reason"]


def test_status_reads_the_nidin_pickup_from_the_head_order(authenticated_manager, isolated_store, capsys):
    from app import auth
    from app.dependencies import cart_store
    from app.nidin import normalize_menu_from_api
    from tests.fixtures import RAW_GROUP_MENU

    owner = auth.first_admin_id()
    auth.set_leader_credentials(owner, "tok-123456789", "777")
    session = cart_store.create_or_get_session(
        group_buy_url="https://order.nidin.shop/gb/menu/1717/ABCDEFGHIJ", store_id="1717", group_buy_code="ABCDEFGHIJ",
        session_name="測試", menu=normalize_menu_from_api(RAW_GROUP_MENU, "1717"), delivery_summary=None, owner_id=owner,
    )
    cart_store.link_nidin_order(session.session_id, "375000001")
    patches = [
        patch.object(NidinClient, "fetch_group_buy_brief", new=AsyncMock(return_value={"group_buying_status": 10})),
        patch.object(NidinClient, "fetch_order_basic", new=AsyncMock(
            return_value={"delivery_type": 3, "delivery_address": "某路1號", "delivery_distance": 5049},
        )),
    ]
    code, body = _run(capsys, "status", session.session_id, patches=patches)
    assert code == 0, body
    assert (body["pickup"], body["has_delivery_address"], body["distance_m"]) == ("delivery", True, 5049)
    assert "某路" not in json.dumps(body)


def test_leader_address_picks_the_first_usable_nidin_address(authenticated_manager, isolated_store, capsys):
    from app import auth

    auth.set_leader_credentials(auth.first_admin_id(), "tok-123456789", "777")
    addresses = [
        {"loc_name": "沒座標", "address": "某路2號", "latitude": "", "longitude": ""},
        {"loc_name": "公司", "address": "某路1號", "latitude": "25.06", "longitude": "121.57"},
    ]
    patches = [patch.object(NidinClient, "list_delivery_addresses", new=AsyncMock(return_value=addresses))]
    code, body = _run(capsys, "leader-address", patches=patches)
    assert code == 0 and body == {"address": "某路1號", "note": "公司"}

    patches = [patch.object(NidinClient, "list_delivery_addresses", new=AsyncMock(return_value=addresses[:1]))]
    code, body = _run(capsys, "leader-address", patches=patches)
    assert code == 1 and "沒有可用" in body["error"]


def test_remove_only_deletes_the_local_group(ocard_session_id, capsys):
    mocks, patches = _ocard()
    code, body = _run(capsys, "remove", ocard_session_id, patches=patches)
    assert code == 0 and body["removed"] == ocard_session_id
    mocks["leave_group"].assert_not_awaited()
    assert _run(capsys, "groups")[1]["groups"] == []


def test_connection_failures_are_reported_not_raised(ocard_session_id, capsys):
    with patch.object(OcardClient, "fetch_menu", new=AsyncMock(side_effect=httpx.ConnectError("down"))):
        code, body = _run(capsys, "menu", ocard_session_id, "--search", "春芽")
    # 連不上時退回上次存的菜單，agent 還是拿得到東西
    assert code == 0 and body["items"][0]["name"] == "春芽綠茶"


@pytest.mark.anyio
async def test_disabled_items_maps_ids_to_names():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/onShelfMenu"):
            return httpx.Response(200, json={"menu": {
                "product_list_json": {"schema": {"name": 1}, "data": {"11": [11, "海鹽奶蓋春青"], "12": [12, "冬青"]}},
                "option_list_json": {"schema": {"name": 1}, "data": {"21": [21, "海鹽奶蓋"], "22": [22, "珍珠"]}},
            }})
        return httpx.Response(200, json={
            "product_list": {"category": {"9": [{"product_id": 11}]}, "package": {}, "block": {}},
            "option_list": {"5": [{"product_id": 21}]},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await NidinClient(base_url="https://nidin.test/v2", client=http).list_disabled_items("1717")
    assert result == {"products": ["海鹽奶蓋春青"], "options": ["海鹽奶蓋"]}


@pytest.fixture
def anyio_backend():
    return "asyncio"
