"""閒置的團自動從本站移除：什麼算活動、什麼不算、舊資料的緩衝、以及排程確實有跑。"""

import asyncio
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from app import session_cleanup
from app.cart_store import CartStore
from app.main import app
from app.models import (
    OfficialFollowerItem,
    OfficialFollowerOrder,
    OfficialGroupBuySummary,
    validate_menu_snapshot,
)
from app.nidin import normalize_menu_from_api
from tests.fixtures import RAW_GROUP_MENU

MENU = normalize_menu_from_api(RAW_GROUP_MENU, "1717")
LINE = {"item_id": "1950362419", "quantity": 1, "selection_option_ids": ["1950362926", "1950362950"]}


@pytest.fixture
def store(tmp_path):
    validate_menu_snapshot(MENU)
    return CartStore(tmp_path / "cart.db")


def _session(store, code="ABCDEFGHIJ"):
    return store.create_or_get_session(
        group_buy_url=f"https://order.nidin.shop/gb/menu/1717/{code}", store_id="1717", group_buy_code=code,
        session_name=f"團{code}", menu=MENU, delivery_summary=None,
    ).session_id


def _age(store, session_id, days):
    with sqlite3.connect(store._db_path) as conn:
        stamp = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        conn.execute("UPDATE sessions SET last_activity_at=? WHERE session_id=?", (stamp, session_id))


def _last_activity(store, session_id):
    with sqlite3.connect(store._db_path) as conn:
        return conn.execute("SELECT last_activity_at FROM sessions WHERE session_id=?", (session_id,)).fetchone()[0]


def _official(*orders):
    return OfficialGroupBuySummary(group_buy_code="ABCDEFGHIJ", follower_orders=[
        OfficialFollowerOrder(order_id=order_id, follower_name="小明", items=[
            OfficialFollowerItem(product_id="1950362419", name="奶茶", amount=1, subtotal=total),
        ]) for order_id, total in orders
    ])


def test_groups_idle_past_the_limit_are_removed_and_others_kept(store):
    idle, active = _session(store, "AAAAAAAAAA"), _session(store, "BBBBBBBBBB")
    store.add_lines(idle, contributor_name="小明", lines=[LINE])
    store.set_payment(idle, "小明", True)
    _age(store, idle, 8)
    _age(store, active, 6)

    assert store.purge_idle_sessions() == ["團AAAAAAAAAA"]

    assert [s.session_id for s in store.list_sessions()] == [active]
    with sqlite3.connect(store._db_path) as conn:
        for table in ("lines", "payments", "order_submissions"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE session_id=?", (idle,)).fetchone()[0] == 0


def test_viewing_the_menu_or_syncing_the_same_content_is_not_activity(store):
    session_id = _session(store)
    store.merge_official_orders_to_lines(session_id, _official(("1", 60)))
    _age(store, session_id, 8)
    stale = _last_activity(store, session_id)

    store.replace_menu(session_id, MENU)
    store.merge_official_orders_to_lines(session_id, _official(("1", 60)))

    assert _last_activity(store, session_id) == stale
    assert store.purge_idle_sessions() == ["團ABCDEFGHIJ"]


@pytest.mark.parametrize("act", [
    lambda store, sid: store.add_lines(sid, contributor_name="小明", lines=[LINE]),
    lambda store, sid: store.merge_official_orders_to_lines(sid, _official(("1", 60), ("2", 50))),
    lambda store, sid: store.remove_official_order(sid, "1"),
    lambda store, sid: store.set_payment(sid, "小明", True),
    lambda store, sid: store.clear_lines(sid),
])
def test_real_changes_count_as_activity(store, act):
    session_id = _session(store)
    store.merge_official_orders_to_lines(session_id, _official(("1", 60)))
    _age(store, session_id, 8)

    act(store, session_id)

    assert store.purge_idle_sessions() == []


def test_existing_rows_get_a_full_period_when_the_column_is_added(tmp_path):
    path = tmp_path / "cart.db"
    store = CartStore(path)
    session_id = _session(store)
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE sessions DROP COLUMN last_activity_at")
        conn.execute("UPDATE sessions SET updated_at='2020-01-01T00:00:00+00:00'")

    migrated = CartStore(path)

    assert migrated.purge_idle_sessions() == []
    assert migrated.get_session(session_id).auto_remove_at is not None


def test_limit_is_configurable_and_zero_disables(store, monkeypatch):
    session_id = _session(store)
    _age(store, session_id, 30)

    monkeypatch.setenv("CART_SESSION_IDLE_DAYS", "0")
    assert store.get_session(session_id).auto_remove_at is None
    assert store.purge_idle_sessions() == []

    monkeypatch.setenv("CART_SESSION_IDLE_DAYS", "60")
    assert store.purge_idle_sessions() == []

    monkeypatch.setenv("CART_SESSION_IDLE_DAYS", "not-a-number")
    assert store.purge_idle_sessions() == ["團ABCDEFGHIJ"]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_cleanup_actually_runs_while_the_app_is_up(store, monkeypatch):
    """沒接進 lifespan 的話，整個功能不會發生，單元測試卻都會過。"""
    monkeypatch.setattr("app.dependencies.cart_store", store)
    session_id = _session(store)
    _age(store, session_id, 8)

    async with app.router.lifespan_context(app):
        for _ in range(50):
            if not store.list_sessions():
                break
            await asyncio.sleep(0.05)

    assert store.list_sessions() == []
    assert session_cleanup.CHECK_INTERVAL_SECONDS <= 3600
