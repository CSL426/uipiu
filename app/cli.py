"""主揪在容器裡操作團購的指令：python -m app.cli <指令>。輸出一律是 JSON，失敗時 exit 1。

以站長身分走 agent_tools（和 MCP 的主揪 key 相同的權限與流程），不另外實作一份。
.claude/skills/group-order 會用到這裡。
"""

import argparse
import asyncio
import json
import sys
from typing import Any

import httpx
from fastapi import HTTPException

import app.main  # noqa: F401  — 載入時才會設定 auth 的資料庫路徑
from app import agent_tools, dependencies, store_search
from app import auth as account_auth
from app.agent_tools import ToolError
from app.api_keys import ApiKeyPrincipal
from app.deps_shared import initialize_legacy_ownership
from app.nidin.client import DELIVERY_TYPE_DELIVERY, DELIVERY_TYPE_PICKUP, NidinApiError
from app.ocard import OcardApiError, target_from_session
from app.platforms import platform_for_session
from app.routers.leader_auth import call_with_nidin_relogin


def _principal() -> ApiKeyPrincipal:
    initialize_legacy_ownership()
    owner_id = account_auth.first_admin_id()
    if owner_id is None:
        raise ToolError("站上還沒有主揪帳號。")
    return ApiKeyPrincipal(key_id="cli", owner_id=owner_id, name="cli", scope="leader")


async def _nidin_pickup(session: Any, principal: ApiKeyPrincipal) -> dict[str, Any]:
    store = dependencies.get_cart_store()
    order_id = store.nidin_order_id(session.session_id)
    if not order_id:
        return {"pickup": "unknown", "note": "這個團沒有記下團長單號。"}
    client = dependencies.get_nidin_client()
    user = account_auth.get_user(principal.owner_id)
    basic, _ = await call_with_nidin_relogin(
        user, lambda token, user_id: client.fetch_order_basic(token=token, user_id=user_id, order_id=order_id)
    )
    kind = basic.get("delivery_type")
    return {
        "pickup": {DELIVERY_TYPE_DELIVERY: "delivery", DELIVERY_TYPE_PICKUP: "pickup"}.get(kind, f"type-{kind}"),
        "has_delivery_address": bool(basic.get("delivery_address")),
        "distance_m": basic.get("delivery_distance"),
    }


async def _ocard_pickup(session: Any) -> dict[str, Any]:
    target = target_from_session(session.store_id, session.group_buy_code)
    group, _ = await dependencies.get_ocard_client().fetch_group(target)
    order_type = str(group.get("orderType") or "")
    # property 是團長的外送地點，只回答有沒有設，不把內容印出來
    place = group.get("property")
    try:
        place = json.loads(place) if isinstance(place, str) and place else place
    except ValueError:
        place = None
    return {
        "pickup": {"DELIVERY": "delivery", "PICKUP": "pickup"}.get(order_type, order_type.lower()),
        "has_delivery_address": isinstance(place, dict) and bool(place.get("lat") or place.get("placeId")),
        "order_time": group.get("orderDatetime") or group.get("orderTime"),
    }


async def status(group_id: str) -> dict[str, Any]:
    """這個團在平台上還開著嗎、取餐方式是什麼。只讀。"""
    principal = _principal()
    try:
        session = dependencies.get_cart_store().get_session(group_id)
    except KeyError as exc:
        raise ToolError("找不到這個團。先用 groups 看有哪些團。") from exc
    platform = platform_for_session(session)
    result: dict[str, Any] = {"group_id": group_id, "name": session.session_name, "platform": platform.label}
    try:
        await platform.ensure_open(session)
        result["open"] = True
    except HTTPException as exc:
        result["open"] = False
        result["closed_reason"] = str(exc.detail)
    if result["open"]:
        reader = _ocard_pickup(session) if session.provider == "ocard" else _nidin_pickup(session, principal)
        result.update(await reader)
    return result


async def disabled(store_id: str) -> dict[str, Any]:
    """這家 Nidin 分店今天停賣什麼。公開菜單看不出來，要到團長結帳才會被擋。"""
    return await dependencies.get_nidin_client().list_disabled_items(store_id)


async def leader_address() -> dict[str, Any]:
    """主揪在 Nidin 的預設外送地址。Ocard 帳號沒存地址時，開完團要拿這個去填。"""
    principal = _principal()
    client = dependencies.get_nidin_client()
    user = account_auth.get_user(principal.owner_id)
    addresses, _ = await call_with_nidin_relogin(
        user, lambda token, user_id: client.list_delivery_addresses(token=token, user_id=user_id)
    )
    address = store_search.default_delivery_address(addresses)
    if address is None:
        raise ToolError("主揪在 Nidin 沒有可用的外送地址。")
    return {"address": address.get("address"), "note": address.get("loc_name") or ""}


