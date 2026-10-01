from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.cart_store import CartStore
from app.main import app
from app.nidin import NidinClient, normalize_menu
from tests.fixtures import RAW_MENU


@pytest.fixture
def setup(monkeypatch, tmp_path):
    store = CartStore(tmp_path / "cart.db")
    monkeypatch.setattr("app.dependencies.cart_store", store)
    monkeypatch.setenv("NIDIN_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("NIDIN_ADMIN_PASSWORD", "admin-test-password")
    auth.configure_auth(lambda: store._db_path)
    admin = TestClient(app)
    assert admin.post("/auth/login", json={"username": "admin", "password": "admin-test-password"}).status_code == 200
    res = admin.post("/auth/accounts", json={"username": "host2", "password": "host2-test-password"})
    assert res.status_code in (200, 201)
    other = TestClient(app)
    user = other.post("/auth/login", json={"username": "host2", "password": "host2-test-password"}).json()
    menu = normalize_menu(RAW_MENU)
    legacy = store.create_or_get_session(
        group_buy_url="https://order.nidin.shop/gb/menu/1717/legacy",
        store_id="1717", group_buy_code="legacy", session_name="legacy",
        menu=menu, delivery_summary=None,
    )
    second = store.create_or_get_session(
        group_buy_url="https://order.nidin.shop/gb/menu/1717/second",
        store_id="1717", group_buy_code="second", session_name="second",
        menu=menu, delivery_summary=None, owner_id=user["id"],
    )
    yield store, admin, other, legacy.session_id, second.session_id
    admin.close()
    other.close()
    from app.dependencies import get_cart_store
    auth.configure_auth(lambda: get_cart_store()._db_path)


def test_legacy_owner_and_private_management_list(setup):
    store, admin, other, legacy, second = setup
    assert [s["session_id"] for s in admin.get("/admin/cart-sessions").json()] == [legacy]
    assert [s["session_id"] for s in other.get("/admin/cart-sessions").json()] == [second]
    assert store.session_owner(legacy) == auth.first_admin_id()
    with TestClient(app) as guest:
        assert guest.get("/cart-sessions").status_code == 200
        assert guest.get("/admin/cart-sessions").status_code == 401
        assert guest.post("/cart-sessions", json={"group_buy_url": "unused"}).status_code == 401


@pytest.mark.parametrize("method,suffix", [
    ("delete", "lines"), ("post", "sync-official"),
    ("post", "submit-guest-order"), ("get", "official-orders"),
    ("get", "payments"), ("put", "payments"),
])
def test_non_owner_and_anonymous_management_denied(setup, method, suffix):
    _, admin, other, legacy, second = setup
    payload = {"guest_name": "test", "contributor_name": "guest", "paid": True}
    kwargs = {} if method == "get" else {"json": payload}
    assert other.request(method, f"/cart-sessions/{legacy}/{suffix}", **kwargs).status_code == 403
    assert admin.request(method, f"/cart-sessions/{second}/{suffix}", **kwargs).status_code == 403
    with TestClient(app) as guest:
        assert guest.request(method, f"/cart-sessions/{legacy}/{suffix}", **kwargs).status_code == 401


def test_cannot_take_over_group_via_create(setup, monkeypatch):
    _, _, other, _, _ = setup
    upstream = NidinClient()
    upstream.fetch_menu = AsyncMock(return_value=normalize_menu(RAW_MENU))
    upstream.fetch_group_buy_brief = AsyncMock(return_value={})
    upstream.fetch_store_delivery_summary = AsyncMock(return_value=None)
    monkeypatch.setattr("app.dependencies.get_nidin_client", lambda: upstream)
    response = other.post("/cart-sessions", json={
        "group_buy_url": "https://order.nidin.shop/gb/menu/1717/legacy",
    })
    assert response.status_code == 403


def test_credentials_are_private_and_logout_revokes(setup):
    _, admin, other, _, _ = setup
    assert admin.post("/leader/auth", json={"token": "admin-nidin-test-value", "user_id": "1"}).status_code == 200
    assert other.get("/leader/auth").json()["is_authenticated"] is False
    assert other.post("/leader/auth", json={"token": "other-nidin-test-value", "user_id": "2"}).status_code == 200
    assert admin.get("/leader/auth").json()["user_id"] == "1"
    assert other.delete("/leader/auth").status_code == 200
    assert admin.get("/leader/auth").json()["is_authenticated"] is True
    assert admin.post("/auth/logout").status_code == 200
    assert admin.get("/leader/auth").status_code == 401


def test_payments_persist_and_isolate_groups_and_new_orders(setup):
    store, admin, other, legacy, second = setup
    line = {"item_id": "drink_1", "quantity": 1, "selection_option_ids": ["sweet_50", "ice_0"]}
    for sid in (legacy, second):
        store.add_lines(sid, contributor_name="同名者", lines=[line])
    result = admin.put(f"/cart-sessions/{legacy}/payments", json={"contributor_name": "同名者", "paid": True})
    assert result.status_code == 200
    assert result.json() == {"同名者": True}
    assert other.get(f"/cart-sessions/{second}/payments").json() == {}
    assert CartStore(store._db_path).get_payments(legacy) == {"同名者": True}
    store.add_lines(legacy, contributor_name="同名者", lines=[line])
    # 又點了一杯：應付變多，先前的收款不再涵蓋，判定為未收清
    assert admin.get(f"/cart-sessions/{legacy}/payments").json().get("同名者", False) is False


def test_cross_origin_admin_mutation_denied(setup):
    _, admin, _, legacy, _ = setup
    response = admin.delete(f"/cart-sessions/{legacy}/lines", headers={"Origin": "https://evil.invalid"})
    assert response.status_code == 403


def test_someone_else_ordering_and_cancelling_under_your_name_does_not_clear_your_payment(setup):
    store, admin, _, legacy, _ = setup
    line = {"item_id": "drink_1", "quantity": 1, "selection_option_ids": ["sweet_50", "ice_0"]}
    store.add_lines(legacy, contributor_name="同名者", lines=[line])
    admin.put(f"/cart-sessions/{legacy}/payments", json={"contributor_name": "同名者", "paid": True})

    built = store.build_order_lines(legacy, contributor_name="同名者", lines=[line])
    store.record_order_lines(legacy, built, official_order_id="999")
    assert admin.get(f"/cart-sessions/{legacy}/payments").json()["同名者"] is False
    store.remove_official_order(legacy, "999")

    assert admin.get(f"/cart-sessions/{legacy}/payments").json()["同名者"] is True
