"""用真實 /groupBuy/{code}/info 的結構（2026-09-24 取樣，姓名電話已換成假資料）測同步。"""

import pytest

from app.cart_store import CartStore
from app.nidin import normalize_menu_from_api, normalize_official_group_buy_info
from tests.fixtures import RAW_GROUP_MENU


def _product(name: str, price: int, total: int, adjusts: list[tuple[str, str, int]], **extra):
    return {
        "product_id": 2042754343,
        "name": name,
        "amount": 1,
        "price": price,
        "money": price,
        "total_price": total,
        "total_money": total,
        "type": 1,
        "options": [],
        "adjusts": [
            {"name": group, "options": [{"name": option, "price": add, "actual_amount": 1, "amount": 1}]}
            for group, option, add in adjusts
        ],
        "remark_memo": None,
        "shopper_remark": None,
        "sticker_memo": None,
        **extra,
    }


def _follower(order_id: int, contact: str, product: dict):
    return {
        "order_id": order_id,
        "contact_name": contact,
        "contact_method_remark": "0900000000",
        "total_money": None,
        "product": [product],
        # 真實資料的 product_simple 沒有規格，不能拿它當主要來源
        "product_simple": [{"product_id": product["product_id"], "name": product["name"], "amount": 1, "price": product["price"]}],
    }


REAL_SHAPE = {
    "status": 200,
    "group_buy_info": {"group_buy_list": [], "role": 2},
    "group_buy_head_order_info": {
        "group_buying_name": "測試開的團",
        "head_buying_status": 20,
        "share_code": "ABCDEFGHIJ",
        "product": [],
        "product_simple": [],
    },
    "group_buy_follower_order_list": [
        _follower(1, "小明", _product("焙烏龍", 30, 45, [("份量選擇", "大杯", 5), ("甜度選擇", "微糖", 0), ("飲料溫度", "微冰", 0), ("加購配料", "珍珠", 10)])),
        _follower(2, "小美", _product("春烏龍", 30, 30, [("份量選擇", "中杯", 0), ("甜度選擇", "微微糖", 0), ("飲料溫度", "微冰", 0)], shopper_remark="不要吸管")),
    ],
}


def test_normalizer_takes_specs_names_and_money_straight_from_nidin():
    summary = normalize_official_group_buy_info(REAL_SHAPE, "ABCDEFGHIJ")

    first, second = summary.follower_orders
    assert first.follower_name == "小明"
    assert first.items[0].options == ["大杯", "微糖", "微冰", "珍珠"]
    # Nidin 的 total_money 含大杯 +5、珍珠 +10；不能用 price 30 × 1 重算
    assert first.items[0].subtotal == 45
    assert first.total_money == 45
    assert second.items[0].remark == "不要吸管"
    assert summary.total_money == 75
    assert summary.status == 20
    assert summary.group_buying_name == "測試開的團"


@pytest.fixture
def store_with_session(tmp_path):
    store = CartStore(tmp_path / "cart.db")
    summary = store.create_or_get_session(
        group_buy_url="https://order.nidin.shop/gb/menu/1717/ABCDEFGHIJ",
        store_id="1717",
        group_buy_code="ABCDEFGHIJ",
        session_name="測試",
        menu=normalize_menu_from_api(RAW_GROUP_MENU, "1717"),
        delivery_summary=None,
    )
    return store, summary.session_id


def test_resyncing_mirrors_nidin_instead_of_piling_up_duplicates(store_with_session):
    store, session_id = store_with_session
    summary = normalize_official_group_buy_info(REAL_SHAPE, "ABCDEFGHIJ")

    store.merge_official_orders_to_lines(session_id, summary)
    detail = store.merge_official_orders_to_lines(session_id, summary)

    assert detail.line_count == 2
    assert sorted(line.contributor_name for line in detail.lines) == ["小明", "小美"]
    assert sum(line.line_total for line in detail.lines) == 75


def test_follower_who_left_on_nidin_disappears_on_next_sync(store_with_session):
    store, session_id = store_with_session
    store.merge_official_orders_to_lines(session_id, normalize_official_group_buy_info(REAL_SHAPE, "ABCDEFGHIJ"))

    only_first = {**REAL_SHAPE, "group_buy_follower_order_list": REAL_SHAPE["group_buy_follower_order_list"][:1]}
    detail = store.merge_official_orders_to_lines(session_id, normalize_official_group_buy_info(only_first, "ABCDEFGHIJ"))

    assert [line.contributor_name for line in detail.lines] == ["小明"]


