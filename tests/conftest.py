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
