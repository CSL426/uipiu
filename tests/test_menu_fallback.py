from copy import deepcopy
from datetime import datetime
from unittest.mock import AsyncMock

import httpx
import pytest

from app.cart_store import CartStore
from app.main import app
from app.models import ExpectedOrderLine
from app.nidin import (
    MenuChangedError,
    NidinClient,
    normalize_menu,
    normalize_menu_from_api,
)
from tests.fixtures import RAW_GROUP_MENU, RAW_MENU


pytestmark = pytest.mark.usefixtures("authenticated_manager")

@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def setup(monkeypatch, tmp_path):
    store = CartStore(tmp_path / "cart.db")
    menu = normalize_menu(RAW_MENU)
    session = store.create_or_get_session(
        group_buy_url="https://order.nidin.shop/gb/menu/1717/example",
        store_id="1717", group_buy_code="example", session_name="test",
        menu=menu, delivery_summary=None,
    )
    client = NidinClient()
    client.fetch_menu = AsyncMock(return_value=menu)
    client.order_guest_cart_session = AsyncMock()
    monkeypatch.setattr("app.dependencies.cart_store", store)
    monkeypatch.setattr("app.dependencies.get_nidin_client", lambda: client)
    return store, session.session_id, client, menu


def upstream_error(status):
    response = httpx.Response(status, request=httpx.Request("GET", "https://upstream/menu"))
    return httpx.HTTPStatusError("upstream", request=response.request, response=response)


async def menu_request(session_id):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        return await client.get(f"/cart-sessions/{session_id}/menu")


async def submit_request(session_id):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        return await client.post(
            f"/cart-sessions/{session_id}/submit-guest-order",
            json={"guest_name": "test"},
        )


def add_draft(store, session_id):
    return store.add_lines(session_id, contributor_name="test", lines=[{
        "item_id": "drink_1", "quantity": 2,
        "selection_option_ids": ["sweet_50", "ice_0"],
    }])


@pytest.mark.anyio
async def test_live_refresh_has_independent_persisted_timestamp(setup, monkeypatch):
    store, session_id, client, menu = setup
    monkeypatch.setattr("app.cart_store._now_iso", lambda: "2026-09-17T01:00:00+00:00")
    menu.items[0].price = 65
    response = await menu_request(session_id)
    assert response.status_code == 200
    assert response.json()["menu_source"] == "live"
    assert datetime.fromisoformat(response.json()["menu_fetched_at"]).tzinfo
    assert response.json()["items"][0]["price"] == 65
    monkeypatch.setattr("app.cart_store._now_iso", lambda: "2026-09-17T02:00:00+00:00")
    add_draft(store, session_id)
    reopened = CartStore(store._db_path)
    assert reopened.get_menu(session_id).menu_fetched_at == response.json()["menu_fetched_at"]
    assert reopened.get_session(session_id).updated_at != response.json()["menu_fetched_at"]
    monkeypatch.setattr("app.dependencies.cart_store", reopened)
    client.fetch_menu.side_effect = httpx.ConnectError("offline")
    cached = await menu_request(session_id)
    assert cached.status_code == 200
    assert cached.json()["menu_source"] == "cache"
    assert cached.json()["menu_fetched_at"] == response.json()["menu_fetched_at"]
    assert cached.json()["items"] == response.json()["items"]


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [
    httpx.ConnectError("offline"), httpx.ReadTimeout("timeout"),
    httpx.RemoteProtocolError("upstream disconnected"),
    upstream_error(500), upstream_error(503),
])
async def test_temporary_failure_uses_snapshot(setup, failure):
    store, session_id, client, _ = setup
    before = store.get_menu(session_id)
    client.fetch_menu.side_effect = failure
    response = await menu_request(session_id)
    assert response.status_code == 200
    assert response.json() == before.model_dump()


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [
    upstream_error(401), upstream_error(403), upstream_error(404),
    upstream_error(410), upstream_error(429), ValueError("invalid"),
    KeyError("missing schema"),
])
async def test_non_temporary_error_does_not_hide_behind_snapshot(setup, failure):
    store, session_id, client, _ = setup
    before = store.get_menu(session_id).model_dump()
    client.fetch_menu.side_effect = failure
    response = await menu_request(session_id)
    assert response.status_code == 502
    assert store.get_menu(session_id).model_dump() == before


