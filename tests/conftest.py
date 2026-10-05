import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 必須在任何 app 模組被 import 之前設定：CartStore 的預設路徑 app/data/cart.db 在主機上
# 就是正式容器 bind mount 的同一個檔案。2026-09-24 有個 fixture 在隔離生效前寫入，
# 把正式環境的主揪憑證蓋成測試假資料、也刪掉了記住的密碼。
os.environ["CART_DB_PATH"] = str(Path(tempfile.mkdtemp(prefix="nidin-tests-")) / "cart.db")


import pytest


@pytest.fixture
def authenticated_manager(monkeypatch):
    """Existing business tests use a manager; auth boundaries have separate tests."""
    from app import auth
    from app.dependencies import get_cart_store
    from app.main import app

    monkeypatch.setenv("NIDIN_ADMIN_USERNAME", "test-manager")
    monkeypatch.setenv("NIDIN_ADMIN_PASSWORD", "test-manager-password")
    auth.configure_auth(lambda: get_cart_store()._db_path)

    def manager():
        return auth.AuthUser(
            id=auth.first_admin_id(), username="test-manager", is_admin=True,
        )

    app.dependency_overrides[auth.require_user] = manager
    app.dependency_overrides[auth.current_user] = manager
    yield
    app.dependency_overrides.pop(auth.require_user, None)
    app.dependency_overrides.pop(auth.current_user, None)


@pytest.fixture
def isolated_store(monkeypatch, tmp_path):
    """乾淨的 CartStore，並清掉 Ocard 菜單快取、刷新節流與送單限流的行程內狀態。"""
    from app.cart_store import CartStore
    from app.ocard import clear_menu_cache
    from app.routers import live_orders

    monkeypatch.setattr("app.dependencies.cart_store", CartStore(tmp_path / "cart.db"))
    live_orders._last_refresh.clear()
    live_orders._submits_by_ip.clear()
    clear_menu_cache()


@pytest.fixture
def ocard_session_id(authenticated_manager, isolated_store):
    """一個開在 Ocard 的團（測試資料見 tests/fixtures_ocard.py）。"""
    from app import auth
    from app.dependencies import cart_store
    from tests.fixtures_ocard import OCARD_MENU, OCARD_TARGET

    return cart_store.create_or_get_session(
        group_buy_url=OCARD_TARGET.url,
        store_id=OCARD_TARGET.session_store_id,
        group_buy_code=OCARD_TARGET.group_code,
        session_name="測試",
        menu=OCARD_MENU.menu,
        delivery_summary=None,
        owner_id=auth.first_admin_id(),
        provider="ocard",
    ).session_id
