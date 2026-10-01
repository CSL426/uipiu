from __future__ import annotations

import pytest
from app.cart_store import CartStore
from app.models import (
    CartSessionLine,
    MenuGroup,
    MenuItem,
    MenuOption,
    NormalizedMenu,
)
from app.order_hooks import (
    CoreDrinkDimensionHook,
    HookContext,
    HookResult,
    MaxQuantityLimitHook,
    OrderAuditLoggingHook,
    OrderHook,
    OrderHookPipeline,
    is_core_drink_dimension,
)


@pytest.fixture
def drink_menu():
    ice_group = MenuGroup(
        id="g_ice",
        name="溫度",
        min_select=0,  # 模擬某些店家 API 預設為 0
        max_select=1,
        options=[
            MenuOption(id="opt_ice_normal", name="正常冰", price=0),
            MenuOption(id="opt_ice_micro", name="微冰", price=0),
            MenuOption(id="opt_ice_none", name="去冰", price=0),
        ],
    )
    sweet_group = MenuGroup(
        id="g_sweet",
        name="甜度",
        min_select=0,  # 模擬某些店家 API 預設為 0
        max_select=1,
        options=[
            MenuOption(id="opt_sweet_normal", name="正常糖", price=0),
            MenuOption(id="opt_sweet_half", name="半糖", price=0),
            MenuOption(id="opt_sweet_none", name="無糖", price=0),
        ],
    )
    size_group = MenuGroup(
        id="g_size",
        name="尺寸",
        min_select=0,
        max_select=1,
        options=[
            MenuOption(id="opt_size_m", name="中杯", price=0),
            MenuOption(id="opt_size_l", name="大杯", price=10),
        ],
    )
    fixed_ice_group = MenuGroup(
        id="g_fixed_ice",
        name="溫度",
        min_select=0,
        max_select=1,
        options=[
            MenuOption(id="opt_ice_fixed", name="固定冰量", price=0),
        ],
    )
    single_size_group = MenuGroup(
        id="g_single_size",
        name="尺寸",
        min_select=0,
        max_select=1,
        options=[
            MenuOption(id="opt_size_fixed_l", name="L", price=0),
        ],
    )
    topping_group = MenuGroup(
        id="g_topping",
        name="加料",
        min_select=0,
        max_select=2,
        options=[
            MenuOption(id="opt_pearl", name="波霸珍珠", price=10),
            MenuOption(id="opt_jelly", name="烤糖蕎麥凍", price=20),
        ],
    )

    item_multi = MenuItem(
        id="item_tea",
        name="極上綠茶",
        price=35,
        group_ids=["g_ice", "g_sweet", "g_size", "g_topping"],
    )
    item_fixed = MenuItem(
        id="item_smoothie",
        name="芒果冰沙",
        price=65,
        group_ids=["g_fixed_ice", "g_sweet", "g_single_size"],
    )

    groups = [ice_group, sweet_group, size_group, fixed_ice_group, single_size_group, topping_group]
    return NormalizedMenu(
        store_id="test_store",
        store_name="測試手搖飲",
        categories=[],
        groups=groups,
        items=[item_multi, item_fixed],
    )


def test_is_core_drink_dimension():
    # 溫度
    assert is_core_drink_dimension("溫度") == "temperature"
    assert is_core_drink_dimension("飲料溫度") == "temperature"
    assert is_core_drink_dimension("冰量") == "temperature"
    assert is_core_drink_dimension("冷熱") == "temperature"

    # 甜度
    assert is_core_drink_dimension("甜度") == "sweetness"
    assert is_core_drink_dimension("糖度選擇") == "sweetness"
    assert is_core_drink_dimension("糖量") == "sweetness"

    # 尺寸
    assert is_core_drink_dimension("尺寸") == "size"
    assert is_core_drink_dimension("大小") == "size"
    assert is_core_drink_dimension("杯型") == "size"

    # 非核心手搖維度
    assert is_core_drink_dimension("加料") is None
    assert is_core_drink_dimension("加價購") is None
    assert is_core_drink_dimension("主餐配菜") is None