@pytest.mark.anyio
@pytest.mark.parametrize("payload", ["{}", "not-json"])
async def test_no_usable_snapshot_is_explicit(setup, payload):
    store, session_id, client, _ = setup
    with store._connect() as conn:
        conn.execute("UPDATE sessions SET menu_json=? WHERE session_id=?", (payload, session_id))
    client.fetch_menu.side_effect = httpx.ConnectError("offline")
    response = await menu_request(session_id)
    assert response.status_code == 503
    assert "沒有可用" in response.json()["detail"]


@pytest.mark.anyio
@pytest.mark.parametrize("invalid", ["empty", "missing_group", "duplicate_item"])
async def test_incomplete_success_never_replaces_snapshot(setup, invalid):
    store, session_id, client, menu = setup
    before = store.get_menu(session_id).model_dump()
    if invalid == "empty":
        menu.items = []
    elif invalid == "missing_group":
        menu.groups = []
    else:
        menu.items.append(menu.items[0])
    response = await menu_request(session_id)
    assert response.status_code == 502
    assert store.get_menu(session_id).model_dump() == before


def test_legacy_migration_preserves_unknown_fetch_time(setup):
    store, session_id, _, _ = setup
    with store._connect() as conn:
        conn.execute("ALTER TABLE sessions DROP COLUMN menu_fetched_at")
    reopened = CartStore(store._db_path)
    assert reopened.get_menu(session_id).menu_fetched_at is None
    assert reopened.get_menu(session_id).menu_source == "cache"
    assert CartStore(store._db_path).get_menu(session_id).items


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["price", "option_price", "removed", "required", "disabled_option"])
async def test_submit_changed_menu_keeps_draft_and_never_orders(setup, change):
    store, session_id, client, menu = setup
    before = add_draft(store, session_id)
    if change == "price":
        menu.items[0].price += 5
    elif change == "option_price":
        menu.groups[0].options[0].price += 5
    elif change == "removed":
        menu.items.pop(0)
    elif change == "required":
        menu.groups[2].min_select = 1
    else:
        menu.groups[0].options.pop(0)
    response = await submit_request(session_id)
    assert response.status_code == 409
    client.order_guest_cart_session.assert_not_awaited()
    assert store.get_session(session_id).lines == before.lines


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [httpx.ReadTimeout("offline"), upstream_error(503)])
async def test_submit_refresh_failure_does_not_use_cached_menu(setup, failure):
    store, session_id, client, _ = setup
    before = add_draft(store, session_id)
    client.fetch_menu.side_effect = failure
    response = await submit_request(session_id)
    assert response.status_code == 503
    client.order_guest_cart_session.assert_not_awaited()
    assert store.get_session(session_id).lines == before.lines


@pytest.mark.anyio
async def test_failed_official_submit_does_not_clear_cart(setup):
    store, session_id, client, _ = setup
    before = add_draft(store, session_id)
    client.order_guest_cart_session.side_effect = httpx.ReadTimeout("uncertain")
    response = await submit_request(session_id)
    assert response.status_code == 503
    assert store.get_session(session_id).lines == before.lines


def expected_group_lines():
    return [ExpectedOrderLine(
        item_id="1950362347", quantity=2,
        selection_option_ids=["1950362926", "1950362968"], line_total=70,
    ), ExpectedOrderLine(
        item_id="1950362390", quantity=1,
        selection_option_ids=["1950362926", "1950362968"], line_total=50,
    )]


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["price", "removed", "option", "required"])
async def test_structured_guest_order_blocks_changes_before_any_write(change):
    raw = deepcopy(RAW_GROUP_MENU)
    menu = raw["menu"]
    product = menu["product_list_json"]
    options = menu["option_list_json"]
    if change == "price":
        product["data"]["1950362347"][product["schema"]["price"]] = 40
    elif change == "removed":
        product["data"]["1950362347"][product["schema"]["is_enable"]] = False
    elif change == "option":
        del options["data"]["1950362926"]
    else:
        adjusts = menu["adjust_list_json"]
        adjusts["data"]["1950362863"][adjusts["schema"]["min_limit"]] = 1
    client = NidinClient()
    client.fetch_raw_menu = AsyncMock(return_value=raw)
    client.order_guest_parsed_orders = AsyncMock()
    with pytest.raises(MenuChangedError):
        await client.order_guest_once(
            group_buy_url="https://order.nidin.shop/gb/menu/1717/example",
            guest_name="test", guest_phone="", order_text="ignored",
            expected_lines=expected_group_lines(),
        )
    client.order_guest_parsed_orders.assert_not_awaited()


