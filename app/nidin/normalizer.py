"""Decode upstream menus and official orders into local models."""

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from app.models import (
    GroupBuyTarget,
    MenuCategory,
    MenuGroup,
    MenuItem,
    MenuOption,
    NormalizedMenu,
    OfficialFollowerItem,
    OfficialFollowerOrder,
    OfficialGroupBuySummary,
    StoreDeliverySummary,
)


STORE_ID_PATTERN = re.compile(r"/menu/(?P<store_id>\d+)")


GROUP_BUY_PATTERN = re.compile(r"/gb/(?:menu|login)/(?P<store_id>\d+)/(?P<group_buy_code>[A-Za-z0-9_-]+)")


DECORATIVE_EDGE_PATTERN = re.compile(r"^\W+|\W+$")


MENU_TEXT_PATTERN = re.compile(r"[A-Za-z0-9\u3400-\u9fff]+")


SIZE_PREFIX_PATTERN = re.compile(r"^(?P<size>[大中小])(?:\W+)(?P<name>.+)$")


TAIWAN_MOBILE_PATTERN = re.compile(r"^09\d{8}$")


def extract_store_id(value: str) -> str:
    candidate = value.strip()
    if candidate.isdigit():
        return candidate

    parsed = urlparse(candidate)
    match = STORE_ID_PATTERN.search(parsed.path)
    if match:
        return match.group("store_id")

    last_segment = parsed.path.rstrip("/").split("/")[-1]
    if last_segment.isdigit():
        return last_segment

    raise ValueError(f"Unable to extract store_id from: {value}")


def extract_group_buy_target(value: str) -> GroupBuyTarget:
    parsed = urlparse(value.strip())
    match = GROUP_BUY_PATTERN.search(parsed.path)
    if match:
        return GroupBuyTarget(
            store_id=match.group("store_id"),
            group_buy_code=match.group("group_buy_code"),
        )

    raise ValueError(f"Unable to extract group buy target from: {value}")


def normalize_menu(payload: dict) -> NormalizedMenu:
    """Normalize the legacy {items, groups} dict shape (used by /parse-order endpoint)."""
    items = [
        MenuItem(
            id=str(item["id"]),
            name=item["name"],
            price=int(item.get("price", 0)),
            group_ids=[str(group_id) for group_id in item.get("group_ids", [])],
            aliases=list(item.get("aliases", [])),
        )
        for item in payload.get("items", [])
    ]
    groups = [
        MenuGroup(
            id=str(group["id"]),
            name=group["name"],
            min_select=int(group.get("min_select", 0)),
            max_select=int(group.get("max_select", 1)),
            options=[
                MenuOption(
                    id=str(option["id"]),
                    name=option["name"],
                    price=int(option.get("price", 0)),
                    aliases=list(option.get("aliases", [])),
                )
                for option in group.get("options", [])
            ],
        )
        for group in payload.get("groups", [])
    ]

    return NormalizedMenu(
        store_id=str(payload.get("store_id", "")),
        store_name=payload.get("store_name", ""),
        items=items,
        groups=groups,
        categories=[],
    )


def _decode_list(list_json: dict) -> list[dict]:
    """Decode Nidin's compressed schema+data format into a list of dicts."""
    schema: dict[str, int] = list_json.get("schema", {})
    data: dict | list = list_json.get("data", {})
    rows = data.values() if isinstance(data, dict) else data
    return [{field: row[idx] for field, idx in schema.items() if idx < len(row)} for row in rows]


def _decode_map(list_json: dict, key_field: str) -> dict[str, dict]:
    return {str(row[key_field]): row for row in _decode_list(list_json)}


def _menu_payload(payload: dict) -> dict:
    return payload.get("menu", payload)


def _compact_menu_text(value: str) -> str:
    return "".join(MENU_TEXT_PATTERN.findall(value))


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _derive_item_aliases(name: str) -> list[str]:
    aliases: list[str] = []

    compact = _compact_menu_text(name)
    if compact and compact != name:
        aliases.append(compact)

    size_match = SIZE_PREFIX_PATTERN.match(name)
    if size_match:
        size_stripped = _compact_menu_text(size_match.group("name"))
        if size_stripped and size_stripped != name:
            aliases.append(size_stripped)

    return _dedupe_preserve_order(aliases)


