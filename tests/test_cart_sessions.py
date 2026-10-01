import httpx
import pytest

from app.cart_store import CartStore
from app.main import app
from app.models import NormalizedMenu, StoreDeliverySummary
from app.nidin import normalize_menu_from_api
from tests.fixtures import GROUP_BUY_BRIEF, RAW_GROUP_MENU


pytestmark = pytest.mark.usefixtures("authenticated_manager")

@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def isolate_cart_store(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr("app.dependencies.cart_store", CartStore(tmp_path / "cart.db"))


def build_menu() -> NormalizedMenu:
    return normalize_menu_from_api(RAW_GROUP_MENU, "1717")


def build_store_summary() -> StoreDeliverySummary:
    return StoreDeliverySummary(
        store_id="1717",
        store_name="瑞光店",
        delivery_minimum=250,
        delivery_description="最低滿250外送",
        order_status_reason="滿250元有外送",
    )


@pytest.mark.anyio
async def test_create_cart_session_endpoint(monkeypatch: pytest.MonkeyPatch):
    menu = build_menu()
    store_summary = build_store_summary()

    class FakeClient:
        async def fetch_group_buy_brief(self, group_buy_code: str):
            assert group_buy_code == "TESTCODE01"
            return GROUP_BUY_BRIEF["group_buy_brief"]

        async def fetch_menu(self, store_id: str):
            assert store_id == "1717"
            return menu

        async def fetch_store_delivery_summary(self, store_id: str):
            assert store_id == "1717"
            return store_summary

    monkeypatch.setattr("app.dependencies.get_nidin_client", lambda: FakeClient())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/cart-sessions",
            json={"group_buy_url": "https://order.nidin.shop/gb/menu/1717/TESTCODE01"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["store_id"] == "1717"
    assert payload["group_buy_code"] == "TESTCODE01"
    assert payload["session_name"] == "50嵐 瑞光店"
    assert payload["store_name"] == "瑞光店"
    assert payload["line_count"] == 0
    assert payload["total_money"] == 0
    assert payload["delivery_minimum"] == 250


@pytest.mark.anyio
async def test_create_cart_session_endpoint_uses_custom_session_name(monkeypatch: pytest.MonkeyPatch):
    menu = build_menu()
    store_summary = build_store_summary()

    class FakeClient:
        async def fetch_group_buy_brief(self, group_buy_code: str):
            assert group_buy_code == "TESTCODE01"
            return GROUP_BUY_BRIEF["group_buy_brief"]

        async def fetch_menu(self, store_id: str):
            assert store_id == "1717"
            return menu

        async def fetch_store_delivery_summary(self, store_id: str):
            assert store_id == "1717"
            return store_summary

    monkeypatch.setattr("app.dependencies.get_nidin_client", lambda: FakeClient())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/cart-sessions",
            json={
                "group_buy_url": "https://order.nidin.shop/gb/menu/1717/TESTCODE01",
                "session_name": "下午茶團",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_name"] == "下午茶團"
    assert payload["store_name"] == "瑞光店"


@pytest.mark.anyio
async def test_add_cart_lines_and_read_cart_detail(monkeypatch: pytest.MonkeyPatch):
    menu = build_menu()
    store_summary = build_store_summary()

    class FakeClient:
        async def fetch_group_buy_brief(self, group_buy_code: str):
            assert group_buy_code == "TESTCODE01"
            return GROUP_BUY_BRIEF["group_buy_brief"]

        async def fetch_menu(self, store_id: str):
            assert store_id == "1717"
            return menu

        async def fetch_store_delivery_summary(self, store_id: str):
            assert store_id == "1717"
            return store_summary

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
                        "item_id": "1950362347",
                        "quantity": 2,
                        "selection_option_ids": ["1950362926", "1950362968", "1950363133"],
                    }
                ],
            },
        )
        detail_response = await client.get(f"/cart-sessions/{session_id}")
        list_response = await client.get("/cart-sessions")

    assert add_response.status_code == 200
    add_payload = add_response.json()
    assert add_payload["line_count"] == 1
    assert add_payload["total_money"] == 90
    assert add_payload["lines"][0]["contributor_name"] == "小明"
    assert add_payload["lines"][0]["item_name"] == "中●阿薩姆紅茶"
    assert add_payload["lines"][0]["line_total"] == 90
    assert [selection["option_name"] for selection in add_payload["lines"][0]["selections"]] == [
        "正常冰",
        "正常甜",
        "+珍珠",
    ]

    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["line_count"] == 1
    assert detail_payload["total_money"] == 90

    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert len(list_payload) == 1
    assert list_payload[0]["session_id"] == session_id
    assert list_payload[0]["total_money"] == 90
    assert list_payload[0]["line_count"] == 1


@pytest.mark.anyio
async def test_clear_cart_session_lines(monkeypatch: pytest.MonkeyPatch):
    menu = build_menu()
    store_summary = build_store_summary()

    class FakeClient:
        async def fetch_group_buy_brief(self, group_buy_code: str):
            assert group_buy_code == "TESTCODE01"
            return GROUP_BUY_BRIEF["group_buy_brief"]

        async def fetch_menu(self, store_id: str):
            assert store_id == "1717"
            return menu

        async def fetch_store_delivery_summary(self, store_id: str):
            assert store_id == "1717"
            return store_summary

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

        await client.post(
            f"/cart-sessions/{session_id}/lines",
            json={
                "contributor_name": "小明",
                "lines": [
                    {
                        "item_id": "1950362347",
                        "quantity": 1,
                        "selection_option_ids": ["1950362926", "1950362968"],
                    }
                ],
            },
        )
        clear_response = await client.delete(f"/cart-sessions/{session_id}/lines")
        detail_response = await client.get(f"/cart-sessions/{session_id}")

    assert clear_response.status_code == 200
    clear_payload = clear_response.json()
    assert clear_payload["line_count"] == 0
    assert clear_payload["total_money"] == 0
    assert clear_payload["lines"] == []

    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["line_count"] == 0
    assert detail_payload["total_money"] == 0
    assert detail_payload["lines"] == []


@pytest.mark.anyio
async def test_get_cart_session_menu_refreshes_stored_menu(monkeypatch: pytest.MonkeyPatch):
    menu_without_images = normalize_menu_from_api(RAW_GROUP_MENU, "1717")
    menu_with_images = normalize_menu_from_api(
        RAW_GROUP_MENU,
        "1717",
        product_image_urls={"1950362347": "https://cdn-order-v3.nidin.shop/product/images/red-tea.png"},
    )
    store_summary = build_store_summary()

    class FakeClient:
        def __init__(self):
            self.menu = menu_without_images

        async def fetch_group_buy_brief(self, group_buy_code: str):
            assert group_buy_code == "TESTCODE01"
            return GROUP_BUY_BRIEF["group_buy_brief"]

        async def fetch_menu(self, store_id: str):
            assert store_id == "1717"
            return self.menu

        async def fetch_store_delivery_summary(self, store_id: str):
            assert store_id == "1717"
            return store_summary

    fake_client = FakeClient()
    monkeypatch.setattr("app.dependencies.get_nidin_client", lambda: fake_client)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/cart-sessions",
            json={"group_buy_url": "https://order.nidin.shop/gb/menu/1717/TESTCODE01"},
        )
        session_id = create_response.json()["session_id"]

        fake_client.menu = menu_with_images

        menu_response = await client.get(f"/cart-sessions/{session_id}/menu")

    assert menu_response.status_code == 200
    payload = menu_response.json()
    assert payload["items"][0]["image_url"] == "https://cdn-order-v3.nidin.shop/product/images/red-tea.png"
