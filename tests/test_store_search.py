"""依品牌找 Nidin 分店（最近／評分相對高）、直接開新團並匯入、從本站移除團。"""

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


def _open_patches(create=None):
    menu = normalize_menu_from_api(RAW_GROUP_MENU, "1717")
    delivery = StoreDeliverySummary(store_id="1717", store_name="示範店A", delivery_minimum=500, delivery_description="外送")
    return [
        patch.object(NidinClient, "create_group_buy", new=create or AsyncMock(
            return_value={"order_id": 375000001, "order_sn": "M0375000001", "sharing_code": "ABCDEFGHIJ"},
        )),
        patch.object(NidinClient, "fetch_group_buy_brief", new=AsyncMock(return_value={**GROUP_BUY_BRIEF, "store_id": 1717})),
        patch.object(NidinClient, "fetch_menu", new=AsyncMock(return_value=menu)),
        patch.object(NidinClient, "fetch_store_delivery_summary", new=AsyncMock(return_value=delivery)),
    ]


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