def _derive_option_aliases(name: str) -> list[str]:
    aliases: list[str] = []
    normalized = DECORATIVE_EDGE_PATTERN.sub("", name).strip()
    if normalized and normalized != name:
        aliases.append(normalized)
    return aliases


def _normalize_store_delivery_summary(payload: dict, store_id: str) -> StoreDeliverySummary:
    delivery_candidates = [
        int(entry.get("order_min_price") or 0)
        for entry in (payload.get("delivery_info") or [])
        if int(entry.get("order_min_price") or 0) > 0
    ]

    return StoreDeliverySummary(
        store_id=store_id,
        store_name=str(payload.get("name") or ""),
        delivery_minimum=min(delivery_candidates) if delivery_candidates else None,
        delivery_description=str(payload.get("delivery_way_description") or ""),
        order_status_reason=str(payload.get("order_status_reason") or ""),
    )


def _resolve_guest_phone(value: str) -> str:
    """正規化點單人自填的手機；沒填回空字串。

    以前沒填就亂數產生一支 09 號碼，團長在 Nidin 上會看到陌生人的號碼。
    Nidin 官網的訪客註冊本來就只在有填時才帶 phone，跟團送單的聯絡方式沒填就送空字串。
    """
    digits = re.sub(r"\D", "", value)
    if digits.startswith("8869") and len(digits) == 12:
        digits = f"0{digits[3:]}"
    elif digits.startswith("9") and len(digits) == 9:
        digits = f"0{digits}"

    if not digits:
        return ""

    if TAIWAN_MOBILE_PATTERN.fullmatch(digits):
        return digits

    raise ValueError("手機格式錯誤，請輸入 09 開頭的 10 碼手機。")


def _resolve_product_price(prod: dict, combine_rows: dict[str, dict]) -> int:
    direct_price = int(prod.get("price") or 0)
    if direct_price > 0:
        return direct_price

    product_id = int(prod["product_id"])
    candidate_prices: list[int] = []
    for combine_id in prod.get("combine_list", []) or []:
        combine = combine_rows.get(str(combine_id))
        if combine is None:
            continue

        combine_price = int(combine.get("price") or 0)
        if combine_price > 0:
            candidate_prices.append(combine_price)

        edge_list = combine.get("edge_list") or {}
        edges = edge_list.values() if isinstance(edge_list, dict) else edge_list
        for edge in edges:
            if not isinstance(edge, list) or len(edge) < 3:
                continue
            edge_product_id = int(edge[0] or 0)
            edge_price = int(edge[2] or 0)
            edge_product_type = int(edge[4] or 0) if len(edge) > 4 else 0
            if edge_product_id != product_id or edge_price <= 0:
                continue
            if edge_product_type not in (0, 1):
                continue
            candidate_prices.append(edge_price)

    return min(candidate_prices) if candidate_prices else 0


def _extract_product_image_urls(payload: dict[str, Any]) -> dict[str, str]:
    product_list = payload.get("product_list") or {}
    image_urls: dict[str, str] = {}
    for product_id, image_payload in product_list.items():
        if not isinstance(image_payload, dict):
            continue

        image_url = ""
        sub_images = image_payload.get("sub_image_list") or []
        if sub_images:
            image_url = str((sub_images[0] or {}).get("url") or "").strip()

        if not image_url:
            main_image = image_payload.get("main_image") or {}
            image_url = str(main_image.get("url") or "").strip()

        if image_url:
            image_urls[str(product_id)] = image_url

    return image_urls


def _build_valid_options_per_product(
    combines: list[dict],
    product_name_to_id: dict[str, int],
    option_ids_by_adjust: dict[int, list[int]],
) -> dict[int, set[int]]:
    """Build mapping of product_id -> set of valid option_ids from combine edge_lists.

    edge_list keys can be either option_ids (direct) or adjust_ids (group-level).
    When a key is an adjust_id, expand it to all option_ids under that adjust group.
    """
    all_option_ids = {oid for oids in option_ids_by_adjust.values() for oid in oids}

    result: dict[int, set[int]] = {}
    for combo in combines:
        edge_keys = {int(k) for k in combo.get("edge_list", {}).keys()}
        resolved_option_ids: set[int] = set()
        for key in edge_keys:
            if key in all_option_ids:
                resolved_option_ids.add(key)
            elif key in option_ids_by_adjust:
                resolved_option_ids.update(option_ids_by_adjust[key])
        for group in combo.get("common_list", []):
            for item in group:
                if not isinstance(item, list) or len(item) < 1 or not isinstance(item[0], str):
                    continue
                prod_id = product_name_to_id.get(item[0])
                if prod_id is not None:
                    result.setdefault(prod_id, set()).update(resolved_option_ids)
    return result


