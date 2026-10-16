"""依品牌找 Nidin 分店（最近／評分相對高）、直接開新團並匯入、從本站移除團。"""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app import agent_tools, api_keys, store_search
from app import auth as account_auth
from app.agent_tools import ToolError
from app.cart_store import CartStore
from app.main import app
from app.models import StoreDeliverySummary
from app.nidin import NidinClient, normalize_menu_from_api
from app.store_search import Origin, match_brands, origin_from_addresses, rank_stores
from tests.fixtures import GROUP_BUY_BRIEF, RAW_GROUP_MENU

pytestmark = pytest.mark.usefixtures("authenticated_manager")

OFFICE = Origin(25.0, 121.5, "辦公室")


def _store(store_id, lat, lng, star, count, **extra):
    return {"id": store_id, "name": f"店{store_id}", "address": "某路", "latitude": str(lat), "longitude": str(lng),
            "rating_average_star": star, "rating_order_count": count, "is_open": 1, "is_close": 0, "order_status": 10,
            **extra}


NEAR = _store(1, 25.005, 121.5, 4.5, 40)       # 約 0.6 km
POPULAR = _store(2, 25.02, 121.5, 4.8, 300)    # 約 2.2 km
LUCKY = _store(3, 25.03, 121.5, 5.0, 1)        # 約 3.3 km，只有 1 則評價
FAR = _store(4, 25.2, 121.5, 4.95, 500)        # 約 22 km
CLOSED = _store(5, 25.001, 121.5, 4.9, 100, is_open=0)


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---- 排序 ---------------------------------------------------------------------


def test_nearest_open_store_comes_first_and_closed_ones_go_last():
    ranked = rank_stores([FAR, POPULAR, CLOSED, NEAR], OFFICE, "distance")
    assert [row["store_id"] for row in ranked] == ["1", "2", "4", "5"]
    assert ranked[0]["distance_km"] == 0.6 and ranked[-1]["orderable"] is False


def test_rating_weighs_review_count_and_ignores_far_stores():
    ranked = rank_stores([LUCKY, NEAR, POPULAR, FAR], OFFICE, "rating")
    # 300 則 4.8 星勝過 1 則 5 星；22 km 外的不列
    assert [row["store_id"] for row in ranked] == ["2", "1", "3"]


def test_without_an_origin_stores_are_still_listed():
    ranked = rank_stores([NEAR, POPULAR], None, "distance")
    assert {row["store_id"] for row in ranked} == {"1", "2"}
    assert all(row["distance_km"] is None for row in ranked)


def test_brand_matching_prefers_exact_names():
    brands = [{"id": 1, "name": "青山"}, {"id": 2, "name": "青山茶園"}, {"id": 3, "name": "可不可", "name_alias": ["KEBUKE"]}]
    assert [b["id"] for b in match_brands(brands, "青山")] == [1]
    assert [b["id"] for b in match_brands(brands, "青")] == [1, 2]
    assert [b["id"] for b in match_brands(brands, "KEBUKE")] == [3]
    assert match_brands(brands, " ") == []


def test_origin_uses_the_first_address_with_coordinates():
    origin = origin_from_addresses([
        {"loc_name": "家", "latitude": "", "longitude": ""},
        {"loc_name": "公司", "latitude": "25.06", "longitude": "121.57"},
    ])
    assert origin == Origin(25.06, 121.57, "公司")
    assert origin_from_addresses([{"loc_name": "家"}]) is None


# ---- API ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolate(monkeypatch: pytest.MonkeyPatch, tmp_path, authenticated_manager):
    monkeypatch.setattr("app.dependencies.cart_store", CartStore(tmp_path / "cart.db"))
    store_search.clear_brands_cache()
    account_auth.set_leader_credentials(account_auth.first_admin_id(), "tok-123456789", "777")


def _api():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


def _search_patches(addresses=None):
    return [
        patch.object(NidinClient, "list_brands", new=AsyncMock(return_value=[{"id": 2622, "name": "青山"}])),
        patch.object(NidinClient, "list_brand_stores", new=AsyncMock(return_value=[POPULAR, NEAR])),
        patch.object(
            NidinClient, "list_delivery_addresses",
            new=AsyncMock(return_value=addresses if addresses is not None else [
                {"loc_name": "辦公室", "latitude": "25.0", "longitude": "121.5"},
            ]),
        ),
    ]


