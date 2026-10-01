from app.nidin import extract_store_id, normalize_menu

from tests.fixtures import RAW_MENU


def test_extract_store_id_from_nidin_menu_url():
    assert extract_store_id("https://order.nidin.shop/menu/9736") == "9736"
    assert extract_store_id("https://order.nidin.shop/menu/9736?utm_source=line") == "9736"
    assert extract_store_id("9736") == "9736"


def test_normalize_menu_from_items_and_groups_shape():
    menu = normalize_menu(RAW_MENU)

    assert menu.store_id == "9736"
    assert menu.store_name == "示範飲料店"
    assert [item.name for item in menu.items] == ["珍珠奶茶", "紅茶"]
    assert menu.items[0].group_ids == ["sweet", "ice", "topping"]
    assert [group.name for group in menu.groups] == ["甜度", "冰塊", "加料"]
    assert [option.name for option in menu.groups[2].options] == ["珍珠", "布丁"]
