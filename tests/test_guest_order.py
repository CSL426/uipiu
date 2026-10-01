import json
import re

import httpx
import pytest

from app.nidin import (
    NidinClient,
    _extract_product_image_urls,
    build_group_buy_cart_payload,
    extract_group_buy_target,
    _normalize_store_delivery_summary,
    normalize_menu_from_api,
)
from app.parser import parse_order_lines, parse_order_text
from tests.fixtures import GROUP_BUY_BRIEF, RAW_GROUP_MENU


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_extract_group_buy_target_from_gb_urls():
    target = extract_group_buy_target("https://order.nidin.shop/gb/menu/1717/TESTCODE01")
    assert target.store_id == "1717"
    assert target.group_buy_code == "TESTCODE01"

    login_target = extract_group_buy_target("https://order.nidin.shop/gb/login/1717/TESTCODE01")
    assert login_target.store_id == "1717"
    assert login_target.group_buy_code == "TESTCODE01"


def test_build_group_buy_cart_payload_from_parsed_order():
    menu = normalize_menu_from_api(RAW_GROUP_MENU, "1717")
    parsed = parse_order_text("+2 中●阿薩姆紅茶 正常冰 正常甜", menu)

    payload = build_group_buy_cart_payload(
        group_buy_code="TESTCODE01",
        group_buy_brief=GROUP_BUY_BRIEF["group_buy_brief"],
        menu_payload=RAW_GROUP_MENU,
        parsed_order=parsed,
    )

    assert payload["group_buy_code"] == "TESTCODE01"
    assert payload["brand_id"] == 43
    assert payload["menu_id"] == 2358673
    assert payload["store_id"] == 1717
    assert payload["menu_ver_stamp"] == 672773986
    assert payload["total_money"] == 70

    products = json.loads(payload["products"])
    assert products["items"][0]["product_id"] == 1950362347
    assert products["items"][0]["amount"] == 2
    assert products["items"][0]["combine_id"] == 837481216
    assert products["items"][0]["adjusts"] == [
        {"adjust_id": 1950362842, "options": [{"option_id": 1950362926, "amount": 1}]},
        {"adjust_id": 1950362845, "options": [{"option_id": 1950362968, "amount": 1}]},
    ]


def test_build_group_buy_cart_payload_from_multiple_items_with_toppings():
    menu = normalize_menu_from_api(RAW_GROUP_MENU, "1717")
    parsed_orders = parse_order_lines(
        "+1 中●阿薩姆紅茶 正常冰 正常甜 珍珠\n+2 中●奶茶 正常冰 半糖",
        menu,
    )

    payload = build_group_buy_cart_payload(
        group_buy_code="TESTCODE01",
        group_buy_brief=GROUP_BUY_BRIEF["group_buy_brief"],
        menu_payload=RAW_GROUP_MENU,
        parsed_orders=parsed_orders,
    )

    assert payload["total_money"] == 145

    products = json.loads(payload["products"])
    assert len(products["items"]) == 2
    assert products["items"][0]["product_id"] == 1950362347
    assert products["items"][0]["amount"] == 1
    assert products["items"][0]["adjusts"] == [
        {"adjust_id": 1950362842, "options": [{"option_id": 1950362926, "amount": 1}]},
        {"adjust_id": 1950362845, "options": [{"option_id": 1950362968, "amount": 1}]},
        {"adjust_id": 1950362863, "options": [{"option_id": 1950363133, "amount": 1}]},
    ]
    assert products["items"][1]["product_id"] == 1950362390
    assert products["items"][1]["amount"] == 2
    assert products["items"][1]["combine_id"] == 837481217
    assert products["items"][1]["adjusts"] == [
        {"adjust_id": 1950362842, "options": [{"option_id": 1950362926, "amount": 1}]},
        {"adjust_id": 1950362845, "options": [{"option_id": 1950362950, "amount": 1}]},
    ]


