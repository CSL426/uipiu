"""跨 router 共用的相依與輔助函式。

從 main.py 抽出來讓各 router 匯入，避免 router 反向 import main 造成循環匯入。
"""

from typing import Any

from fastapi import Depends, HTTPException
import httpx

from app import auth as account_auth
from app import dependencies
from app.auth import AuthUser, require_user
from app.models import (
    NormalizedMenu,
    StoreDeliverySummary,
    validate_menu_snapshot,
)
from app.nidin import NidinClient


def initialize_legacy_ownership() -> None:
    admin_id = account_auth.first_admin_id()
    if admin_id is not None:
        dependencies.get_cart_store().claim_legacy_sessions(admin_id)
        account_auth.migrate_legacy_credentials(admin_id)


def require_session_owner(
    session_id: str,
    user: AuthUser = Depends(require_user),
) -> AuthUser:
    initialize_legacy_ownership()
    try:
        owner = dependencies.get_cart_store().session_owner(session_id)
    except KeyError as exc:
        raise HTTPException(404, "找不到這個團購。") from exc
    if owner != user.id:
        raise HTTPException(403, "只有此團購的主揪可以執行這個操作。")
    return user


def build_default_session_name(
    *,
    explicit_name: str,
    group_buy_brief: dict[str, Any],
    menu: NormalizedMenu,
    delivery_summary: StoreDeliverySummary | None,
) -> str:
    if explicit_name.strip():
        return explicit_name.strip()

    brand_name = str(group_buy_brief.get("brand_name") or "").strip()
    store_name = (
        str(group_buy_brief.get("store_name") or "").strip()
        or menu.store_name.strip()
        or (delivery_summary.store_name.strip() if delivery_summary else "")
    )
    if brand_name and store_name:
        return f"{brand_name} {store_name}"
    return brand_name or store_name or "未命名團購"


async def fetch_current_menu(client: NidinClient, store_id: str) -> NormalizedMenu:
    try:
        menu = await client.fetch_menu(store_id)
        validate_menu_snapshot(menu)
        return menu
    except (httpx.NetworkError, httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
        raise HTTPException(503, "Nidin 暫時無法連線，請稍後重試。") from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        code = 503 if status >= 500 else 502
        raise HTTPException(code, f"Nidin 菜單請求失敗（HTTP {status}）。") from exc
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        raise HTTPException(502, "Nidin 菜單資料格式錯誤，請稍後重試。") from exc
