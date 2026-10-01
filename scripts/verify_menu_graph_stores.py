"""Verify local menu snapshots; missing data is an incomplete run."""

import json
from pathlib import Path

from app.menu_graph import MenuGraph
from app.nidin import normalize_menu_from_api


TEST_CASES = [
    {
        "file": "data/menus/50嵐_485.json",
        "store_id": "485",
        "order": "小明要一杯四季春青茶 微糖微冰",
        "expected_item": "大●四季春青茶",
        "expected_action": "add_to_cart",
    },
    {
        "file": "data/menus/50嵐_485.json",
        "store_id": "485",
        "order": "小華要一杯中杯阿薩姆紅茶",
        "expected_action": "reply",  # missing required temp/sweet
    },
    {
        "file": "data/menus/迷客夏_3.json",
        "store_id": "3",
        "order": "大明要一杯伯爵紅茶拿鐵 微糖微冰",
        "expected_item": "伯爵紅茶拿鐵",
        "expected_action": "add_to_cart",
    },
    {
        "file": "data/menus/便當研究所_30140.json",
        "store_id": "30140",
        "order": "老王要一份炙燒豚弁當 肉加量",
        "expected_item": "炙燒豚弁當",
        "expected_action": "add_to_cart",
    },
    {
        "file": "data/menus/九湯屋日本拉麵_21702.json",
        "store_id": "21702",
        "order": "鳴人要一碗東京豚骨拉麵 大碗 細直麵 溏心蛋",
        "expected_item": "東京豚骨拉麵",
        "expected_action": "add_to_cart",
    },
    {
        "file": "data/menus/莫克漢堡_9508.json",
        "store_id": "9508",
        "order": "漢堡王要一個柴燒慢烤牛胸肉漢堡",
        "expected_item": "柴燒慢烤牛胸肉漢堡",
        "expected_action": "add_to_cart",
    },
]


def verify_orders(test_cases, root: Path) -> int:
    passed = failed = skipped = 0
    for case in test_cases:
        path = root / case["file"]
        if not path.is_file():
            print(f"SKIP: {case['file']} (not found)")
            skipped += 1
            continue

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            menu = normalize_menu_from_api(
                raw.get("raw_menu", raw), case["store_id"]
            )
            graph = MenuGraph.build_from_normalized_menu(menu)
            result = graph.resolve_order_fast(case["order"])
            if result is None:
                raise ValueError("Order could not be resolved")
            if result.action != case["expected_action"]:
                raise ValueError(
                    f"Expected {case['expected_action']}, got {result.action}"
                )
            if case["expected_action"] == "add_to_cart":
                if not result.items:
                    raise ValueError("No order items returned")
                if result.items[0].item_name != case["expected_item"]:
                    raise ValueError(
                        f"Expected {case['expected_item']}, "
                        f"got {result.items[0].item_name}"
                    )
        except Exception as exc:
            # Finish the batch so one broken snapshot cannot hide other cases.
            print(f"FAIL: {case['file']} ({type(exc).__name__})")
            failed += 1
            continue

        print(f"PASS: [{menu.store_name or case['store_id']}] {case['order']}")
        passed += 1

    print(f"Summary: passed={passed}, failed={failed}, skipped={skipped}")
    if not passed or failed or skipped:
        print("Verification incomplete or failed.")
        return 1
    print("All requested cases passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(verify_orders(TEST_CASES, Path(__file__).resolve().parent.parent))
