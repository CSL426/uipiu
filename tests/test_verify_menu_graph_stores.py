import json

import pytest

from scripts.verify_menu_graph_stores import verify_orders
from tests.fixtures import RAW_GROUP_MENU


@pytest.fixture
def order_case(tmp_path):
    (tmp_path / "menu.json").write_text(
        json.dumps({"raw_menu": RAW_GROUP_MENU}), encoding="utf-8"
    )
    return {
        "file": "menu.json",
        "store_id": "1717",
        "order": "小明要一杯中杯阿薩姆紅茶 半糖正常冰",
        "expected_action": "add_to_cart",
        "expected_item": "中●阿薩姆紅茶",
    }


def test_all_cases_pass(tmp_path, order_case, capsys):
    assert verify_orders([order_case], tmp_path) == 0
    assert "passed=1, failed=0, skipped=0" in capsys.readouterr().out


def test_missing_snapshot_is_incomplete(tmp_path, order_case, capsys):
    missing = {**order_case, "file": "missing.json"}
    assert verify_orders([missing, order_case], tmp_path) == 1
    output = capsys.readouterr().out
    assert "passed=1, failed=0, skipped=1" in output
    assert "All requested cases passed" not in output


def test_no_cases_is_not_success(tmp_path, capsys):
    assert verify_orders([], tmp_path) == 1
    assert "passed=0, failed=0, skipped=0" in capsys.readouterr().out


def test_failure_does_not_stop_remaining_cases(tmp_path, order_case, capsys):
    wrong = {**order_case, "expected_item": "不存在的飲料"}
    assert verify_orders([wrong, order_case], tmp_path) == 1
    assert "passed=1, failed=1, skipped=0" in capsys.readouterr().out


def test_invalid_snapshot_is_failure(tmp_path, order_case, capsys):
    (tmp_path / "menu.json").write_text("{", encoding="utf-8")
    assert verify_orders([order_case], tmp_path) == 1
    assert "passed=0, failed=1, skipped=0" in capsys.readouterr().out
