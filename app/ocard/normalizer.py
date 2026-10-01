"""Ocard 回應轉成本站模型：揪團網址、菜單、跟團清單。"""

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.models import (
    MenuCategory,
    MenuGroup,
    MenuItem,
    MenuOption,
    NormalizedMenu,
    OfficialFollowerItem,
    OfficialFollowerOrder,
    OfficialGroupBuySummary,
)
from app.nidin.normalizer import _derive_item_aliases, _derive_option_aliases

OCARD_HOST = "order.ocard.co"
_SLUG = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# 對齊 Nidin 的 group_buying_status，讓共用的送單流程用同一套判斷
GROUP_OPEN = 10
GROUP_CLOSED = 20


@dataclass(frozen=True)
class OcardTarget:
    brand_id: str
    store_id: str
    group_code: str

    @property
    def session_store_id(self) -> str:
        """本站 sessions.store_id：Ocard 的店要品牌＋分店兩段才能定位。"""
        return f"{self.brand_id}/{self.store_id}"

    @property
    def url(self) -> str:
        return f"https://{OCARD_HOST}/{self.brand_id}/{self.store_id}?group={self.group_code}"


@dataclass
class OcardMenu:
    menu: NormalizedMenu
    # 選項 id → 不能同時選的選項 id（Ocard 的 block，例如熱飲不能加凍類配料）
    exclusions: dict[str, set[str]] = field(default_factory=dict)
    # 跟團單品項帶的是 item_id，送單要用的是菜單的 id；同步時靠這張表對回本站菜單
    menu_id_by_item_id: dict[str, str] = field(default_factory=dict)


def is_ocard_url(value: str) -> bool:
    return urlparse(value.strip()).hostname == OCARD_HOST


def parse_group_url(value: str) -> OcardTarget:
    parsed = urlparse(value.strip())
    if parsed.hostname != OCARD_HOST:
        raise ValueError("不是 Ocard 線上點的揪團連結。")
    parts = [part for part in parsed.path.split("/") if part]
    group_code = (parse_qs(parsed.query).get("group") or [""])[0]
    if len(parts) < 2 or not group_code:
        raise ValueError("Ocard 連結要是揪團連結（網址帶 ?group=…），請從「分享揪團」複製。")
    brand_id, store_id = parts[0], parts[1]
    if not all(_SLUG.match(part) for part in (brand_id, store_id, group_code)):
        raise ValueError("Ocard 揪團連結格式不正確。")
    return OcardTarget(brand_id=brand_id, store_id=store_id, group_code=group_code)


def target_from_session(store_id: str, group_buy_code: str) -> OcardTarget:
    brand_id, _, branch_id = store_id.partition("/")
    if not brand_id or not branch_id:
        raise ValueError("這個團的 Ocard 店家資料不完整。")
    return OcardTarget(brand_id=brand_id, store_id=branch_id, group_code=group_buy_code)


