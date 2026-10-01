from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app import auth as account_auth
from app.cart_store import CartStore
from app.main import app
from app.models import StoreDeliverySummary
from app.nidin import NidinClient, normalize_menu_from_api, normalize_official_group_buy_info
from app.nidin.client import NidinTokenExpired
from tests.fixtures import GROUP_BUY_BRIEF, RAW_GROUP_MENU
from tests.test_official_sync_real_shape import REAL_SHAPE, _product


pytestmark = pytest.mark.usefixtures("authenticated_manager")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def isolate_cart_store(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr("app.dependencies.cart_store", CartStore(tmp_path / "cart.db"))


@pytest.fixture(autouse=True)
def bound_credentials(authenticated_manager, isolate_cart_store):
    account_auth.set_leader_credentials(account_auth.first_admin_id(), "tok-123456789", "777")


def _api():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


CART = {"order_id": 300000002, "order_type": 2, "store_id": 1717, "brand_name": "得正", "store_name": "示範店A",
        "head_group_buying_name": "測試開的團", "create_time": "2026-09-24 16:57:37", "total_money": 0, "sub_order_amount": 0}
ORDERS = [
    {"order_id": 300000003, "order_type": 2, "status": 130, "status_msg": "完成", "store_id": 27808, "brand_name": "示範品牌",
     "store_name": "示範店B", "head_group_buying_name": "測試開的團", "order_time": "2026-09-23 13:25:29", "total_money": 910, "sub_order_amount": 14},
    {"order_id": 1, "order_type": 1, "status": 130, "status_msg": "完成", "store_id": 1, "brand_name": "50嵐", "store_name": "示範店C"},
    {"order_id": 2, "order_type": 3, "status": 35, "status_msg": "跟團成功", "store_id": 2, "brand_name": "迷客夏", "store_name": "瑞光店"},
]


@pytest.mark.anyio
async def test_lists_only_group_head_orders_with_open_ones_first():
    with patch.object(NidinClient, "list_carts", new=AsyncMock(return_value=[CART, {**CART, "order_id": 9, "order_type": 1}])), \
            patch.object(NidinClient, "list_order_history", new=AsyncMock(return_value=ORDERS)):
        async with _api() as client:
            res = await client.get("/nidin/group-buys")

    assert res.status_code == 200
    body = res.json()
    assert [g["order_id"] for g in body] == ["300000002", "300000003"]
    assert body[0]["session_id"] is None
    assert body[0]["title"] == "得正 示範店A"
    assert body[0]["submitted"] is False and body[0]["status_text"] == "揪團中（尚未送單）"
    assert body[1]["status_text"] == "完成"
    assert body[1]["follower_count"] == 14 and body[1]["total_money"] == 910


@pytest.mark.anyio
async def test_list_relogs_in_when_the_token_was_revoked():
    uid = account_auth.first_admin_id()
    account_auth.set_leader_credentials(uid, "revoked", "777", keep_login=True)
    account_auth.save_nidin_login(uid, "0912345678", "pw", "dev")
    carts = AsyncMock(side_effect=[NidinTokenExpired(), [CART]])
    with patch.object(NidinClient, "list_carts", new=carts), \
            patch.object(NidinClient, "list_order_history", new=AsyncMock(return_value=[])), \
            patch.object(NidinClient, "login_web", new=AsyncMock(return_value=("fresh-token-1", "777"))):
        async with _api() as client:
            res = await client.get("/nidin/group-buys")

    assert res.status_code == 200
    assert [g["order_id"] for g in res.json()] == ["300000002"]


def _import_patches(official_side_effect):
    menu = normalize_menu_from_api(RAW_GROUP_MENU, "1717")
    delivery = StoreDeliverySummary(store_id="1717", store_name="示範店A", delivery_minimum=400, delivery_description="外送")
    return [
        patch.object(NidinClient, "fetch_share_code", new=AsyncMock(return_value="ABCDEFGHIJ")),
        patch.object(NidinClient, "fetch_group_buy_brief", new=AsyncMock(return_value={**GROUP_BUY_BRIEF, "store_id": 1717})),
        patch.object(NidinClient, "fetch_menu", new=AsyncMock(return_value=menu)),
        patch.object(NidinClient, "fetch_store_delivery_summary", new=AsyncMock(return_value=delivery)),
        patch.object(NidinClient, "fetch_group_buy_official_info", new=AsyncMock(side_effect=official_side_effect)),
    ]


@pytest.mark.anyio
async def test_import_creates_the_session_links_it_and_syncs_head_and_followers():
    with_head = {**REAL_SHAPE, "group_buy_head_order_info": {
        **REAL_SHAPE["group_buy_head_order_info"], "head_sn": "M0300000003",
        "product": [_product("NI_特上503L", 55, 55, [("甜度", "二分糖", 0)])],
    }}
    official = normalize_official_group_buy_info(with_head, "ABCDEFGHIJ")
    patches = _import_patches([official])
    for p in patches:
        p.start()
    try:
        async with _api() as client:
            res = await client.post("/nidin/group-buys/300000003/import")
    finally:
        for p in patches:
            p.stop()

    assert res.status_code == 200
    body = res.json()
    assert body["sync_error"] is None
    session = body["session"]
    assert session["group_buy_url"] == "https://order.nidin.shop/gb/menu/1717/ABCDEFGHIJ"
    assert sorted(line["contributor_name"] for line in session["lines"]) == sorted(["小明", "小美", "團長"])
    assert session["total_money"] == 45 + 30 + 55
    from app.dependencies import cart_store
    assert cart_store.sessions_by_nidin_order(account_auth.first_admin_id()) == {"300000003": session["session_id"]}


@pytest.mark.anyio
async def test_import_keeps_the_session_when_the_first_sync_fails():
    patches = _import_patches(NidinTokenExpired())
    for p in patches:
        p.start()
    try:
        async with _api() as client:
            res = await client.post("/nidin/group-buys/300000003/import")
    finally:
        for p in patches:
            p.stop()

    assert res.status_code == 200
    assert "重新綁定" in res.json()["sync_error"]
    assert res.json()["session"]["group_buy_code"] == "ABCDEFGHIJ"