def test_core_dimension_hook_enforces_missing_dimensions(drink_menu):
    hook = CoreDrinkDimensionHook()
    group_map = {g.id: g for g in drink_menu.groups}
    item = drink_menu.items[0]  # 極上綠茶

    # 1. 什麼都沒選：應該 reject 並要求選擇第一個未滿足的核心維度（溫度）
    ctx = HookContext(
        item=item,
        quantity=1,
        selection_option_ids=[],
        contributor_name="小美",
        group_map=group_map,
        menu=drink_menu,
    )
    res = hook.before_validate_item(ctx)
    assert not res.is_valid
    assert res.action == "reply"
    assert "小美" in res.message
    assert "極上綠茶" in res.message
    assert "溫度" in res.message

    # 2. 選了溫度與甜度，但漏選尺寸
    ctx.selection_option_ids = ["opt_ice_micro", "opt_sweet_half"]
    res = hook.before_validate_item(ctx)
    assert not res.is_valid
    assert res.action == "reply"
    assert "尺寸" in res.message

    # 3. 溫度、甜度、尺寸都選齊，不選加料也可以（因為加料非核心維度且 min_select=0）
    ctx.selection_option_ids = ["opt_ice_micro", "opt_sweet_half", "opt_size_l"]
    res = hook.before_validate_item(ctx)
    assert res.is_valid
    assert res.action == "pass"


def test_core_dimension_hook_auto_attaches_fixed_options(drink_menu):
    hook = CoreDrinkDimensionHook()
    group_map = {g.id: g for g in drink_menu.groups}
    item = drink_menu.items[1]  # 芒果冰沙: g_fixed_ice (只有固定冰量), g_sweet, g_single_size (只有 L)

    # 顧客只選了甜度，沒有選固定冰量和單一尺寸
    ctx = HookContext(
        item=item,
        quantity=1,
        selection_option_ids=["opt_sweet_none"],
        contributor_name="阿明",
        group_map=group_map,
        menu=drink_menu,
    )
    res = hook.before_validate_item(ctx)
    assert res.is_valid
    assert res.modified_option_ids is not None
    # 自動補齊了固定冰量與單一尺寸 L
    assert "opt_ice_fixed" in res.modified_option_ids
    assert "opt_size_fixed_l" in res.modified_option_ids
    assert "opt_sweet_none" in res.modified_option_ids


def test_max_quantity_limit_hook():
    hook = MaxQuantityLimitHook(max_quantity=30)
    group_map = {}
    item = MenuItem(id="i1", name="珍珠奶茶", price=50, group_ids=[])

    # 數量 0 或負數
    ctx_zero = HookContext(
        item=item, quantity=0, selection_option_ids=[], contributor_name="測試", group_map=group_map
    )
    res = hook.before_validate_item(ctx_zero)
    assert not res.is_valid
    assert "至少要 1 份" in res.message

    # 數量超過上限 30
    ctx_over = HookContext(
        item=item, quantity=99, selection_option_ids=[], contributor_name="測試", group_map=group_map
    )
    res = hook.before_validate_item(ctx_over)
    assert not res.is_valid
    assert "超過上限" in res.message

    # 數量正常
    ctx_valid = HookContext(
        item=item, quantity=5, selection_option_ids=[], contributor_name="測試", group_map=group_map
    )
    res = hook.before_validate_item(ctx_valid)
    assert res.is_valid


def test_order_audit_logging_hook():
    hook = OrderAuditLoggingHook()
    item = MenuItem(id="i1", name="珍珠奶茶", price=50, group_ids=[])
    ctx = HookContext(item=item, quantity=2, selection_option_ids=[], contributor_name="小華", group_map={})
    line = CartSessionLine(
        line_id="line_1",
        session_id="sess_1",
        contributor_name="小華",
        item_id="i1",
        item_name="珍珠奶茶",
        quantity=2,
        line_total=100,
        created_at="2026-09-17T12:00:00Z",
    )
    # 呼叫 post hook 不應拋出異常
    hook.after_item_added(ctx, line)