@pytest.mark.anyio
async def test_structured_guest_multi_line_uses_validated_raw_menu():
    client = NidinClient()
    client.fetch_raw_menu = AsyncMock(return_value=RAW_GROUP_MENU)
    client.order_guest_parsed_orders = AsyncMock(return_value="receipt")
    result = await client.order_guest_once(
        group_buy_url="https://order.nidin.shop/gb/menu/1717/example",
        guest_name="test", guest_phone="", order_text="do not parse this text",
        expected_lines=expected_group_lines(),
    )
    assert result == "receipt"
    kwargs = client.order_guest_parsed_orders.call_args.kwargs
    assert kwargs["raw_menu"] is RAW_GROUP_MENU
    assert len(kwargs["parsed_orders"]) == 2
    assert [order.quantity for order in kwargs["parsed_orders"]] == [2, 1]
    assert [order.item.price for order in kwargs["parsed_orders"]] == [35, 50]
    client.fetch_raw_menu.assert_awaited_once()


@pytest.mark.anyio
async def test_guest_endpoint_passes_structured_expectations_and_reports_conflict(setup):
    _, _, client, _ = setup
    client.order_guest_once = AsyncMock(side_effect=MenuChangedError("菜單已變更"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as api:
        response = await api.post("/guest/order-once", json={
            "group_buy_url": "https://order.nidin.shop/gb/menu/1717/example",
            "guest_name": "test", "order_text": "ignored",
            "expected_lines": [line.model_dump() for line in expected_group_lines()],
        })
    assert response.status_code == 409
    assert client.order_guest_once.call_args.kwargs["expected_lines"] == expected_group_lines()


def test_truncated_raw_schema_is_rejected():
    raw = deepcopy(RAW_GROUP_MENU)
    raw["menu"]["product_list_json"]["data"]["1950362347"] = [1950362347]
    with pytest.raises(ValueError, match="不完整"):
        normalize_menu_from_api(raw, "1717")


@pytest.mark.anyio
async def test_guest_invalid_upstream_shape_is_explicit_and_never_orders(setup):
    _, _, fake, _ = setup
    real = NidinClient()
    real.fetch_raw_menu = AsyncMock(return_value={"menu": {}})
    real.order_guest_parsed_orders = AsyncMock()
    fake.order_guest_once = real.order_guest_once
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as api:
        response = await api.post("/guest/order-once", json={
            "group_buy_url": "https://order.nidin.shop/gb/menu/1717/example",
            "guest_name": "test", "order_text": "ignored",
            "expected_lines": [line.model_dump() for line in expected_group_lines()],
        })
    assert response.status_code == 502
    real.order_guest_parsed_orders.assert_not_awaited()


def test_clear_submitted_lines_preserves_new_lines_and_their_payments(setup):
    store, session_id, _, _ = setup
    submitted = add_draft(store, session_id).lines
    store.add_lines(session_id, contributor_name="finished", lines=[{
        "item_id": "drink_1", "quantity": 1,
        "selection_option_ids": ["sweet_50", "ice_0"],
    }])
    submitted = store.get_session(session_id).lines
    store.set_payment(session_id, "finished", True)
    added = add_draft(store, session_id).lines[-1]
    store.set_payment(session_id, "test", True)

    remaining = store.clear_submitted_lines(
        session_id, [line.line_id for line in submitted],
    )

    assert remaining.lines == [added]
    assert store.get_payments(session_id) == {"test": True}


@pytest.mark.anyio
@pytest.mark.parametrize("changed", ["item", "option"])
async def test_structured_guest_blocks_renamed_ids_with_same_price(changed):
    raw = deepcopy(RAW_GROUP_MENU)
    menu = normalize_menu_from_api(raw, "1717")
    items = {item.id: item for item in menu.items}
    options = {
        option.id: option for group in menu.groups for option in group.options
    }
    expected = expected_group_lines()
    for line in expected:
        line.item_name = items[line.item_id].name
        line.selection_option_names = [
            options[option_id].name for option_id in line.selection_option_ids
        ]
    table = raw["menu"][
        "product_list_json" if changed == "item" else "option_list_json"
    ]
    changed_id = (
        expected[0].item_id if changed == "item"
        else expected[0].selection_option_ids[0]
    )
    table["data"][changed_id][table["schema"]["name"]] = "另一種餐點或規格"
    client = NidinClient()
    client.fetch_raw_menu = AsyncMock(return_value=raw)
    client.order_guest_parsed_orders = AsyncMock()

    with pytest.raises(MenuChangedError):
        await client.order_guest_once(
            group_buy_url="https://order.nidin.shop/gb/menu/1717/example",
            guest_name="test", guest_phone="", order_text="ignored",
            expected_lines=expected,
        )

    client.order_guest_parsed_orders.assert_not_awaited()
