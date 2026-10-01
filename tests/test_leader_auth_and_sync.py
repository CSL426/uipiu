from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.agent_parser import AgentCartItem, AgentOrderResponse
from app.cart_store import CartStore
from app.main import app
from app.models import StoreDeliverySummary
from app.nidin import normalize_menu_from_api
from tests.fixtures import GROUP_BUY_BRIEF, RAW_GROUP_MENU


pytestmark = pytest.mark.usefixtures("authenticated_manager")

@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def isolate_cart_store(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr("app.dependencies.cart_store", CartStore(tmp_path / "cart.db"))


def setup_session(client, store) -> str:
    menu = normalize_menu_from_api(RAW_GROUP_MENU, "1717")
    delivery = StoreDeliverySummary(
        store_id="1717",
        store_name="瑞光店",
        delivery_minimum=200,
        delivery_description="滿200外送",
    )
    summary = store.create_or_get_session(
        group_buy_url="https://order.nidin.shop/gb/menu/1717/TESTCODE01",
        store_id="1717",
        group_buy_code="TESTCODE01",
        session_name="測試團購",
        menu=menu,
        delivery_summary=delivery,
    )
    return summary.session_id


@pytest.mark.anyio
async def test_leader_auth_lifecycle():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        # Initial status: unauthenticated
        res = await client.get("/leader/auth")
        assert res.status_code == 200
        assert res.json()["is_authenticated"] is False

        # Set auth
        res = await client.post("/leader/auth", json={"token": "test_token_1234567890", "user_id": "12345678"})
        assert res.status_code == 200
        assert res.json()["is_authenticated"] is True
        assert res.json()["user_id"] == "12345678"
        assert "..." in res.json()["masked_token"]

        # Check status again
        res = await client.get("/leader/auth")
        assert res.json()["is_authenticated"] is True

        # Clear auth
        res = await client.delete("/leader/auth")
        assert res.status_code == 200
        assert res.json()["is_authenticated"] is False


@pytest.mark.anyio
async def test_agent_order_endpoint_add_to_cart():
    from app.dependencies import cart_store

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        session_id = setup_session(client, cart_store)

        mock_decision = AgentOrderResponse(
            action="add_to_cart",
            reply_message="已為小明加入大杯奶茶",
            items=[
                AgentCartItem(
                    customer_name="小明",
                    item_id="1950362419",
                    item_name="大●奶茶",
                    quantity=2,
                    selection_option_ids=["1950362926", "1950362950"],
                    selection_option_names=["正常冰", "半糖"],
                )
            ],
        )

        with patch("app.dependencies.agent_parser.parse", new=AsyncMock(return_value=mock_decision)):
            res = await client.post(
                f"/cart-sessions/{session_id}/agent-order",
                json={"text": "小明 +2 大杯奶茶 正常冰半糖", "contributor_name": "小明"},
            )

        assert res.status_code == 200
        body = res.json()
        assert body["action"] == "add_to_cart"
        assert body["items_added"] == 1
        assert body["cart_session"]["line_count"] == 1
        assert body["cart_session"]["lines"][0]["contributor_name"] == "小明"
        assert body["cart_session"]["lines"][0]["quantity"] == 2


@pytest.mark.anyio
async def test_agent_order_endpoint_clarify_reply():
    from app.dependencies import cart_store

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        session_id = setup_session(client, cart_store)

        mock_decision = AgentOrderResponse(
            action="reply",
            reply_message="小美您好，請選擇溫度規格喔！",
            items=[],
        )

        with patch("app.dependencies.agent_parser.parse", new=AsyncMock(return_value=mock_decision)):
            res = await client.post(
                f"/cart-sessions/{session_id}/agent-order",
                json={"text": "小美 阿薩姆紅茶半糖"},
            )

        assert res.status_code == 200
        body = res.json()
        assert body["action"] == "reply"
        assert body["items_added"] == 0
        assert "請選擇溫度" in body["reply_message"]


@pytest.mark.anyio
async def test_official_orders_sync_and_view():
    from app.dependencies import cart_store

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        session_id = setup_session(client, cart_store)

        # Unauthorized when leader auth is not set
        res = await client.get(f"/cart-sessions/{session_id}/official-orders")
        assert res.status_code == 401

        # Now set leader auth
        await client.post("/leader/auth", json={"token": "test_token", "user_id": "12345"})

        mock_official_payload = {
            "group_buy_head_order_info": {
                "group_buying_name": "下午茶開團",
                "brand_name": "50嵐",
                "store_name": "瑞光店",
                "total_money": 110,
                "status": 20,
            },
            "group_buy_follower_order_list": [
                {
                    "order_id": 9991,
                    "name": "同事A",
                    "phone": "0912345678",
                    "total_money": 60,
                    "product": [
                        {
                            "product_id": 1950362419,
                            "name": "大●奶茶",
                            "amount": 1,
                            "price": 60,
                            "options": ["正常冰", "微糖"],
                        }
                    ],
                },
                {
                    "order_id": 9992,
                    "name": "同事B",
                    "total_money": 50,
                    "product_simple": [
                        {
                            "product_id": 1950362390,
                            "name": "中●奶茶",
                            "amount": 1,
                            "price": 50,
                            "options": ["去冰", "無糖"],
                        }
                    ],
                },
            ],
        }

        from app.nidin import normalize_official_group_buy_info

        official_summary_obj = normalize_official_group_buy_info(mock_official_payload, "TESTCODE01")

        with patch("app.dependencies.NidinClient.fetch_group_buy_official_info", new=AsyncMock(return_value=official_summary_obj)):
            # View official orders
            res = await client.get(f"/cart-sessions/{session_id}/official-orders")
            assert res.status_code == 200
            official_data = res.json()
            assert official_data["group_buy_code"] == "TESTCODE01"
            assert len(official_data["follower_orders"]) == 2
            assert official_data["follower_orders"][0]["follower_name"] == "同事A"
            assert official_data["follower_orders"][0]["items"][0]["name"] == "大●奶茶"

            # Sync official orders to session
            sync_res = await client.post(f"/cart-sessions/{session_id}/sync-official")
            assert sync_res.status_code == 200
            synced_cart = sync_res.json()
            assert synced_cart["line_count"] == 2
            assert synced_cart["total_money"] == 110
            names = [line["contributor_name"] for line in synced_cart["lines"]]
            assert "同事A" in names
            assert "同事B" in names