OFFICE_ADDRESS = {"loc_name": "公司", "address": "台北市內湖區某路1號", "address_short": "內湖區某路1號",
                  "area_code": 17, "city_code": 2, "latitude": "25.06", "longitude": "121.57", "src_type": 1, "is_default": 1}
STORE_INFO = {"address": "台北市大安區某路21號", "distance_service_model": 13,
              "delivery_info": [{"delivery_type": 1, "is_default": 1}, {"delivery_type": 3, "is_default": 0}]}


def _open_patches(create=None, *, store_info=STORE_INFO, addresses=(OFFICE_ADDRESS,), route=(5049, 927),
                  set_delivery=None, lookup=None):
    menu = normalize_menu_from_api(RAW_GROUP_MENU, "1717")
    delivery = StoreDeliverySummary(store_id="1717", store_name="示範店A", delivery_minimum=500, delivery_description="外送")
    mocks = {
        "create_group_buy": create or AsyncMock(
            return_value={"order_id": 375000001, "order_sn": "M0375000001", "sharing_code": "ABCDEFGHIJ"},
        ),
        "fetch_store_info": AsyncMock(return_value=store_info),
        "list_delivery_addresses": AsyncMock(return_value=list(addresses)),
        "lookup_delivery_distance": lookup or AsyncMock(return_value=route),
        "set_group_delivery": set_delivery or AsyncMock(return_value=None),
        "fetch_group_buy_brief": AsyncMock(return_value={**GROUP_BUY_BRIEF, "store_id": 1717}),
        "fetch_menu": AsyncMock(return_value=menu),
        "fetch_store_delivery_summary": AsyncMock(return_value=delivery),
    }
    _open_patches.mocks = mocks
    return [patch.object(NidinClient, name, new=mock) for name, mock in mocks.items()]


async def _run(patches, call):
    for p in patches:
        p.start()
    try:
        return await call()
    finally:
        for p in patches:
            p.stop()


@pytest.mark.anyio
async def test_search_ranks_by_distance_from_the_leaders_nidin_address():
    async def call():
        async with _api() as client:
            return await client.get("/nidin/stores", params={"brand": "青山"})

    res = await _run(_search_patches(), call)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["brands"] == ["青山"] and body["origin"] == "辦公室"
    assert [s["store_id"] for s in body["stores"]] == ["1", "2"]


@pytest.mark.anyio
async def test_unknown_brand_is_a_clear_404():
    async def call():
        async with _api() as client:
            return await client.get("/nidin/stores", params={"brand": "不存在的店"})

    res = await _run(_search_patches(), call)
    assert res.status_code == 404 and "不存在的店" in res.json()["detail"]


@pytest.mark.anyio
async def test_opening_a_group_creates_it_on_nidin_and_imports_it():
    async def call():
        async with _api() as client:
            return await client.post("/nidin/stores/1717/group-buy")

    patches = _open_patches()
    res = await _run(patches, call)
    assert res.status_code == 200, res.text
    session = res.json()["session"]
    assert session["group_buy_url"] == "https://order.nidin.shop/gb/menu/1717/ABCDEFGHIJ"
    assert session["delivery_minimum"] == 500
    from app.dependencies import cart_store

    # 記下團長單號：之後刪跟團單、清單顯示「已匯入」都靠它
    assert cart_store.nidin_order_id(session["session_id"]) == "375000001"


@pytest.mark.anyio
async def test_store_id_must_be_numeric():
    async with _api() as client:
        res = await client.post("/nidin/stores/1717x/group-buy")
    assert res.status_code == 422


@pytest.mark.anyio
async def test_deleting_a_session_only_removes_it_from_this_site():
    async def call():
        async with _api() as client:
            opened = await client.post("/nidin/stores/1717/group-buy")
            session_id = opened.json()["session"]["session_id"]
            deleted = await client.delete(f"/cart-sessions/{session_id}")
            listed = await client.get("/cart-sessions")
            return deleted, listed

    deleted, listed = await _run(_open_patches(), call)
    assert deleted.status_code == 200
    assert listed.json() == []


# ---- agent 工具 ---------------------------------------------------------------


def _principal(scope):
    created = api_keys.create_key(account_auth.first_admin_id(), f"t-{scope}", scope)
    return api_keys.authenticate(created.key)


@pytest.mark.anyio
async def test_leader_agent_can_find_stores_and_open_a_group():
    leader = _principal("leader")
    found = await _run(_search_patches(), lambda: agent_tools.find_stores(leader, "青山", "rating"))
    assert found["stores"][0]["store_id"] == "2"
    opened = await _run(_open_patches(), lambda: agent_tools.open_new_group(leader, "1717"))
    assert opened["platform"] == "Nidin" and opened["delivery_minimum"] == 500


