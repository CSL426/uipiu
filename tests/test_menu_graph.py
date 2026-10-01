import pytest
from app.menu_graph import MenuGraph
from app.nidin import normalize_menu_from_api
from tests.fixtures import RAW_GROUP_MENU


@pytest.fixture
def sample_menu():
    return normalize_menu_from_api(RAW_GROUP_MENU, "1717")


def test_menu_graph_build(sample_menu):
    graph = MenuGraph.build_from_normalized_menu(sample_menu)
    assert len(graph.items) == 3
    item_names = [it.name for it in graph.items]
    assert "中●阿薩姆紅茶" in item_names
    assert "大●奶茶" in item_names


def test_menu_graph_fast_resolve_success(sample_menu):
    graph = MenuGraph.build_from_normalized_menu(sample_menu)
    # RAW_GROUP_MENU 中 中●阿薩姆紅茶 支援 溫度(正常冰/熱) 與 甜度(正常甜/半糖)
    res = graph.resolve_order_fast("小明要一杯中杯阿薩姆紅茶 半糖正常冰", default_contributor="訪客")
    assert res is not None
    assert res.action == "add_to_cart"
    assert len(res.items) == 1
    assert res.items[0].customer_name == "小明"
    assert res.items[0].quantity == 1
    assert res.items[0].item_name == "中●阿薩姆紅茶"
    # 驗證有匹配到微糖或去冰
    assert len(res.items[0].selection_option_ids) >= 1


def test_menu_graph_fast_resolve_missing_required(sample_menu):
    graph = MenuGraph.build_from_normalized_menu(sample_menu)
    # 缺少必選的冰塊與甜度
    res = graph.resolve_order_fast("小華要一杯大杯奶茶", default_contributor="小華")
    assert res is not None
    assert res.action == "reply"
    assert "需要選擇" in res.reply_message


def test_menu_graph_extract_narrow_subgraph(sample_menu):
    graph = MenuGraph.build_from_normalized_menu(sample_menu)
    prompt = graph.extract_narrow_subgraph_prompt("奶茶", max_items=5)
    assert "大●奶茶" in prompt
    assert len(prompt) < 2000