async def remove(group_id: str) -> dict[str, Any]:
    """只刪本站紀錄，平台上的團不受影響。"""
    principal = _principal()
    store = dependencies.get_cart_store()
    try:
        session = store.get_session(group_id)
    except KeyError as exc:
        raise ToolError("找不到這個團。") from exc
    if store.session_owner(group_id) != principal.owner_id:
        raise ToolError("這個團不是這個帳號開的。")
    store.delete_session(group_id)
    return {"removed": group_id, "name": session.session_name}


def _items(values: list[str]) -> list[dict[str, Any]]:
    items = []
    for value in values:
        try:
            item = json.loads(value)
        except ValueError as exc:
            raise ToolError(f"--item 要是 JSON：{value}") from exc
        if not isinstance(item, dict):
            raise ToolError(f"--item 要是 JSON 物件：{value}")
        items.append(item)
    return items


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="主揪操作飲料團")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("groups", help="列出本站的團")
    find = commands.add_parser("find-stores", help="依品牌找 Nidin 分店")
    find.add_argument("brand")
    find.add_argument("--sort", choices=["distance", "rating"], default="distance")
    commands.add_parser("disabled", help="Nidin 分店今天停賣什麼").add_argument("store_id")
    commands.add_parser("open", help="在 Nidin 分店開新團並加進本站").add_argument("store_id")
    add = commands.add_parser("import", help="把現成的揪團連結加進本站（Nidin／Ocard）")
    add.add_argument("link")
    add.add_argument("--name", default="")
    commands.add_parser("status", help="團還開著嗎、取餐方式").add_argument("group_id")
    menu = commands.add_parser("menu", help="菜單與規格")
    menu.add_argument("group_id")
    menu.add_argument("--search", default="")
    order = commands.add_parser("order", help="送出一張跟團單（真的會下單）")
    order.add_argument("group_id")
    order.add_argument("--name", required=True)
    order.add_argument("--item", action="append", required=True, help='{"item_id":"…","quantity":1,"option_ids":["…"]}')
    order.add_argument("--phone", default="")
    order.add_argument("--idempotency-key", default="")
    commands.add_parser("orders", help="同步並列出每個人點了什麼").add_argument("group_id")
    cancel = commands.add_parser("cancel", help="撤回一張跟團單")
    cancel.add_argument("group_id")
    cancel.add_argument("order_id")
    paid = commands.add_parser("paid", help="標記已收款")
    paid.add_argument("group_id")
    paid.add_argument("name")
    paid.add_argument("--unpaid", action="store_true", help="改回未收款")
    commands.add_parser("remove", help="從本站移除團（不影響平台）").add_argument("group_id")
    commands.add_parser("leader-address", help="主揪在 Nidin 的預設外送地址（填 Ocard 用）")
    return parser


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "status":
        return await status(args.group_id)
    if args.command == "disabled":
        return await disabled(args.store_id)
    if args.command == "remove":
        return await remove(args.group_id)
    if args.command == "leader-address":
        return await leader_address()

    principal = _principal()
    if args.command == "groups":
        return await agent_tools.list_groups(principal)
    if args.command == "find-stores":
        return await agent_tools.find_stores(principal, args.brand, args.sort)
    if args.command == "open":
        return await agent_tools.open_new_group(principal, args.store_id)
    if args.command == "import":
        return await agent_tools.open_group(principal, args.link, args.name)
    if args.command == "menu":
        return await agent_tools.get_menu(principal, args.group_id, args.search)
    if args.command == "order":
        return await agent_tools.place_order(
            principal,
            args.group_id,
            args.name,
            _items(args.item),
            phone=args.phone,
            idempotency_key=args.idempotency_key,
        )
    if args.command == "orders":
        return await agent_tools.list_orders(principal, args.group_id)
    if args.command == "cancel":
        return await agent_tools.cancel_order(principal, args.group_id, args.order_id)
    if args.command == "paid":
        return await agent_tools.set_paid(principal, args.group_id, args.name, not args.unpaid)
    raise ToolError(f"不認得的指令：{args.command}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = asyncio.run(run(args))
    except HTTPException as exc:
        message = str(exc.detail)
    except (ToolError, NidinApiError, OcardApiError) as exc:
        message = str(exc)
    except httpx.HTTPError as exc:
        message = f"連不上平台：{exc}"
    else:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0
    print(json.dumps({"error": message}, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    sys.exit(main())