@pytest.mark.anyio
async def test_order_agent_cannot_open_groups():
    orderer = _principal("order")
    with pytest.raises(ToolError, match="不能開團"):
        await agent_tools.find_stores(orderer, "青山")
    with pytest.raises(ToolError, match="不能開團"):
        await agent_tools.open_new_group(orderer, "1717")


# ---- 開團後套用主揪的外送地址 ---------------------------------------------------


async def _open(patches):
    async def call():
        async with _api() as client:
            return await client.post("/nidin/stores/1717/group-buy")

    return await _run(patches, call)


@pytest.mark.anyio
async def test_new_group_is_switched_to_delivery_at_the_leaders_default_address():
    res = await _open(_open_patches())
    body = res.json()
    assert res.status_code == 200, res.text
    assert body["pickup"] == "delivery" and "公司" in body["pickup_note"]
    call = _open_patches.mocks["set_group_delivery"].await_args.kwargs
    assert (call["order_id"], call["distance"], call["duration"]) == ("375000001", 5049, 927)
    assert call["address"]["address"] == "台北市內湖區某路1號"


@pytest.mark.anyio
async def test_unknown_route_distance_keeps_the_store_default_and_says_so():
    res = await _open(_open_patches(route=None))
    body = res.json()
    assert res.status_code == 200
    assert body["pickup"] == "store_default" and "外送距離" in body["pickup_note"]
    _open_patches.mocks["set_group_delivery"].assert_not_awaited()


@pytest.mark.anyio
async def test_store_without_delivery_or_leader_without_address_keeps_the_default():
    pickup_only = {**STORE_INFO, "delivery_info": [{"delivery_type": 1, "is_default": 1}]}
    res = await _open(_open_patches(store_info=pickup_only))
    assert res.json()["pickup"] == "store_default" and "不提供外送" in res.json()["pickup_note"]

    no_coords = [{**OFFICE_ADDRESS, "latitude": "", "longitude": ""}]
    res = await _open(_open_patches(addresses=no_coords))
    assert res.json()["pickup"] == "store_default" and "沒有設定外送地址" in res.json()["pickup_note"]


@pytest.mark.anyio
async def test_a_failed_delivery_update_does_not_fail_the_group_opening():
    res = await _open(_open_patches(set_delivery=AsyncMock(side_effect=httpx.ConnectError("down"))))
    assert res.status_code == 200
    assert res.json()["pickup"] == "store_default"
    assert res.json()["session"]["group_buy_url"].endswith("/ABCDEFGHIJ")


def _capturing_client(responses):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, json.loads(request.content or b"null")))
        return httpx.Response(200, json=responses[request.url.path.rsplit("/v2", 1)[-1]])

    return NidinClient(base_url="https://nidin.test/v2", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))), seen


@pytest.mark.anyio
async def test_distance_lookup_uses_address_strings_and_never_writes_the_shared_cache():
    hit = {"status": 200, "list": [{"distance": 5049, "duration": 927}]}
    client, seen = _capturing_client({"/platform/checkAddressDistance": hit})
    route = await client.lookup_delivery_distance(token="t", user_id="1", store_info=STORE_INFO, address=OFFICE_ADDRESS)
    assert route == (5049, 927)
    method, path, body = seen[0]
    assert (method, path) == ("POST", "/v2/platform/checkAddressDistance")
    assert body == {"src_address": "台北市大安區某路21號", "dest_address": "台北市內湖區某路1號", "distance_service_model": 13}
    assert all("saveAddressDistance" not in p for _, p, _ in seen)


@pytest.mark.anyio
async def test_delivery_update_never_sends_the_field_that_closes_the_group():
    client, seen = _capturing_client({"/order/375000001/infoCouponFlow": {"status": 200}})
    await client.set_group_delivery(
        token="t", user_id="1", order_id="375000001", store_info=STORE_INFO, address=OFFICE_ADDRESS,
        distance=5049, duration=927,
    )
    method, path, body = seen[0]
    assert method == "PUT" and path == "/v2/order/375000001/infoCouponFlow"
    assert "group_buying_status" not in body
    assert body["delivery_type"] == 3 and body["delivery_distance"] == 5049
    assert (body["delivery_latitude"], body["delivery_location"]) == ("25.06", "公司")
