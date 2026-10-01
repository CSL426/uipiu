"""主揪在 Nidin 開的團：列出來、一鍵匯入本站並同步。"""

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
import httpx

from app import dependencies
from app.auth import AuthUser, require_user
from app.deps_shared import initialize_legacy_ownership
from app.models import NidinGroupBuy, NidinGroupBuyImport
from app.nidin.client import NidinApiError
from app.routers.cart_sessions import create_session_from_group_buy_url
from app.routers.leader_auth import call_with_nidin_relogin, fetch_official_info

router = APIRouter()

GROUP_HEAD_ORDER_TYPE = 2  # 1 一般單、2 團長的團購單、3 團員的跟團單（官網 orderTypeName）


def _title(raw: dict[str, Any]) -> str:
    return " ".join(part for part in (raw.get("brand_name"), raw.get("store_name")) if part) or "未命名店家"


def _to_group_buy(raw: dict[str, Any], *, submitted: bool) -> NidinGroupBuy:
    return NidinGroupBuy(
        order_id=str(raw.get("order_id")),
        store_id=str(raw.get("store_id") or ""),
        title=_title(raw),
        group_buying_name=str(raw.get("head_group_buying_name") or raw.get("group_buying_name") or ""),
        created_at=str(raw.get("order_time") or raw.get("create_time") or ""),
        # 直接顯示 Nidin 自己的狀態文字，不自己翻譯狀態碼
        status_text=str(raw.get("status_msg") or "") if submitted else "揪團中（尚未送單）",
        submitted=submitted,
        total_money=int(raw.get("total_money") or 0),
        follower_count=int(raw.get("sub_order_amount") or 0),
    )


def _nidin_error(exc: Exception) -> HTTPException:
    if isinstance(exc, NidinApiError):
        return HTTPException(status_code=502, detail=f"Nidin 回應異常：{exc}")
    return HTTPException(status_code=502, detail="連不上 Nidin，請稍後再試。")


@router.get("/nidin/group-buys", response_model=list[NidinGroupBuy])
async def list_my_nidin_group_buys(user: AuthUser = Depends(require_user)) -> list[NidinGroupBuy]:
    """還在揪的（購物車）排前面，再來是已送出的訂單；各取最近 20 筆，只留團長單。"""
    initialize_legacy_ownership()
    client = dependencies.get_nidin_client()

    async def fetch(token: str, user_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        carts, orders = await asyncio.gather(
            client.list_carts(token=token, user_id=user_id),
            client.list_order_history(token=token, user_id=user_id),
        )
        return carts, orders

    try:
        (carts, orders), _ = await call_with_nidin_relogin(user, fetch)
    except (NidinApiError, httpx.HTTPError) as exc:
        raise _nidin_error(exc) from exc

    group_buys = [
        _to_group_buy(raw, submitted=False) for raw in carts if raw.get("order_type") == GROUP_HEAD_ORDER_TYPE
    ] + [
        _to_group_buy(raw, submitted=True) for raw in orders if raw.get("order_type") == GROUP_HEAD_ORDER_TYPE
    ]
    imported = dependencies.get_cart_store().sessions_by_nidin_order(user.id)
    for group_buy in group_buys:
        group_buy.session_id = imported.get(group_buy.order_id)
    return group_buys


@router.post("/nidin/group-buys/{order_id}/import", response_model=NidinGroupBuyImport)
async def import_nidin_group_buy(
    order_id: str,
    user: AuthUser = Depends(require_user),
) -> NidinGroupBuyImport:
    """建立（或找回）本站團購、記下 Nidin 單號，接著同步一次跟團訂單。"""
    initialize_legacy_ownership()
    client = dependencies.get_nidin_client()
    try:
        share_code, _ = await call_with_nidin_relogin(
            user,
            lambda token, user_id: client.fetch_share_code(token=token, user_id=user_id, order_id=order_id),
        )
        brief = await client.fetch_group_buy_brief(share_code)
    except (NidinApiError, httpx.HTTPError) as exc:
        raise _nidin_error(exc) from exc

    store_id = str(brief.get("store_id") or "")
    if not store_id:
        raise HTTPException(status_code=502, detail="Nidin 沒有回傳這個團的店家，無法匯入。")
    group_buy_url = f"https://order.nidin.shop/gb/menu/{store_id}/{share_code}"
    try:
        # 名稱留空＝沿用店名，和後台貼連結開團一致
        summary = await create_session_from_group_buy_url(group_buy_url, "", user.id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    store = dependencies.get_cart_store()
    store.link_nidin_order(summary.session_id, order_id)
    try:
        official = await fetch_official_info(user, share_code)
    except HTTPException as exc:
        # 團已經建好了，同步失敗不該讓整個匯入看起來失敗；之後可以再按同步
        return NidinGroupBuyImport(session=store.get_session(summary.session_id), sync_error=str(exc.detail))
    return NidinGroupBuyImport(session=store.merge_official_orders_to_lines(summary.session_id, official))
