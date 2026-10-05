"""Ocard 揪團：網址解析、菜單與跟團清單轉換、client 的表單格式，以及透過本站送單／取消／同步。"""

import json
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qsl

import httpx
import pytest

from app.main import app
from app.models import validate_menu_snapshot
from app.ocard import (
    OcardApiError,
    OcardClient,
    check_exclusions,
    normalize_group,
    parse_group_url,
)
from tests.fixtures_ocard import (
    OCARD_GROUP,
    OCARD_GROUP_URL,
    OCARD_MENU,
    OCARD_PRODUCTS,
    OCARD_TARGET,
)

pytestmark = pytest.mark.usefixtures("authenticated_manager")

TARGET = OCARD_TARGET
MENU = OCARD_MENU.menu


def _item(name: str):
    return next(item for item in MENU.items if item.name == name)


def _option(group_name: str, option_name: str) -> str:
    group = next(group for group in MENU.groups if group.name == group_name)
    return next(option.id for option in group.options if option.name == option_name)


GREEN = _item("春芽綠茶")
GREEN_LINE = {
    "item_id": GREEN.id,
    "quantity": 1,
    "selection_option_ids": [_option("Size", "中杯"), _option("溫度", "少冰"), _option("甜度", "微糖")],
}


# ---- 網址、菜單、跟團清單 ------------------------------------------------------


def test_group_url_needs_brand_store_and_group_code():
    target = parse_group_url("https://order.ocard.co/teabrand/Store02?group=ABC123X&utm=x")
    assert (target.brand_id, target.store_id, target.group_code) == ("teabrand", "Store02", "ABC123X")
    assert target.session_store_id == "teabrand/Store02"
    assert target.url == "https://order.ocard.co/teabrand/Store02?group=ABC123X"
    with pytest.raises(ValueError, match="揪團連結"):
        parse_group_url("https://order.ocard.co/teabrand/Store02")
    with pytest.raises(ValueError):
        parse_group_url("https://evil.example/teabrand/Store02?group=ABC123X")


def test_menu_keeps_shared_spec_groups_and_maps_order_item_ids_back():
    validate_menu_snapshot(MENU)
    assert MENU.store_id == "teabrand/Store01"
    # 同一杯飲料出現在兩個分類：菜單 id 不同，跟團單上的 item_id 相同
    assert [item.name for item in MENU.items].count("熟成紅茶") == 2
    size = next(group for group in MENU.groups if group.name == "Size")
    assert size.min_select == 1 and size.max_select == 1
    assert {option.name: option.price for option in size.options} == {"中杯": 0, "大杯": 5}
    green_item_id = next(i["item_id"] for c in OCARD_PRODUCTS for i in c["items"] if i["name"] == "春芽綠茶")
    assert OCARD_MENU.menu_id_by_item_id[green_item_id] == GREEN.id


def test_hot_drinks_cannot_take_jelly_toppings():
    with pytest.raises(ValueError, match="不能和"):
        check_exclusions([_option("溫度", "熱"), _option("加料", "春梅凍")], OCARD_MENU)
    check_exclusions([_option("溫度", "少冰"), _option("加料", "春梅凍")], OCARD_MENU)


def test_group_summary_maps_orders_and_drops_host_location():
    summary = normalize_group(TARGET, OCARD_GROUP, OCARD_MENU.menu_id_by_item_id)
    assert summary.status == 10
    assert summary.head_name == "團長阿明"
    guest = next(order for order in summary.follower_orders if order.follower_name == "小美")
    assert guest.order_id == "ORDGUEST"
    item = guest.items[0]
    assert item.product_id == GREEN.id
    assert (item.options, item.option_groups) == (["中杯", "少冰", "微糖"], ["Size", "溫度", "甜度"])
    host_item = summary.follower_orders[0].items[0]
    assert (host_item.price, host_item.subtotal) == (45, 45)
    assert "121.5" not in summary.model_dump_json()


# ---- client 的請求格式 ---------------------------------------------------------


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _transport(responses: dict[str, dict], seen: list[tuple[str, dict]]):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.lstrip("/")
        seen.append((path, dict(parse_qsl(request.content.decode()))))
        return httpx.Response(200, json=responses[path])

    return httpx.MockTransport(handler)