def normalize_menu_from_api(
    payload: dict,
    store_id: str,
    *,
    product_image_urls: dict[str, str] | None = None,
) -> NormalizedMenu:
    """Normalize the real Nidin API response from /store/{id}/onShelfMenu."""
    menu = _menu_payload(payload)
    required_fields = {
        "product_list_json": {"product_id", "name", "price", "type", "is_enable", "is_visible", "adjust_list"},
        "adjust_list_json": {"product_id", "name", "min_limit", "max_limit"},
        "option_list_json": {"product_id", "name", "price", "adjust_id"},
    }
    for key, fields in required_fields.items():
        table = menu[key]
        if not fields.issubset(table.get("schema", {})) or "data" not in table:
            raise ValueError("上游菜單欄位不完整。")
        rows = table["data"].values() if isinstance(table["data"], dict) else table["data"]
        indices = [table["schema"][field] for field in fields]
        if any(len(row) <= max(indices) for row in rows):
            raise ValueError("上游菜單資料列不完整。")
    image_urls = product_image_urls or {}

    products = _decode_list(menu["product_list_json"])
    adjusts = _decode_list(menu["adjust_list_json"])
    options = _decode_list(menu["option_list_json"])
    combines = _decode_list(menu["combine_list_json"]) if menu.get("combine_list_json") else []
    combine_rows = {str(c["combine_id"]): c for c in combines}
    category_rows = _decode_list(menu.get("category_list_json", {})) if menu.get("category_list_json") else []

    categories: list[MenuCategory] = []
    category_by_product_id: dict[int, MenuCategory] = {}
    for row in sorted(category_rows, key=lambda candidate: int(candidate.get("sort") or 0)):
        if not row.get("is_enable") or not row.get("is_visible"):
            continue
        category = MenuCategory(
            id=str(row["product_id"]),
            name=row["name"],
            sort=int(row.get("sort") or 0),
        )
        categories.append(category)
        for product_id in row.get("product_list", []):
            category_by_product_id[int(product_id)] = category

    # Build option lookup by adjust_id
    option_obj_by_id: dict[int, MenuOption] = {}
    options_by_adjust: dict[int, list[MenuOption]] = {}
    for opt in options:
        if not opt.get("is_enable", True) or not opt.get("is_visible", True):
            continue
        adjust_id = opt["adjust_id"]
        menu_opt = MenuOption(
            id=str(opt["product_id"]),
            name=opt["name"],
            price=int(opt["price"] or 0),
            aliases=_derive_option_aliases(opt["name"]),
        )
        option_obj_by_id[int(opt["product_id"])] = menu_opt
        options_by_adjust.setdefault(adjust_id, []).append(menu_opt)

    # Build adjust (group) metadata
    adjust_meta: dict[int, dict] = {}
    for adj in adjusts:
        adjust_meta[adj["product_id"]] = adj

    # Build product_name -> product_id map for combine lookup
    product_name_to_id: dict[str, int] = {}
    for prod in products:
        if prod.get("type") == 1 and prod.get("is_enable") and prod.get("is_visible"):
            product_name_to_id[prod["name"]] = int(prod["product_id"])

    # Build raw option_id lists keyed by adjust_id for combine resolution
    raw_option_ids_by_adjust: dict[int, list[int]] = {}
    for opt in options:
        raw_option_ids_by_adjust.setdefault(opt["adjust_id"], []).append(int(opt["product_id"]))

    # Build per-product valid option_ids from combine edge_lists
    valid_options_per_product = _build_valid_options_per_product(
        combines, product_name_to_id, raw_option_ids_by_adjust,
    )

    # Build items with per-product filtered groups
    items = []
    all_groups: dict[str, MenuGroup] = {}

    for prod in products:
        if not prod.get("is_enable") or not prod.get("is_visible"):
            continue
        if prod.get("type") != 1:
            continue

        prod_id = int(prod["product_id"])
        adjust_ids: list[int] = prod.get("adjust_list", [])
        valid_opt_ids = valid_options_per_product.get(prod_id)

        item_group_ids: list[str] = []
        for aid in adjust_ids:
            meta = adjust_meta.get(aid)
            if not meta:
                raise ValueError("上游菜單缺少品項規格。")

            all_opts = options_by_adjust.get(aid, [])

            # Filter options to only those valid for this product (from combine edge_list)
            if valid_opt_ids:
                filtered_opts = [o for o in all_opts if int(o.id) in valid_opt_ids]
            else:
                filtered_opts = all_opts

            if not filtered_opts and int(meta["min_limit"] or 0) == 0:
                continue

            # Use per-product group key to avoid sharing across products
            group_key = f"{prod_id}_{aid}"
            all_groups[group_key] = MenuGroup(
                id=group_key,
                name=meta["name"],
                min_select=int(meta["min_limit"] or 0),
                max_select=int(meta["max_limit"] or 1),
                options=filtered_opts,
            )
            item_group_ids.append(group_key)

        category = category_by_product_id.get(prod_id)
        if category_by_product_id and category is None:
            continue

        items.append(
            MenuItem(
                id=str(prod_id),
                name=prod["name"],
                price=_resolve_product_price(prod, combine_rows),
                group_ids=item_group_ids,
                aliases=_derive_item_aliases(prod["name"]),
                image_url=image_urls.get(str(prod_id)),
                category_id=category.id if category else None,
                category_name=category.name if category else None,
            )
        )

    return NormalizedMenu(
        store_id=store_id,
        items=items,
        groups=list(all_groups.values()),
        categories=categories,
    )


