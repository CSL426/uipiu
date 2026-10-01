import httpx
import pytest

from app.cart_store import CartStore
from app.main import app
from app.models import GuestOrderReceipt, StoreDeliverySummary
from app.nidin import normalize_menu

from tests.fixtures import RAW_MENU, STORE_INFO


pytestmark = pytest.mark.usefixtures("authenticated_manager")

@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def isolate_cart_store(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr("app.dependencies.cart_store", CartStore(tmp_path / "cart.db"))


@pytest.mark.anyio
async def test_healthcheck():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_resolve_store_endpoint():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/resolve-store",
            json={"menu_url": "https://order.nidin.shop/menu/9736?from=line"},
        )

    assert response.status_code == 200
    assert response.json() == {"store_id": "9736"}


@pytest.mark.anyio
async def test_parse_order_endpoint():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/parse-order",
            json={"text": "+1 珍奶 少糖 去冰", "menu": RAW_MENU},
        )

    assert response.status_code == 200
    payload = response.json()

    assert payload["quantity"] == 1
    assert payload["item"]["name"] == "珍珠奶茶"
    assert [selection["option_name"] for selection in payload["selections"]] == ["半糖", "去冰"]


@pytest.mark.anyio
async def test_guest_order_once_endpoint(monkeypatch: pytest.MonkeyPatch):
    class FakeClient:
        async def order_guest_once(self, **kwargs):
            assert kwargs["group_buy_url"] == "https://order.nidin.shop/gb/menu/1717/TESTCODE01"
            assert kwargs["guest_name"] == "Codex測試"
            assert kwargs["guest_phone"] == "0987654321"
            assert kwargs["order_text"] == "+1 中●阿薩姆紅茶 正常冰 正常甜"
            return GuestOrderReceipt(
                order_id=300000001,
                order_status=31,
                store_id="1717",
                group_buy_code="TESTCODE01",
                guest_user_id=9000001,
                total_money=35,
                item_name="中●阿薩姆紅茶",
            )

    monkeypatch.setattr("app.dependencies.get_nidin_client", lambda: FakeClient())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/guest/order-once",
            json={
                "group_buy_url": "https://order.nidin.shop/gb/menu/1717/TESTCODE01",
                "guest_name": "Codex測試",
                "guest_phone": "0987654321",
                "order_text": "+1 中●阿薩姆紅茶 正常冰 正常甜",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["order_id"] == 300000001
    assert payload["order_status"] == 31
    assert payload["total_money"] == 35


@pytest.mark.anyio
async def test_guest_order_once_endpoint_allows_missing_phone(monkeypatch: pytest.MonkeyPatch):
    class FakeClient:
        async def order_guest_once(self, **kwargs):
            assert kwargs["group_buy_url"] == "https://order.nidin.shop/gb/menu/1717/TESTCODE01"
            assert kwargs["guest_name"] == "Codex測試"
            assert kwargs["guest_phone"] == ""
            assert kwargs["order_text"] == "+1 中●阿薩姆紅茶 正常冰 正常甜"
            return GuestOrderReceipt(
                order_id=300000001,
                order_status=31,
                store_id="1717",
                group_buy_code="TESTCODE01",
                guest_user_id=9000001,
                total_money=35,
                item_name="中●阿薩姆紅茶",
            )

    monkeypatch.setattr("app.dependencies.get_nidin_client", lambda: FakeClient())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/guest/order-once",
            json={
                "group_buy_url": "https://order.nidin.shop/gb/menu/1717/TESTCODE01",
                "guest_name": "Codex測試",
                "order_text": "+1 中●阿薩姆紅茶 正常冰 正常甜",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["order_id"] == 300000001


@pytest.mark.anyio
async def test_store_delivery_summary_endpoint(monkeypatch: pytest.MonkeyPatch):
    class FakeClient:
        async def fetch_store_delivery_summary(self, store_id: str):
            assert store_id == "1717"
            return StoreDeliverySummary(
                store_id="1717",
                store_name="瑞光店",
                delivery_minimum=250,
                delivery_description="最低滿250外送",
                order_status_reason="滿250元有外送",
            )

    monkeypatch.setattr("app.dependencies.get_nidin_client", lambda: FakeClient())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/stores/1717/delivery-summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "store_id": "1717",
        "store_name": "瑞光店",
        "delivery_minimum": 250,
        "delivery_description": "最低滿250外送",
        "order_status_reason": "滿250元有外送",
    }


@pytest.mark.anyio
async def test_submit_cart_session_guest_order_endpoint(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr("app.dependencies.cart_store", CartStore(tmp_path / "cart.db"))
    menu = normalize_menu(RAW_MENU)

    class FakeClient:
        async def fetch_group_buy_brief(self, group_buy_code: str):
            assert group_buy_code == "TESTCODE01"
            return {
                "brand_id": 43,
                "menu_id": 2358673,
                "store_id": 1717,
                "menu_ver_stamp": 672773986,
                "brand_name": "50嵐",
                "store_name": "瑞光店",
            }

        async def fetch_menu(self, store_id: str):
            assert store_id == "1717"
            return menu

        async def fetch_store_delivery_summary(self, store_id: str):
            assert store_id == "1717"
            return StoreDeliverySummary(
                store_id="1717",
                store_name="瑞光店",
                delivery_minimum=250,
                delivery_description="最低滿250外送",
                order_status_reason="滿250元有外送",
            )

        async def order_guest_cart_session(self, **kwargs):
            assert kwargs["group_buy_url"] == "https://order.nidin.shop/gb/menu/1717/TESTCODE01"
            assert kwargs["guest_name"] == "團主"
            assert kwargs["guest_phone"] == "0987654321"
            assert len(kwargs["lines"]) == 1
            assert kwargs["lines"][0].item_name == "珍珠奶茶"
            return GuestOrderReceipt(
                order_id=305443999,
                order_status=31,
                store_id="1717",
                group_buy_code="TESTCODE01",
                guest_user_id=9000001,
                total_money=70,
                item_name="珍珠奶茶",
            )

    monkeypatch.setattr("app.dependencies.get_nidin_client", lambda: FakeClient())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/cart-sessions",
            json={"group_buy_url": "https://order.nidin.shop/gb/menu/1717/TESTCODE01"},
        )
        session_id = create_response.json()["session_id"]

        add_response = await client.post(
            f"/cart-sessions/{session_id}/lines",
            json={
                "contributor_name": "小明",
                "lines": [
                    {
                        "item_id": "drink_1",
                        "quantity": 1,
                        "selection_option_ids": ["sweet_50", "ice_0"],
                    }
                ],
            },
        )
        assert add_response.status_code == 200

        submit_response = await client.post(
            f"/cart-sessions/{session_id}/submit-guest-order",
            json={
                "guest_name": "團主",
                "guest_phone": "0987654321",
            },
        )

    assert submit_response.status_code == 200
    payload = submit_response.json()
    assert payload["receipt"]["order_id"] == 305443999
    assert payload["receipt"]["total_money"] == 70
    assert payload["cart_session"]["line_count"] == 0
    assert payload["cart_session"]["total_money"] == 0
    assert payload["cart_session"]["lines"] == []


@pytest.mark.anyio
async def test_agent_order_dry_run_and_commit(monkeypatch: pytest.MonkeyPatch):
    class FakeClient:
        async def fetch_group_buy_brief(self, group_buy_code: str):
            from tests.fixtures import GROUP_BUY_BRIEF
            return GROUP_BUY_BRIEF["group_buy_brief"]

        async def fetch_menu(self, store_id: str):
            from app.nidin import normalize_menu_from_api
            from tests.fixtures import RAW_GROUP_MENU
            return normalize_menu_from_api(RAW_GROUP_MENU, store_id)

        async def fetch_store_delivery_summary(self, store_id: str):
            return StoreDeliverySummary(
                store_id=store_id,
                store_name="50嵐 瑞光店",
                delivery_minimum=200,
                delivery_description="滿200外送",
            )

    monkeypatch.setattr("app.dependencies.get_nidin_client", lambda: FakeClient())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_res = await client.post(
            "/cart-sessions",
            json={"group_buy_url": "https://order.nidin.shop/gb/menu/1717/TESTCODE01"},
        )
        session_id = create_res.json()["session_id"]

        # 1. 跑 dry_run: True -> 應回傳 preview 且不寫入購物車
        dryrun_res = await client.post(
            f"/cart-sessions/{session_id}/agent-order",
            json={"text": "小明要一杯中杯阿薩姆紅茶 半糖正常冰", "contributor_name": "小明", "dry_run": True},
        )
        assert dryrun_res.status_code == 200
        dryrun_data = dryrun_res.json()
        assert dryrun_data["action"] == "preview"
        assert len(dryrun_data["parsed_items"]) == 1
        item_preview = dryrun_data["parsed_items"][0]
        assert "阿薩姆紅茶" in item_preview["item_name"]
        assert dryrun_data["items_added"] == 0
        # 確認購物車依舊是 0 筆
        sess_res = await client.get(f"/cart-sessions/{session_id}")
        assert sess_res.json()["line_count"] == 0

        # 2. 正式 commit (dry_run: False) -> 應寫入購物車
        commit_res = await client.post(
            f"/cart-sessions/{session_id}/agent-order",
            json={"text": "小明要一杯中杯阿薩姆紅茶 半糖正常冰", "contributor_name": "小明", "dry_run": False},
        )
        assert commit_res.status_code == 200
        commit_data = commit_res.json()
        assert commit_data["action"] == "add_to_cart"
        assert commit_data["items_added"] == 1
        assert commit_data["cart_session"]["line_count"] == 1

