"""Order Validation and Business Rule Hook Pipeline.

Provides pluggable lifecycle hooks for validating, enriching, and auditing orders
before and after they are added to cart sessions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.models import (
    CartSessionLine,
    MenuGroup,
    MenuItem,
    NormalizedMenu,
)

logger = logging.getLogger(__name__)

# 核心手搖維度關鍵字
CORE_DIMENSION_KEYWORDS = {
    "temperature": ["溫度", "冰塊", "冰量", "冷熱", "飲料溫度", "冰度"],
    "sweetness": ["甜度", "糖度", "甜度選擇", "糖量"],
    "size": ["尺寸", "大小", "份量", "杯型", "份量選擇", "容量"],
}


def is_core_drink_dimension(group_name: str) -> str | None:
    """Check if group_name matches any core drink dimension (temperature, sweetness, size)."""
    lower = group_name.lower()
    for dtype, keywords in CORE_DIMENSION_KEYWORDS.items():
        if any(k in lower for k in keywords):
            return dtype
    return None


@dataclass
class HookContext:
    """Context object passed to each hook in the pipeline."""

    item: MenuItem
    quantity: int
    selection_option_ids: list[str]
    contributor_name: str
    group_map: dict[str, MenuGroup]
    menu: NormalizedMenu | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookResult:
    """Result returned by pre-validation hooks."""

    is_valid: bool = True
    modified_option_ids: list[str] | None = None
    modified_quantity: int | None = None
    message: str = ""
    action: str = "pass"  # "pass" | "reply" | "error"

    @classmethod
    def ok(
        cls,
        modified_option_ids: list[str] | None = None,
        modified_quantity: int | None = None,
    ) -> HookResult:
        return cls(
            is_valid=True,
            modified_option_ids=modified_option_ids,
            modified_quantity=modified_quantity,
            action="pass",
        )

    @classmethod
    def reject(cls, message: str, action: str = "error") -> HookResult:
        return cls(is_valid=False, message=message, action=action)


class OrderHook:
    """Base class for order lifecycle hooks."""

    name: str = "BaseOrderHook"
    priority: int = 100  # Lower numbers execute first

    def before_validate_item(self, context: HookContext) -> HookResult:
        """Run before an item is validated and added to cart.

        Can validate constraints, auto-attach fixed options, or reject with a message.
        """
        return HookResult.ok()

    def after_item_added(self, context: HookContext, created_line: CartSessionLine) -> None:
        """Run after an item is successfully committed to the cart."""
        pass


class CoreDrinkDimensionHook(OrderHook):
    """Enforce mandatory choice for core beverage dimensions (ice, sweetness, size).

    - Auto-attaches fixed single options (e.g. only '固定冰量' or single size 'M').
    - Promotes effective_min to 1 when multiple options exist and are not fixed.
    """

    name: str = "CoreDrinkDimensionHook"
    priority: int = 10

    def before_validate_item(self, context: HookContext) -> HookResult:
        item = context.item
        current_option_ids = set(context.selection_option_ids)
        modified_option_ids = list(context.selection_option_ids)

        for group_id in item.group_ids:
            group = context.group_map.get(group_id)
            if group is None or not is_core_drink_dimension(group.name):
                continue

            # 單一核心規格自動帶入，避免要求使用者確認唯一選擇。
            if len(group.options) == 1:
                option_id = group.options[0].id
                if option_id not in current_option_ids:
                    modified_option_ids.append(option_id)
                    current_option_ids.add(option_id)
                continue

            if all("固定" in option.name for option in group.options):
                continue

            group_option_ids = {option.id for option in group.options}
            selected_count = len(current_option_ids & group_option_ids)
            if selected_count < max(group.min_select, 1):
                examples = ", ".join(option.name for option in group.options[:4])
                return HookResult.reject(
                    message=(
                        f"{context.contributor_name}您好，您點的「{item.name}」"
                        f"需要選擇「{group.name}」，請問想選哪一種呢？"
                        f"（例如：{examples}）"
                    ),
                    action="reply",
                )

        if len(modified_option_ids) == len(context.selection_option_ids):
            return HookResult.ok()
        return HookResult.ok(modified_option_ids=modified_option_ids)


class MaxQuantityLimitHook(OrderHook):
    """Prevent typos and abusive single-line quantities (e.g. accidental +100 cups)."""

    name: str = "MaxQuantityLimitHook"
    priority: int = 20

    def __init__(self, max_quantity: int = 50):
        self.max_quantity = max_quantity

    def before_validate_item(self, context: HookContext) -> HookResult:
        if context.quantity < 1:
            return HookResult.reject("點餐數量至少要 1 份", action="error")
        if context.quantity > self.max_quantity:
            return HookResult.reject(
                f"單一品項點餐數量超過上限（單次上限 {self.max_quantity} 份，目前為 {context.quantity} 份），請確認是否打錯數字。",
                action="error",
            )
        return HookResult.ok()


class OrderAuditLoggingHook(OrderHook):
    """Audit log every order addition for traceability."""

    name: str = "OrderAuditLoggingHook"
    priority: int = 90

    def after_item_added(self, context: HookContext, created_line: CartSessionLine) -> None:
        opts = ", ".join(s.option_name for s in created_line.selections) or "無特殊選項"
        logger.info(
            f"[OrderHook Audit] Line created: {created_line.line_id} | "
            f"Contributor: {created_line.contributor_name} | "
            f"Item: {created_line.item_name} x {created_line.quantity} (${created_line.line_total}) | "
            f"Options: {opts}"
        )


class OrderHookPipeline:
    """Central pipeline for managing and running order lifecycle hooks."""

    def __init__(self, hooks: list[OrderHook] | None = None):
        self._hooks: list[OrderHook] = []
        for hook in hooks or [
            CoreDrinkDimensionHook(),
            MaxQuantityLimitHook(max_quantity=50),
            OrderAuditLoggingHook(),
        ]:
            self.register(hook)

    def register(self, hook: OrderHook) -> None:
        """Register a new hook and sort by priority."""
        self._hooks.append(hook)
        self._hooks.sort(key=lambda h: h.priority)
        logger.debug(f"Registered order hook: {hook.name} (priority {hook.priority})")

    def unregister(self, hook_name: str) -> None:
        """Remove a hook by name."""
        self._hooks = [h for h in self._hooks if h.name != hook_name]

    def get_hook(self, hook_name: str) -> OrderHook | None:
        for h in self._hooks:
            if h.name == hook_name:
                return h
        return None

    def run_pre_validation(self, context: HookContext) -> HookResult:
        """Run all registered pre-validation hooks in priority order."""
        current_option_ids = list(context.selection_option_ids)
        current_quantity = context.quantity

        for hook in self._hooks:
            res = hook.before_validate_item(context)
            if not res.is_valid:
                return res

            if res.modified_option_ids is not None:
                current_option_ids = res.modified_option_ids
                context.selection_option_ids = current_option_ids

            if res.modified_quantity is not None:
                current_quantity = res.modified_quantity
                context.quantity = current_quantity

        return HookResult.ok(modified_option_ids=current_option_ids, modified_quantity=current_quantity)

    def run_post_hooks(self, context: HookContext, created_line: CartSessionLine) -> None:
        """Run all registered post-commit hooks."""
        for hook in self._hooks:
            try:
                hook.after_item_added(context, created_line)
            except Exception as exc:
                logger.error(f"Error in hook {hook.name}.after_item_added: {exc}")


# Global default hook pipeline instance
default_order_pipeline = OrderHookPipeline()