def _extract_option_pairs(item_dict: dict[str, Any]) -> list[tuple[str, str]]:
    """(規格群組名稱, 選項名稱)，群組不明時為空字串；同一對只留一次。"""
    pairs: list[tuple[str, str]] = []

    # Case 1: already list of strings
    if "options" in item_dict and isinstance(item_dict["options"], list):
        for opt in item_dict["options"]:
            if isinstance(opt, str) and opt.strip():
                pairs.append(("", opt.strip()))
            elif isinstance(opt, dict):
                name = opt.get("option_name") or opt.get("name")
                if name:
                    pairs.append((str(opt.get("group_name") or ""), str(name).strip()))

    # Case 2: adjusts（真實團購資料的規格都在這裡，adjust 的 name 就是群組名稱）
    if "adjusts" in item_dict and isinstance(item_dict["adjusts"], list):
        for adj in item_dict["adjusts"]:
            if isinstance(adj, dict):
                group_name = str(adj.get("name") or "").strip()
                for sub_opt in adj.get("options", []):
                    if isinstance(sub_opt, dict):
                        name = sub_opt.get("option_name") or sub_opt.get("name")
                        if name:
                            pairs.append((group_name, str(name).strip()))
                    elif isinstance(sub_opt, str) and sub_opt.strip():
                        pairs.append((group_name, sub_opt.strip()))

    return list(dict.fromkeys(pairs))


def _first_int(*values: Any) -> int | None:
    for value in values:
        if value is not None and value != "":
            return int(value)
    return None


def _parse_official_products(raw_products: list[Any]) -> tuple[list[OfficialFollowerItem], int]:
    items: list[OfficialFollowerItem] = []
    cup_count = 0
    for prod in raw_products:
        if not isinstance(prod, dict):
            continue

        amount = int(prod.get("amount") or prod.get("quantity") or 1)
        price = int(prod.get("price") or 0)
        nidin_total = _first_int(
            prod.get("total_money"), prod.get("total_price"), prod.get("total"), prod.get("subtotal")
        )
        subtotal = nidin_total if nidin_total is not None else price * amount
        pairs = _extract_option_pairs(prod)
        remark = "；".join(
            str(prod.get(key)).strip()
            for key in ("remark", "shopper_remark", "remark_memo", "sticker_memo")
            if prod.get(key) and str(prod.get(key)).strip()
        )
        items.append(
            OfficialFollowerItem(
                product_id=prod.get("product_id") or "",
                name=str(prod.get("name") or prod.get("product_name") or "未命名商品"),
                amount=amount,
                price=price,
                subtotal=subtotal,
                options=[name for _, name in pairs],
            option_groups=[group for group, _ in pairs],
                remark=remark,
            )
        )
        cup_count += amount
    return items, cup_count