@pytest.mark.anyio
async def test_add_item_sends_form_with_bracketed_adds_and_device_token():
    seen: list[tuple[str, dict]] = []
    responses = {"cart/addItem": {"code": 500, "data": {"group": OCARD_GROUP}}}
    async with httpx.AsyncClient(transport=_transport(responses, seen)) as http:
        await OcardClient(client=http).add_item(
            TARGET, dtk="DTK1", order_id="ORD1", order_type="DELIVERY",
            item_id=GREEN.id, count=2, option_ids=["o1", "o2"],
        )
    path, form = seen[0]
    assert path == "cart/addItem"
    assert form["adds[0][id]"] == "o1" and form["adds[1][id]"] == "o2" and form["adds[1][count]"] == "1"
    assert (form["dtk"], form["order_id"], form["group"], form["count"]) == ("DTK1", "ORD1", "GRP0001", "2")


@pytest.mark.anyio
async def test_non_success_code_is_an_api_error_with_ocards_message():
    seen: list[tuple[str, dict]] = []
    responses = {"group/join": {"code": 496, "msg": "no such data", "clearOrder": True}}
    async with httpx.AsyncClient(transport=_transport(responses, seen)) as http:
        with pytest.raises(OcardApiError, match="no such data"):
            await OcardClient(client=http).join_group(TARGET, dtk="DTK1", name="小美")


# ---- 透過本站點單即送 -----------------------------------------------------------


@pytest.fixture(autouse=True)
def isolate(isolated_store):
    pass


@pytest.fixture
def session_id(ocard_session_id):
    return ocard_session_id


def _ocard(**overrides):
    mocks = {
        "fetch_group": AsyncMock(return_value=(OCARD_GROUP, "測試茶行 測試店")),
        "fetch_menu": AsyncMock(return_value=OCARD_MENU),
        "register_device": AsyncMock(return_value="DTK-NEW"),
        "join_group": AsyncMock(return_value="ORDNEW01"),
        "add_item": AsyncMock(return_value={"group": OCARD_GROUP}),
        "leave_group": AsyncMock(return_value=None),
    }
    mocks.update(overrides)
    return mocks, [patch.object(OcardClient, name, new=mock) for name, mock in mocks.items()]


async def _call(method, path, json_body=None, *, patches=(), headers=None):
    for p in patches:
        p.start()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path, json=json_body, headers=headers)
    finally:
        for p in patches:
            p.stop()


def _order(submission_id="sub-ocard-01", name="小美"):
    return {"submission_id": submission_id, "contributor_name": name, "contributor_phone": "0912345678", "lines": [GREEN_LINE]}


@pytest.mark.anyio
async def test_pasting_an_ocard_link_opens_an_ocard_session(authenticated_manager):
    _, patches = _ocard()
    res = await _call("POST", "/cart-sessions", {"group_buy_url": OCARD_GROUP_URL}, patches=patches)

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["provider"] == "ocard"
    assert body["store_id"] == "teabrand/Store01"
    assert body["group_buy_code"] == "GRP0001"
    assert body["session_name"] == "測試茶行 測試店"


@pytest.mark.anyio
async def test_submitting_joins_the_group_as_a_guest_and_adds_each_line(session_id):
    mocks, patches = _ocard()
    res = await _call("POST", f"/cart-sessions/{session_id}/orders", _order(), patches=patches)

    assert res.status_code == 200, res.text
    assert res.json()["official_order_id"] == "ORDNEW01"
    assert mocks["join_group"].await_args.kwargs == {"dtk": "DTK-NEW", "name": "小美"}
    add = mocks["add_item"].await_args.kwargs
    assert (add["order_id"], add["item_id"], add["order_type"]) == ("ORDNEW01", GREEN.id, "DELIVERY")
    assert add["option_ids"] == GREEN_LINE["selection_option_ids"]
    # 退團要用這張單專屬的裝置權杖，送單時就得記下
    from app.dependencies import cart_store

    assert cart_store.submission_for_order(session_id, "ORDNEW01")["cancel_ref"] == "DTK-NEW"


@pytest.mark.anyio
async def test_blocked_option_combo_is_refused_before_joining(session_id):
    line = {**GREEN_LINE, "selection_option_ids": [
        _option("Size", "中杯"), _option("溫度", "熱"), _option("甜度", "微糖"), _option("加料", "春梅凍"),
    ]}
    mocks, patches = _ocard()
    res = await _call("POST", f"/cart-sessions/{session_id}/orders", {**_order(), "lines": [line]}, patches=patches)

    assert res.status_code == 422
    mocks["join_group"].assert_not_awaited()


