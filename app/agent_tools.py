"""外部 agent 能做的事。和傳輸方式無關：MCP（app/mcp_server.py）只是把這些函式包成工具。

送單、取消、同步走的都是公開頁同一條流程（routers/live_orders.py），所以防重送、取消憑證、
Nidin／Ocard 的行為完全一致。權限見 docs/specs/2026-10-05-agent-mcp.md。
"""

import secrets
from typing import Any

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from app import dependencies
from app.api_keys import ApiKeyPrincipal
from app.models import CartSessionDetail, NormalizedMenu
from app.nidin.client import NidinApiError
from app.ocard import OcardApiError
from app.platforms import platform_for_session
from app.routers import live_orders, nidin_group_buys
from app.routers.cart_sessions import (
    AddCartLineDraftRequest,
    create_session_from_group_buy_url,
)


class ToolError(Exception):
    """給 agent 看的錯誤訊息；MCP 會把它當成工具執行失敗回傳。"""


def _detail(exc: HTTPException) -> str:
    return str(exc.detail)


def _session(group_id: str) -> CartSessionDetail:
    try:
        return dependencies.get_cart_store().get_session(group_id)
    except KeyError as exc:
        raise ToolError("找不到這個團。先用 list_groups 看有哪些團。") from exc


def _require_leader_of(principal: ApiKeyPrincipal, group_id: str) -> None:
    if not principal.is_leader:
        raise ToolError("這把 key 只有點單權限，不能做主揪操作。")
    if dependencies.get_cart_store().session_owner(group_id) != principal.owner_id:
        raise ToolError("這個團不是這把 key 的主揪開的。")


def _platform_label(session: CartSessionDetail) -> str:
    return platform_for_session(session).label


def _spec_text(selections: list[Any]) -> str:
    return "、".join(selection.option_name for selection in selections)


async def list_groups(principal: ApiKeyPrincipal) -> dict[str, Any]:
    store = dependencies.get_cart_store()
    groups = []
    for summary in store.list_sessions():
        groups.append({
            "group_id": summary.session_id,
            "platform": "Ocard" if summary.provider == "ocard" else "Nidin",
            "name": summary.session_name,
            "store": summary.store_name,
            "item_count": summary.line_count,
            "total": summary.total_money,
            "delivery_minimum": summary.delivery_minimum,
            "delivery_note": summary.delivery_description or summary.order_status_reason,
            "link": summary.group_buy_url,
            "yours": principal.is_leader and store.session_owner(summary.session_id) == principal.owner_id,
        })
    return {"groups": groups}


async def _current_menu(session: CartSessionDetail) -> NormalizedMenu:
    try:
        return await platform_for_session(session).fetch_menu(session)
    except HTTPException as exc:
        if exc.status_code != 503:
            raise ToolError(_detail(exc)) from exc
        try:
            return dependencies.get_cart_store().get_menu(session.session_id)
        except (KeyError, ValueError) as cache_exc:
            raise ToolError(_detail(exc)) from cache_exc


async def get_menu(principal: ApiKeyPrincipal, group_id: str, search: str = "") -> dict[str, Any]:
    session = _session(group_id)
    menu = await _current_menu(session)
    keyword = search.strip()
    items = [
        item for item in menu.items
        if not keyword or keyword in item.name or any(keyword in alias for alias in item.aliases)
    ]
    used_groups = {group_id for item in items for group_id in item.group_ids}
    return {
        "platform": _platform_label(session),
        "how_to_order": (
            "place_order 的每一項給 item_id、quantity 與 option_ids。每個 spec_group 要選 min～max 個選項"
            "（min 為 0 表示可不選），option_ids 放所選選項的 id；加價會自動算進金額。"
        ),
        "items": [
            {
                "item_id": item.id,
                "name": item.name,
                "price": item.price,
                "category": item.category_name,
                "spec_group_ids": item.group_ids,
            }
            for item in items
        ],
        "spec_groups": [
            {
                "group_id": group.id,
                "name": group.name,
                "min": group.min_select,
                "max": group.max_select,
                "options": [{"option_id": o.id, "name": o.name, "price": o.price} for o in group.options],
            }
            for group in menu.groups
            if group.id in used_groups
        ],
    }


def _order_lines(detail: CartSessionDetail, order_id: str) -> list[dict[str, Any]]:
    return [
        {"item": line.item_name, "quantity": line.quantity, "specs": _spec_text(line.selections), "amount": line.line_total}
        for line in detail.lines
        if line.official_order_id == order_id
    ]