def test_lines_added_on_this_site_survive_a_sync(store_with_session):
    store, session_id = store_with_session
    store.add_lines(
        session_id,
        contributor_name="現場加點",
        lines=[{"item_id": "1950362419", "quantity": 1, "selection_option_ids": ["1950362926", "1950362950"]}],
    )

    store.merge_official_orders_to_lines(session_id, normalize_official_group_buy_info(REAL_SHAPE, "ABCDEFGHIJ"))
    detail = store.merge_official_orders_to_lines(session_id, normalize_official_group_buy_info(REAL_SHAPE, "ABCDEFGHIJ"))

    assert sorted(line.contributor_name for line in detail.lines) == ["小明", "小美", "現場加點"]


def test_head_leaders_own_drinks_are_included_so_totals_match_nidin():
    # 實例：跟團 855 + 團長自己一杯 55 = Nidin 的 910
    with_head = {**REAL_SHAPE, "group_buy_head_order_info": {
        **REAL_SHAPE["group_buy_head_order_info"],
        "head_sn": "M0300000003",
        "product": [_product("NI_特上503L", 55, 55, [("加料", "白玉珍珠", 0), ("冰量", "去冰", 0), ("甜度", "二分糖", 0)])],
    }}

    summary = normalize_official_group_buy_info(with_head, "ABCDEFGHIJ")

    head = summary.follower_orders[0]
    assert head.is_head is True
    assert head.follower_name == "團長"
    assert head.order_id == "head:M0300000003"
    assert head.items[0].options == ["白玉珍珠", "去冰", "二分糖"]
    assert summary.total_money == 45 + 30 + 55
    assert summary.total_items == 3
    assert [order.is_head for order in summary.follower_orders[1:]] == [False, False]


def test_synced_lines_get_real_option_ids_stable_ids_and_nidin_order_time(store_with_session):
    store, session_id = store_with_session
    menu = store.get_menu(session_id)
    item = next(it for it in menu.items if it.group_ids)
    group = next(g for g in menu.groups if g.id in item.group_ids and g.options)
    option = group.options[-1]
    payload = {
        "group_buy_head_order_info": {"head_buying_status": 10, "product": []},
        "group_buy_follower_order_list": [{
            "order_id": 7, "contact_name": "小明", "create_time": "2026-09-24 17:05:25", "total_money": None,
            "product": [{
                "product_id": int(item.id), "name": item.name, "amount": 1, "price": item.price,
                "total_money": item.price + option.price,
                "adjusts": [{"name": group.name, "options": [{"name": option.name}, {"name": "菜單上沒有的選項"}]}],
            }],
        }],
    }

    first = store.merge_official_orders_to_lines(session_id, normalize_official_group_buy_info(payload, "ABCDEFGHIJ"))
    second = store.merge_official_orders_to_lines(session_id, normalize_official_group_buy_info(payload, "ABCDEFGHIJ"))

    line = second.lines[0]
    assert line.selections[0].option_id == option.id
    assert line.selections[0].group_id == group.id
    assert line.selections[0].price == option.price
    assert line.selections[1].option_id == "opt"  # 對不到的只留名稱
    assert line.line_id == first.lines[0].line_id
    # Nidin 給台灣時間，存成 UTC 才能跟本站自己寫的時間一起用字串排序
    assert line.created_at == "2026-09-24T09:05:25+00:00"



def test_same_option_name_in_two_groups_resolves_by_group_name():
    from app.cart_store import _resolve_official_selections
    from app.models import MenuGroup, MenuItem, MenuOption, NormalizedMenu

    menu = NormalizedMenu(
        store_id="1",
        items=[MenuItem(id="10", name="紅茶", price=30, group_ids=["size", "cup"])],
        groups=[
            MenuGroup(id="size", name="尺寸", options=[MenuOption(id="size_L", name="L", price=10)]),
            MenuGroup(id="cup", name="杯型", options=[MenuOption(id="cup_L", name="L", price=0)]),
        ],
    )

    by_group = _resolve_official_selections(menu, "10", ["L"], ["杯型"])
    unknown_group = _resolve_official_selections(menu, "10", ["L"], [""])

    assert by_group[0]["option_id"] == "cup_L"
    assert unknown_group[0]["option_id"] == "size_L"  # 沒有群組名稱時退回第一個同名選項