@pytest.mark.anyio
async def test_closed_group_is_refused(session_id):
    mocks, patches = _ocard(fetch_group=AsyncMock(return_value=({**OCARD_GROUP, "status": "LOCKED"}, "")))
    res = await _call("POST", f"/cart-sessions/{session_id}/orders", _order(), patches=patches)

    assert res.status_code == 409
    assert "截止" in res.json()["detail"]
    mocks["join_group"].assert_not_awaited()


@pytest.mark.anyio
async def test_failed_add_rolls_back_the_join_and_can_be_retried(session_id):
    mocks, patches = _ocard(add_item=AsyncMock(side_effect=OcardApiError("品項已售完", 400)))
    res = await _call("POST", f"/cart-sessions/{session_id}/orders", _order(), patches=patches)

    assert res.status_code == 409
    mocks["leave_group"].assert_awaited_once()
    assert mocks["leave_group"].await_args.kwargs == {"dtk": "DTK-NEW", "order_id": "ORDNEW01"}
    _, retry_patches = _ocard()
    retry = await _call("POST", f"/cart-sessions/{session_id}/orders", _order(), patches=retry_patches)
    assert retry.status_code == 200


@pytest.mark.anyio
async def test_rollback_that_also_fails_is_reported_as_uncertain(session_id):
    _, patches = _ocard(
        add_item=AsyncMock(side_effect=httpx.ReadTimeout("slow")),
        leave_group=AsyncMock(side_effect=httpx.ReadTimeout("slow")),
    )
    res = await _call("POST", f"/cart-sessions/{session_id}/orders", _order(), patches=patches)

    assert res.status_code == 503
    assert "Ocard" in res.json()["detail"]
    # 可能已在 Ocard 留下品項：同一個 id 不能直接重送
    _, again_patches = _ocard()
    again = await _call("POST", f"/cart-sessions/{session_id}/orders", _order(), patches=again_patches)
    assert again.status_code == 409


@pytest.mark.anyio
async def test_cancel_leaves_the_group_with_the_orders_own_device_token(session_id):
    _, patches = _ocard()
    sent = await _call("POST", f"/cart-sessions/{session_id}/orders", _order(), patches=patches)
    token = sent.json()["cancel_token"]

    mocks, patches = _ocard(fetch_group=AsyncMock(return_value=({**OCARD_GROUP, "orders": []}, "")))
    res = await _call(
        "DELETE", f"/cart-sessions/{session_id}/orders/ORDNEW01", patches=patches, headers={"X-Cancel-Token": token}
    )

    assert res.status_code == 200, res.text
    assert mocks["leave_group"].await_args.kwargs == {"dtk": "DTK-NEW", "order_id": "ORDNEW01"}
    assert res.json()["lines"] == []


@pytest.mark.anyio
async def test_orders_not_sent_through_this_site_cannot_be_cancelled_here(session_id):
    mocks, patches = _ocard()
    res = await _call("DELETE", f"/cart-sessions/{session_id}/orders/ORDGUEST", patches=patches)

    assert res.status_code == 409
    assert "Ocard" in res.json()["detail"]
    mocks["leave_group"].assert_not_awaited()


@pytest.mark.anyio
async def test_refresh_copies_everyones_orders_from_ocard(session_id):
    _, patches = _ocard()
    res = await _call("POST", f"/cart-sessions/{session_id}/refresh", patches=patches)

    assert res.status_code == 200, res.text
    assert res.json()["synced"] is True
    lines = res.json()["session"]["lines"]
    assert sorted((line["contributor_name"], line["item_name"], line["line_total"]) for line in lines) == [
        ("團長阿明", "熟成冷露", 45),
        ("小美", "春芽綠茶", 35),
    ]
    guest = next(line for line in lines if line["contributor_name"] == "小美")
    # 規格對回本站菜單的選項 id，「+1 跟著點」才複製得出完整規格
    assert [s["option_id"] for s in guest["selections"]] == GREEN_LINE["selection_option_ids"]
    assert "121.5" not in json.dumps(res.json())
