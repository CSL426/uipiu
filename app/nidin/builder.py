"""Validate cart snapshots and build upstream order payloads."""

import json
from typing import Any

from app.models import (
    CartLineSelection,
    CartSessionLine,
    ExpectedOrderLine,
    NormalizedMenu,
    ParsedItem,
    ParsedOrder,
    ParsedSelection,
    validate_menu_snapshot,
)
from app.nidin.normalizer import (
    _decode_map,
    _menu_payload,
    normalize_menu_from_api,
)


def build_group_buy_cart_payload(
    *,
    group_buy_code: str,
    group_buy_brief: dict[str, Any],
    menu_payload: dict,
    parsed_orders: list[ParsedOrder] | None = None,
    parsed_order: ParsedOrder | None = None,
) -> dict[str, Any]:
    menu = _menu_payload(menu_payload)
    products = _decode_map(menu["product_list_json"], "product_id")
    adjusts = _decode_map(menu["adjust_list_json"], "product_id")
    orders = parsed_orders or ([parsed_order] if parsed_order is not None else [])
    if not orders:
        raise ValueError("At least one parsed order is required")

    cart_items: list[dict[str, Any]] = []
    total_money = 0
    for order in orders:
        product = products.get(order.item.id)
        if product is None:
            raise ValueError(f"Unable to find product for item_id={order.item.id}")

        # group_id may be "{product_id}_{adjust_id}" or plain "{adjust_id}"
        grouped_selections: dict[int, list[dict[str, Any]]] = {}
        for selection in order.selections:
            raw = selection.group_id
            adjust_id_from_selection = int(raw.split("_")[-1])
            grouped_selections.setdefault(adjust_id_from_selection, []).append(
                {"option_id": int(selection.option_id), "amount": 1}
            )

        cart_adjusts: list[dict[str, Any]] = []
        for adjust_id in product.get("adjust_list", []):
            adjust = adjusts.get(str(adjust_id))
            if adjust is None:
                continue

            selected_options = grouped_selections.get(int(adjust_id), [])
            min_limit = int(adjust.get("min_limit") or 0)
            if min_limit > 0 and not selected_options:
                raise ValueError(f"Missing required selection for {adjust['name']}")

            if selected_options:
                cart_adjusts.append(
                    {
                        "adjust_id": int(adjust_id),
                        "options": selected_options,
                    }
                )

        combine_list = product.get("combine_list", [])
        if not combine_list:
            raise ValueError(f"Missing combine_list for product_id={order.item.id}")

        subtotal = order.item.price + sum(selection.price for selection in order.selections)
        total_money += subtotal * order.quantity

        cart_items.append(
            {
                "product_id": int(order.item.id),
                "type": int(product.get("type", 1)),
                "amount": order.quantity,
                "combine_id": int(combine_list[0]),
                "options": [],
                "adjusts": cart_adjusts,
            }
        )

    products_payload = {"items": cart_items}

    return {
        "group_buy_code": group_buy_code,
        "is_empty": 0,
        "brand_id": int(group_buy_brief["brand_id"]),
        "menu_id": int(group_buy_brief["menu_id"]),
        "total_money": total_money,
        "products": json.dumps(products_payload, ensure_ascii=False, separators=(",", ":")),
        "store_id": int(group_buy_brief["store_id"]),
        "menu_ver_stamp": int(group_buy_brief["menu_ver_stamp"]),
    }


class MenuChangedError(ValueError):
    pass


class MenuDataError(ValueError):
    pass


def validated_order_menu(payload: dict, store_id: str) -> NormalizedMenu:
    try:
        menu = normalize_menu_from_api(payload, store_id)
        validate_menu_snapshot(menu)
        return menu
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        raise MenuDataError("Nidin 菜單資料格式錯誤，無法確認訂單。") from exc


def build_expected_cart_lines(
    expected_lines: list[ExpectedOrderLine], menu: NormalizedMenu,
) -> list[CartSessionLine]:
    if not expected_lines:
        raise MenuChangedError("購物車是空的，請重新選餐。")
    items = {item.id: item for item in menu.items}
    groups = {group.id: group for group in menu.groups}
    lines = []
    for expected in expected_lines:
        item = items.get(expected.item_id)
        if item is None:
            raise MenuChangedError("品項已停售，請重新選餐。")
        if expected.item_name is not None and expected.item_name != item.name:
            raise MenuChangedError("品項名稱已變更，請重新選餐。")
        selections = []
        for option_id in expected.selection_option_ids:
            matches = [
                (groups[group_id], option)
                for group_id in item.group_ids
                for option in groups[group_id].options
                if option.id == option_id
            ]
            if len(matches) != 1:
                raise MenuChangedError("品項規格已變更，請重新選餐。")
            group, option = matches[0]
            selections.append(CartLineSelection(
                group_id=group.id, group_name=group.name,
                option_id=option.id, option_name=option.name, price=option.price,
            ))
        if (
            expected.selection_option_names is not None
            and expected.selection_option_names != [s.option_name for s in selections]
        ):
            raise MenuChangedError("品項規格已變更，請重新選餐。")
        lines.append(CartSessionLine(
            line_id="expected", contributor_name="", created_at="",
            item_id=item.id, item_name=item.name,
            quantity=expected.quantity, selections=selections,
            line_total=expected.line_total,
        ))
    return lines


def validate_cart_lines(lines: list[CartSessionLine], menu: NormalizedMenu) -> None:
    validate_menu_snapshot(menu)
    items = {item.id: item for item in menu.items}
    groups = {group.id: group for group in menu.groups}
    message = "菜單品項、價格、規格或供應狀態已變更，請重新選餐。"
    for line in lines:
        item = items.get(line.item_id)
        if item is None or item.name != line.item_name or line.quantity < 1:
            raise MenuChangedError(message)
        counts = {group_id: 0 for group_id in item.group_ids}
        seen = set()
        for selection in line.selections:
            group = groups.get(selection.group_id)
            if group is None or group.id not in counts:
                raise MenuChangedError(message)
            option = next((o for o in group.options if o.id == selection.option_id), None)
            key = (selection.group_id, selection.option_id)
            if (
                option is None or key in seen
                or option.price != selection.price
                or option.name != selection.option_name
                or group.name != selection.group_name
            ):
                raise MenuChangedError(message)
            seen.add(key)
            counts[group.id] += 1
        for group_id, count in counts.items():
            group = groups[group_id]
            if not group.min_select <= count <= group.max_select:
                raise MenuChangedError(message)
        total = (item.price + sum(s.price for s in line.selections)) * line.quantity
        if total != line.line_total:
            raise MenuChangedError(message)


def build_parsed_orders_from_cart_lines(lines: list[CartSessionLine], menu: NormalizedMenu) -> list[ParsedOrder]:
    item_map = {item.id: item for item in menu.items}
    parsed_orders: list[ParsedOrder] = []
    for line in lines:
        item = item_map.get(line.item_id)
        if item is None:
            raise ValueError(f"Unable to find item in session menu for item_id={line.item_id}")

        parsed_orders.append(
            ParsedOrder(
                quantity=line.quantity,
                item=ParsedItem(
                    id=item.id,
                    name=item.name,
                    price=item.price,
                ),
                selections=[
                    ParsedSelection(
                        group_id=selection.group_id,
                        group_name=selection.group_name,
                        option_id=selection.option_id,
                        option_name=selection.option_name,
                        price=selection.price,
                        source_token=selection.option_name,
                    )
                    for selection in line.selections
                ],
            )
        )

    return parsed_orders