def _nidin_time_to_iso(value: Any) -> str:
    """Nidin 回的是台灣時間字串 "2026-03-26 11:30:36"；轉成 UTC ISO。

    本站自己寫的 created_at 是 UTC，資料庫用字串排序，兩種時區混用會排錯。
    """
    try:
        local = datetime.strptime(str(value or "").strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ""
    return local.replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc).isoformat()


def _products_of(order: dict[str, Any]) -> list[Any]:
    return order.get("product") or order.get("product_simple") or order.get("items") or []


def normalize_official_group_buy_info(payload: dict[str, Any], group_buy_code: str) -> OfficialGroupBuySummary:
    """Normalize the official Nidin response from /groupBuy/{code}/info.

    欄位依 2026-09-24 對真實團購（4 團 36 筆跟團）的比對：
    - 規格只在完整的 `product[].adjusts[].options[]`；`product_simple` 沒有規格，只能當備援。
    - 金額直接用 Nidin 的 `total_money`（已含大杯、加料加價），不自己用 price×數量重算；
      `price` 只是基本價。跟團者層級的 `total_money` 真實資料為 null，只好加總品項。
    - 跟團者名字在 `contact_name`；團的狀態在 `head_buying_status`。
    - 團長自己點的飲料在 `group_buy_head_order_info.product`，格式同跟團者；不算進來，
      總額會比 Nidin 少（實例：跟團 855 + 團長 55 = Nidin 的 910）。
    舊欄位名（name、options 字串陣列、status…）保留為備援。
    """
    head_info = payload.get("group_buy_head_order_info") or payload.get("head_info") or {}
    follower_list = payload.get("group_buy_follower_order_list") or payload.get("follower_list") or []

    followers_out: list[OfficialFollowerOrder] = []
    total_items_count = 0
    calculated_total_money = 0

    head_items, head_cups = _parse_official_products(_products_of(head_info))
    if head_items:
        head_total = sum(item.subtotal for item in head_items)
        followers_out.append(
            OfficialFollowerOrder(
                order_id=f"head:{head_info.get('head_sn') or group_buy_code}",
                follower_name="團長",
                is_head=True,
                created_at=_nidin_time_to_iso(head_info.get("head_create_time")),
                total_money=head_total,
                item_count=head_cups,
                items=head_items,
            )
        )
        calculated_total_money += head_total
        total_items_count += head_cups

    for follower in follower_list:
        if not isinstance(follower, dict):
            continue

        follower_items, follower_item_count = _parse_official_products(_products_of(follower))
        follower_total = _first_int(follower.get("total_money"))
        order_total = follower_total if follower_total is not None else sum(it.subtotal for it in follower_items)
        calculated_total_money += order_total
        total_items_count += follower_item_count

        follower_name = str(
            follower.get("contact_name")
            or follower.get("name")
            or follower.get("nickname")
            or follower.get("user_name")
            or follower.get("guest_name")
            or "未知跟團者"
        ).strip()

        followers_out.append(
            OfficialFollowerOrder(
                order_id=follower.get("order_id") or "",
                follower_name=follower_name,
                created_at=_nidin_time_to_iso(follower.get("create_time")),
                phone=str(follower.get("phone") or follower.get("contact_method_remark") or ""),
                total_money=order_total,
                item_count=follower_item_count,
                items=follower_items,
            )
        )

    head_total = _first_int(head_info.get("total_money"))
    if head_total is None:
        head_total = calculated_total_money

    return OfficialGroupBuySummary(
        group_buy_code=group_buy_code,
        group_buying_name=str(head_info.get("group_buying_name") or ""),
        brand_name=str(head_info.get("brand_name") or ""),
        store_name=str(head_info.get("store_name") or ""),
        store_id=str(head_info.get("store_id") or ""),
        status=int(head_info.get("head_buying_status") or head_info.get("status") or 0),
        head_name=str(head_info.get("shopper_name") or head_info.get("name") or head_info.get("head_name") or ""),
        head_user_id=head_info.get("user_id") or head_info.get("shopper_id") or "",
        total_money=head_total,
        total_items=total_items_count,
        follower_orders=followers_out,
    )