def normalize_menu(
    target: OcardTarget,
    store_name: str,
    products: list[dict[str, Any]],
    item_details: dict[str, dict[str, Any]],
) -> OcardMenu:
    """products 是 menu/get 的分類列表；item_details 是各品項 menu/item 的 data，以菜單 id 為鍵。"""
    groups: dict[str, MenuGroup] = {}
    items: list[MenuItem] = []
    categories: list[MenuCategory] = []
    exclusions: dict[str, set[str]] = {}
    menu_id_by_item_id: dict[str, str] = {}

    for sort, category in enumerate(products):
        category_id = str(category.get("id") or f"cat{sort}")
        category_name = str(category.get("name") or "")
        categories.append(MenuCategory(id=category_id, name=category_name, sort=sort))
        for raw in category.get("items") or []:
            menu_id = str(raw.get("id") or "")
            detail = item_details.get(menu_id)
            if not menu_id or detail is None or raw.get("soldOut") or detail.get("can_purchase") is False:
                continue
            group_ids: list[str] = []
            usable = True
            for add in detail.get("adds") or []:
                options = [
                    option for option in add.get("options") or []
                    if not option.get("soldOut") and not option.get("unavailable")
                ]
                minimum = int(add.get("min") or 0)
                if add.get("required"):
                    minimum = max(minimum, 1)
                if len(options) < minimum:
                    # 必選規格全部售完＝這項其實點不了
                    usable = False
                    break
                group_id = str(add["id"])
                group_ids.append(group_id)
                if group_id not in groups:
                    groups[group_id] = MenuGroup(
                        id=group_id,
                        name=str(add.get("name") or ""),
                        min_select=minimum,
                        max_select=max(int(add.get("max") or 1), minimum, 1),
                        options=[
                            MenuOption(
                                id=str(option["id"]),
                                name=str(option.get("name") or ""),
                                price=int(option.get("price") or 0),
                                aliases=_derive_option_aliases(str(option.get("name") or "")),
                            )
                            for option in options
                        ],
                    )
                for option in options:
                    blocked = {str(value) for value in option.get("block") or []}
                    if blocked:
                        exclusions.setdefault(str(option["id"]), set()).update(blocked)
            if not usable:
                continue
            name = str(detail.get("name") or raw.get("name") or "")
            items.append(MenuItem(
                id=menu_id,
                name=name,
                price=int(detail.get("price") if detail.get("price") is not None else raw.get("price") or 0),
                group_ids=group_ids,
                aliases=_derive_item_aliases(name),
                image_url=raw.get("image_m") or raw.get("image") or None,
                category_id=category_id,
                category_name=category_name,
            ))
            if raw.get("item_id"):
                menu_id_by_item_id[str(raw["item_id"])] = menu_id

    menu = NormalizedMenu(
        store_id=target.session_store_id,
        store_name=store_name,
        items=items,
        groups=list(groups.values()),
        categories=categories,
    )
    return OcardMenu(menu=menu, exclusions=exclusions, menu_id_by_item_id=menu_id_by_item_id)


def check_exclusions(option_ids: list[str], ocard_menu: OcardMenu) -> None:
    """Ocard 前端會擋互斥選項；伺服器是否也擋不確定，送出前自己先擋。"""
    chosen = set(option_ids)
    names = {option.id: option.name for group in ocard_menu.menu.groups for option in group.options}
    for option_id in option_ids:
        conflict = ocard_menu.exclusions.get(option_id, set()) & chosen
        if conflict:
            other = names.get(next(iter(conflict)), "其他選項")
            raise ValueError(f"「{names.get(option_id, option_id)}」不能和「{other}」一起選。")


def group_status(group: dict[str, Any]) -> int:
    return GROUP_OPEN if group.get("status") == "ORDERING" else GROUP_CLOSED


def normalize_group(
    target: OcardTarget, group: dict[str, Any], menu_id_by_item_id: dict[str, str]
) -> OfficialGroupBuySummary:
    """store/get 帶 group_id 回來的 group 物件。不保留 property（團長外送地點，所有跟團者都拿得到）。"""
    host = group.get("host") or {}
    orders: list[OfficialFollowerOrder] = []
    for order in group.get("orders") or []:
        items: list[OfficialFollowerItem] = []
        for raw in order.get("items") or []:
            adds = raw.get("adds") or []
            count = int(raw.get("count") or 1)
            unit = int(raw.get("unit_price") or 0) + int(raw.get("unit_add_price") or 0)
            item_id = str(raw.get("item_id") or "")
            items.append(OfficialFollowerItem(
                product_id=menu_id_by_item_id.get(item_id, item_id),
                name=str(raw.get("name") or ""),
                amount=count,
                price=unit,
                subtotal=int(raw["subtotal"]) if raw.get("subtotal") is not None else unit * count,
                options=[str(add.get("name") or "") for add in adds],
                option_groups=[str(add.get("group_name") or "") for add in adds],
                remark=str(raw.get("comment") or ""),
            ))
        orders.append(OfficialFollowerOrder(
            order_id=str(order.get("id") or ""),
            follower_name=str(order.get("name") or ""),
            total_money=int(order.get("item_subtotal") or sum(item.subtotal for item in items)),
            item_count=int(order.get("item_count") or sum(item.amount for item in items)),
            items=items,
        ))
    return OfficialGroupBuySummary(
        group_buy_code=target.group_code,
        store_id=target.session_store_id,
        status=group_status(group),
        head_name=str(host.get("name") or ""),
        total_money=int(group.get("item_subtotal") or 0),
        total_items=int(group.get("item_count") or 0),
        follower_orders=[order for order in orders if order.order_id],
    )