def test_parse_order_text_supports_item_aliases_derived_from_live_menu_names():
    menu = normalize_menu_from_api(RAW_GROUP_MENU, "1717")

    parsed = parse_order_text("+1 阿薩姆紅茶 正常冰 正常甜 珍珠", menu)

    assert parsed.item.name == "中●阿薩姆紅茶"


def test_parse_order_text_rejects_ambiguous_item_keyword():
    menu = normalize_menu_from_api(RAW_GROUP_MENU, "1717")

    with pytest.raises(ValueError, match="Ambiguous item"):
        parse_order_text("+1 奶茶 正常冰 半糖", menu)


def test_normalize_menu_from_api_includes_item_categories():
    menu = normalize_menu_from_api(RAW_GROUP_MENU, "1717")

    assert [category.name for category in menu.categories] == ["找好茶", "找奶茶"]
    assert menu.items[0].category_name == "找好茶"
    assert menu.items[1].category_name == "找奶茶"
    assert menu.items[2].category_name == "找奶茶"


def test_normalize_menu_from_api_includes_product_images():
    menu = normalize_menu_from_api(
        RAW_GROUP_MENU,
        "1717",
        product_image_urls={
            "1950362347": "https://cdn-order-v3.nidin.shop/product/images/red-tea.png",
        },
    )

    assert menu.items[0].image_url == "https://cdn-order-v3.nidin.shop/product/images/red-tea.png"
    assert menu.items[1].image_url is None


def test_extract_product_image_urls_prefers_large_sub_image():
    payload = {
        "product_list": {
            "123": {
                "main_image": {
                    "url": "https://cdn-order-v3.nidin.shop/product/images/main-small.png",
                },
                "sub_image_list": [
                    {
                        "url": "https://cdn-order-v3.nidin.shop/product/images/sub-large.png",
                    }
                ],
            }
        }
    }

    image_urls = _extract_product_image_urls(payload)

    assert image_urls["123"] == "https://cdn-order-v3.nidin.shop/product/images/sub-large.png"


def test_normalize_store_delivery_summary_tolerates_null_strings():
    summary = _normalize_store_delivery_summary(
        {
            "name": "南港昆陽店",
            "order_status_reason": None,
            "delivery_way_description": None,
            "delivery_info": [{"order_min_price": 300}],
        },
        "14224",
    )

    assert summary.store_id == "14224"
    assert summary.store_name == "南港昆陽店"
    assert summary.delivery_minimum == 300
    assert summary.delivery_description == ""
    assert summary.order_status_reason == ""


def test_normalize_menu_from_api_uses_combine_price_when_product_price_is_zero():
    payload = {
        "product_list_json": {
            "schema": {
                "product_id": 0,
                "name": 1,
                "price": 2,
                "type": 3,
                "is_enable": 4,
                "is_visible": 5,
                "adjust_list": 6,
                "combine_list": 7,
            },
            "data": {
                "drink_1": [1001, "極品紅茶", 0, 1, True, True, [], [9001]],
            },
        },
        "adjust_list_json": {
            "schema": {
                "product_id": 0,
                "name": 1,
                "min_limit": 2,
                "max_limit": 3,
            },
            "data": {},
        },
        "option_list_json": {
            "schema": {
                "product_id": 0,
                "name": 1,
                "price": 2,
                "adjust_id": 3,
            },
            "data": {},
        },
        "combine_list_json": {
            "schema": {
                "combine_id": 0,
                "name": 1,
                "price": 2,
                "price_type": 3,
                "is_common": 4,
                "edge_list": 5,
                "common_list": 6,
            },
            "data": {
                "9001": [
                    9001,
                    "北A",
                    None,
                    3,
                    False,
                    {"1001": [1001, 1, 40, 3, 1]},
                    [],
                ]
            },
        },
    }

    menu = normalize_menu_from_api(payload, "14224")

    assert menu.items[0].name == "極品紅茶"
    assert menu.items[0].price == 40