def test_custom_hook_in_pipeline():
    class VIPDiscountHook(OrderHook):
        name = "VIPDiscountHook"
        priority = 5

        def before_validate_item(self, context: HookContext) -> HookResult:
            if context.contributor_name.startswith("VIP"):
                # 附帶特別標記
                context.extra["vip"] = True
            return HookResult.ok()

    pipeline = OrderHookPipeline(hooks=[VIPDiscountHook()])
    item = MenuItem(id="i1", name="紅茶", price=25, group_ids=[])
    ctx = HookContext(item=item, quantity=1, selection_option_ids=[], contributor_name="VIP-王董", group_map={})

    res = pipeline.run_pre_validation(ctx)
    assert res.is_valid
    assert ctx.extra.get("vip") is True


def test_pipeline_integration_with_cart_store(tmp_path, drink_menu):
    db_path = tmp_path / "test_cart.db"
    store = CartStore(db_path=db_path)

    session = store.create_or_get_session(
        group_buy_url="https://order.nidin.shop/gb/menu/test_store/TESTCODE",
        store_id=drink_menu.store_id,
        group_buy_code="TESTCODE",
        session_name="測試團購",
        menu=drink_menu,
        delivery_summary=None,
    )

    # 1. 嘗試加入極上綠茶，未選溫度甜度尺寸 -> 應拋出 ValueError 包含提示話術
    with pytest.raises(ValueError, match="需要選擇「溫度」"):
        store.add_lines(
            session_id=session.session_id,
            contributor_name="小豪",
            lines=[{"item_id": "item_tea", "quantity": 1, "selection_option_ids": []}],
        )

    # 2. 嘗試單次加入 100 杯 -> 應拋出 ValueError 超過上限
    with pytest.raises(ValueError, match="超過上限"):
        store.add_lines(
            session_id=session.session_id,
            contributor_name="小豪",
            lines=[{
                "item_id": "item_tea",
                "quantity": 100,
                "selection_option_ids": ["opt_ice_micro", "opt_sweet_half", "opt_size_l"],
            }],
        )

    # 3. 芒果冰沙只選甜度，固定冰量與固定尺寸自動補齊並成功入購物車
    detail = store.add_lines(
        session_id=session.session_id,
        contributor_name="小豪",
        lines=[{
            "item_id": "item_smoothie",
            "quantity": 2,
            "selection_option_ids": ["opt_sweet_none"],
        }],
    )
    assert len(detail.lines) == 1
    line = detail.lines[0]
    assert line.quantity == 2
    attached_opt_names = [s.option_name for s in line.selections]
    assert "固定冰量" in attached_opt_names
    assert "無糖" in attached_opt_names
    assert "L" in attached_opt_names


@pytest.mark.parametrize("option_names", [[], ["固定冰量", "冰量固定"]])
def test_core_dimension_hook_keeps_fixed_groups_optional(option_names):
    group = MenuGroup(
        id="ice",
        name="溫度",
        min_select=0,
        max_select=1,
        options=[
            MenuOption(id=str(index), name=name, price=0)
            for index, name in enumerate(option_names)
        ],
    )
    context = HookContext(
        item=MenuItem(id="tea", name="冰茶", price=30, group_ids=["ice"]),
        quantity=1,
        selection_option_ids=[],
        contributor_name="小明",
        group_map={"ice": group},
    )

    result = CoreDrinkDimensionHook().before_validate_item(context)

    assert result.is_valid
    assert result.modified_option_ids is None


def test_core_dimension_hook_does_not_count_duplicate_selections(drink_menu):
    group = drink_menu.groups[0].model_copy(update={"min_select": 2})
    context = HookContext(
        item=drink_menu.items[0],
        quantity=1,
        selection_option_ids=["opt_ice_micro", "opt_ice_micro"],
        contributor_name="小明",
        group_map={group.id: group},
    )

    result = CoreDrinkDimensionHook().before_validate_item(context)

    assert not result.is_valid
    assert result.action == "reply"
    assert "溫度" in result.message
