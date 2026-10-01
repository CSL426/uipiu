from app.nidin import normalize_menu
from app.parser import parse_order_lines, parse_order_text

from tests.fixtures import RAW_MENU


def test_parse_order_text_with_quantity_and_options():
    menu = normalize_menu(RAW_MENU)

    parsed = parse_order_text("+2 珍珠奶茶 去冰 半糖 珍珠", menu)

    assert parsed.quantity == 2
    assert parsed.item.name == "珍珠奶茶"
    assert [selection.option_name for selection in parsed.selections] == ["半糖", "去冰", "珍珠"]
    assert parsed.unmatched_tokens == []


def test_parse_order_text_supports_item_alias():
    menu = normalize_menu(RAW_MENU)

    parsed = parse_order_text("+1 珍奶 少糖 微冰", menu)

    assert parsed.quantity == 1
    assert parsed.item.name == "珍珠奶茶"
    assert [selection.option_name for selection in parsed.selections] == ["半糖", "微冰"]


def test_parse_order_text_surfaces_unmatched_tokens():
    menu = normalize_menu(RAW_MENU)

    parsed = parse_order_text("+1 紅茶 去冰 熱熱的", menu)

    assert parsed.item.name == "紅茶"
    assert parsed.unmatched_tokens == ["熱熱的"]


def test_parse_order_lines_support_multiple_items_and_toppings():
    menu = normalize_menu(RAW_MENU)

    parsed_orders = parse_order_lines(
        "+1 珍珠奶茶 去冰 半糖 珍珠\n+2 紅茶 無糖 去冰",
        menu,
    )

    assert len(parsed_orders) == 2
    assert parsed_orders[0].item.name == "珍珠奶茶"
    assert [selection.option_name for selection in parsed_orders[0].selections] == ["半糖", "去冰", "珍珠"]
    assert parsed_orders[1].item.name == "紅茶"
    assert parsed_orders[1].quantity == 2