@pytest.mark.anyio
async def test_order_guest_once_calls_nidin_sequence():
    seen: list[tuple[str, dict | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = json.loads(request.content.decode()) if request.content else None

        if path.endswith("/groupBuy/TESTCODE01/brief"):
            seen.append(("brief", None))
            return httpx.Response(200, json=GROUP_BUY_BRIEF)

        if path.endswith("/store/1717/onShelfMenu"):
            seen.append(("menu", None))
            return httpx.Response(200, json=RAW_GROUP_MENU)

        if path.endswith("/user/registerGuest"):
            seen.append(("register", payload))
            return httpx.Response(
                200,
                json={
                    "token": "register-token",
                    "user_id": 9000001,
                    "guest_id": 5000001,
                    "account": "guest-account",
                    "password": "guest-password",
                    "nickname": "Codex測試",
                },
            )

        if path.endswith("/user/loginWebGuest"):
            seen.append(("login", payload))
            return httpx.Response(
                200,
                json={
                    "token": "login-token",
                    "user_id": 9000001,
                    "guest_id": 5000001,
                    "nickname": "Codex測試",
                },
            )

        if path.endswith("/carts/subItem"):
            seen.append(("cart", payload))
            assert request.headers["mc-api-token"] == "login-token"
            assert request.headers["mc-api-user"] == "9000001"
            return httpx.Response(
                200,
                json={
                    "order_id": 300000001,
                    "order_sn": "M0300000001",
                    "sharing_code": "TESTCODE01",
                },
            )

        if path.endswith("/order/productCouponFlow"):
            seen.append(("submit", payload))
            assert request.headers["mc-api-token"] == "login-token"
            assert request.headers["mc-api-user"] == "9000001"
            return httpx.Response(
                200,
                json={
                    "status": 200,
                    "message": "Order success!",
                    "order_id": 300000001,
                    "order_status": 31,
                },
            )

        raise AssertionError(f"Unexpected request path: {path}")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://loctw-service-api.nidin.shop/shopper/v2") as client:
        nidin_client = NidinClient(client=client, base_url="https://loctw-service-api.nidin.shop/shopper/v2")
        receipt = await nidin_client.order_guest_once(
            group_buy_url="https://order.nidin.shop/gb/menu/1717/TESTCODE01",
            guest_name="Codex測試",
            guest_phone="0987654321",
            order_text="+1 中●阿薩姆紅茶 正常冰 正常甜 珍珠\n+2 中●奶茶 正常冰 半糖",
        )

    assert [step for step, _ in seen] == ["menu", "brief", "register", "login", "cart", "submit"]
    assert receipt.order_id == 300000001
    assert receipt.order_status == 31
    assert receipt.store_id == "1717"
    assert receipt.group_buy_code == "TESTCODE01"
    assert receipt.total_money == 145


@pytest.mark.anyio
@pytest.mark.parametrize(("given", "expected"), [("", None), ("+886 912-345-678", "0912345678")])
async def test_register_guest_sends_a_phone_only_when_the_orderer_gave_one(given, expected):
    # 以前沒填就亂數產生一支 09 號碼，團長會在 Nidin 上看到陌生人的電話；官網沒填時也不帶
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "token": "register-token",
                "user_id": 9000001,
                "guest_id": 5000001,
                "account": "guest-account",
                "password": "guest-password",
                "nickname": "Codex測試",
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://loctw-service-api.nidin.shop/shopper/v2") as client:
        nidin_client = NidinClient(client=client, base_url="https://loctw-service-api.nidin.shop/shopper/v2")
        registration = await nidin_client.register_guest(
            guest_name="Codex測試",
            guest_phone=given,
            device_id="NIDINShopperWeb_test123456",
        )

    assert registration["account"] == "guest-account"
    assert seen.get("phone") == expected