async def place_order(
    principal: ApiKeyPrincipal,
    group_id: str,
    name: str,
    items: list[dict[str, Any]],
    phone: str = "",
    idempotency_key: str = "",
) -> dict[str, Any]:
    session = _session(group_id)
    # 不同 key 用同一個 idempotency_key 不會撞在一起，也拿不到別把 key 的單
    suffix = idempotency_key.strip() or secrets.token_urlsafe(12)
    try:
        payload = live_orders.SubmitOrderRequest(
            submission_id=f"agent-{principal.key_id}-{suffix}"[:64],
            contributor_name=name,
            contributor_phone=phone,
            lines=[AddCartLineDraftRequest(
                item_id=str(item.get("item_id", "")),
                quantity=int(item.get("quantity", 1)),
                selection_option_ids=[str(o) for o in item.get("option_ids") or []],
            ) for item in items],
        )
    except (ValidationError, ValueError, TypeError) as exc:
        raise ToolError(f"點單內容不正確：{exc}") from exc
    try:
        result = await live_orders.place_order(
            group_id, payload, rate_bucket=f"key:{principal.key_id}", api_key_id=principal.key_id
        )
    except HTTPException as exc:
        raise ToolError(_detail(exc)) from exc
    lines = _order_lines(result.session, result.official_order_id)
    return {
        "platform": _platform_label(session),
        "order_id": result.official_order_id,
        "already_submitted": result.already_submitted,
        "name": name,
        "lines": lines,
        "total": sum(line["amount"] for line in lines),
    }


async def list_orders(principal: ApiKeyPrincipal, group_id: str) -> dict[str, Any]:
    _session(group_id)
    try:
        refreshed = await live_orders.refresh_session(group_id)
    except HTTPException as exc:
        raise ToolError(_detail(exc)) from exc
    detail = refreshed.session
    store = dependencies.get_cart_store()
    show_paid = principal.is_leader and store.session_owner(group_id) == principal.owner_id
    paid = store.get_payments(group_id) if show_paid else {}
    people: dict[str, dict[str, Any]] = {}
    for line in detail.lines:
        person = people.setdefault(line.contributor_name, {"name": line.contributor_name, "items": [], "total": 0})
        person["items"].append({
            "order_id": line.official_order_id,
            "item": line.item_name,
            "quantity": line.quantity,
            "specs": _spec_text(line.selections),
            "amount": line.line_total,
        })
        person["total"] += line.line_total
        if show_paid:
            person["paid"] = bool(paid.get(line.contributor_name))
    return {
        "platform": _platform_label(detail),
        "synced": refreshed.synced,
        "note": refreshed.message,
        "people": list(people.values()),
        "total": detail.total_money,
    }


async def cancel_order(principal: ApiKeyPrincipal, group_id: str, order_id: str) -> dict[str, Any]:
    _session(group_id)

    def allowed(submission: dict[str, str] | None, owner) -> bool:
        if principal.is_leader and owner.id == principal.owner_id:
            return True
        return submission is not None and submission.get("api_key_id") == principal.key_id

    try:
        detail = await live_orders.cancel_order(group_id, order_id, allowed=allowed)
    except HTTPException as exc:
        if exc.status_code == 403:
            raise ToolError("這把 key 只能撤回自己送出的單。") from exc
        raise ToolError(_detail(exc)) from exc
    return {"cancelled": order_id, "remaining_total": detail.total_money}


async def open_group(principal: ApiKeyPrincipal, link: str, name: str = "") -> dict[str, Any]:
    if not principal.is_leader:
        raise ToolError("這把 key 只有點單權限，不能開團。")
    try:
        summary = await create_session_from_group_buy_url(link, name, principal.owner_id)
    except PermissionError as exc:
        raise ToolError(str(exc)) from exc
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    except (NidinApiError, OcardApiError) as exc:
        raise ToolError(f"平台回應異常：{exc}") from exc
    except httpx.HTTPError as exc:
        raise ToolError("連不上點餐平台，請稍後再試。") from exc
    return {
        "group_id": summary.session_id,
        "platform": "Ocard" if summary.provider == "ocard" else "Nidin",
        "name": summary.session_name,
        "store": summary.store_name,
    }


async def set_paid(principal: ApiKeyPrincipal, group_id: str, name: str, paid: bool) -> dict[str, Any]:
    _session(group_id)
    _require_leader_of(principal, group_id)
    try:
        payments = dependencies.get_cart_store().set_payment(group_id, name, paid)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return {"name": name, "paid": bool(payments.get(name))}


def _owner(principal: ApiKeyPrincipal):
    from app import auth as account_auth

    user = account_auth.get_user(principal.owner_id)
    if user is None:
        raise ToolError("發這把 key 的主揪帳號已經不存在。")
    return user


async def find_stores(principal: ApiKeyPrincipal, brand: str, sort: str = "distance") -> dict[str, Any]:
    if not principal.is_leader:
        raise ToolError("這把 key 只有點單權限，不能開團。")
    if sort not in ("distance", "rating"):
        raise ToolError("sort 只能是 distance（最近）或 rating（評分相對高）。")
    try:
        result = await nidin_group_buys.search_stores(_owner(principal), brand, sort)
    except HTTPException as exc:
        raise ToolError(_detail(exc)) from exc
    return result.model_dump()


async def open_new_group(principal: ApiKeyPrincipal, store_id: str) -> dict[str, Any]:
    if not principal.is_leader:
        raise ToolError("這把 key 只有點單權限，不能開團。")
    try:
        imported = await nidin_group_buys.open_group_at_store(_owner(principal), store_id)
    except HTTPException as exc:
        raise ToolError(_detail(exc)) from exc
    session = imported.session
    return {
        "group_id": session.session_id,
        "platform": "Nidin",
        "name": session.session_name,
        "link": session.group_buy_url,
        "delivery_minimum": session.delivery_minimum,
    }
