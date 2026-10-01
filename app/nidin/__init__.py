"""Stable import entry point for the Nidin integration.

HTTP orchestration, response normalization, and cart construction live in
separate modules. Re-exported classes retain identity for existing patches.
"""

from app.nidin.builder import (
    MenuChangedError,
    MenuDataError,
    build_expected_cart_lines,
    build_group_buy_cart_payload,
    build_parsed_orders_from_cart_lines,
    validate_cart_lines,
    validated_order_menu,
)
from app.nidin.client import (
    BROWSER_USER_AGENT,
    NIDIN_API_BASE_URL,
    NidinClient,
)
from app.nidin.normalizer import (
    DECORATIVE_EDGE_PATTERN,
    GROUP_BUY_PATTERN,
    MENU_TEXT_PATTERN,
    SIZE_PREFIX_PATTERN,
    STORE_ID_PATTERN,
    TAIWAN_MOBILE_PATTERN,
    _build_valid_options_per_product,
    _compact_menu_text,
    _decode_list,
    _decode_map,
    _dedupe_preserve_order,
    _derive_item_aliases,
    _derive_option_aliases,
    _extract_option_pairs,
    _extract_product_image_urls,
    _menu_payload,
    _normalize_store_delivery_summary,
    _resolve_guest_phone,
    _resolve_product_price,
    extract_group_buy_target,
    extract_store_id,
    normalize_menu,
    normalize_menu_from_api,
    normalize_official_group_buy_info,
)


__all__ = [
    "BROWSER_USER_AGENT",
    "DECORATIVE_EDGE_PATTERN",
    "GROUP_BUY_PATTERN",
    "MENU_TEXT_PATTERN",
    "MenuChangedError",
    "MenuDataError",
    "NIDIN_API_BASE_URL",
    "NidinClient",
    "SIZE_PREFIX_PATTERN",
    "STORE_ID_PATTERN",
    "TAIWAN_MOBILE_PATTERN",
    "build_expected_cart_lines",
    "build_group_buy_cart_payload",
    "build_parsed_orders_from_cart_lines",
    "extract_group_buy_target",
    "extract_store_id",
    "normalize_menu",
    "normalize_menu_from_api",
    "normalize_official_group_buy_info",
    "validate_cart_lines",
    "validated_order_menu",
]
