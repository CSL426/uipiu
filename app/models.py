from typing import Literal

from pydantic import BaseModel, Field


class MenuOption(BaseModel):
    id: str
    name: str
    price: int = 0
    aliases: list[str] = Field(default_factory=list)


class MenuCategory(BaseModel):
    id: str
    name: str
    sort: int = 0


class MenuGroup(BaseModel):
    id: str
    name: str
    min_select: int = 0
    max_select: int = 1
    options: list[MenuOption] = Field(default_factory=list)


class MenuItem(BaseModel):
    id: str
    name: str
    price: int = 0
    group_ids: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    image_url: str | None = None
    category_id: str | None = None
    category_name: str | None = None


class NormalizedMenu(BaseModel):
    store_id: str
    store_name: str = ""
    items: list[MenuItem] = Field(default_factory=list)
    groups: list[MenuGroup] = Field(default_factory=list)
    categories: list[MenuCategory] = Field(default_factory=list)


class CartSessionMenu(NormalizedMenu):
    menu_source: Literal["live", "cache"]
    menu_fetched_at: str | None = None


def validate_menu_snapshot(menu: NormalizedMenu) -> None:
    """Reject incomplete upstream data before replacing a usable snapshot."""
    if not menu.store_id or not menu.items:
        raise ValueError("菜單資料不完整或沒有可供應品項。")
    groups = {group.id: group for group in menu.groups}
    if len(groups) != len(menu.groups):
        raise ValueError("菜單規格識別碼重複。")
    if len({item.id for item in menu.items}) != len(menu.items):
        raise ValueError("菜單品項識別碼重複。")
    for group in menu.groups:
        if (
            not group.id or not group.name
            or group.min_select < 0 or group.max_select < group.min_select
            or len(group.options) < group.min_select
            or len({option.id for option in group.options}) != len(group.options)
        ):
            raise ValueError("菜單規格資料不完整。")
        if any(not option.id or not option.name for option in group.options):
            raise ValueError("菜單選項資料不完整。")
    for item in menu.items:
        if not item.id or not item.name or item.price < 0:
            raise ValueError("菜單品項資料不完整。")
        if any(group_id not in groups for group_id in item.group_ids):
            raise ValueError("菜單缺少品項規格。")


class ExpectedOrderLine(BaseModel):
    item_id: str
    item_name: str | None = None
    selection_option_names: list[str] | None = None
    quantity: int = Field(ge=1)
    selection_option_ids: list[str] = Field(default_factory=list)
    line_total: int = Field(ge=0)


class GroupBuyTarget(BaseModel):
    store_id: str
    group_buy_code: str


class ParsedItem(BaseModel):
    id: str
    name: str
    price: int = 0


class ParsedSelection(BaseModel):
    group_id: str
    group_name: str
    option_id: str
    option_name: str
    price: int = 0
    source_token: str


class ParsedOrder(BaseModel):
    quantity: int = 1
    item: ParsedItem
    selections: list[ParsedSelection] = Field(default_factory=list)
    unmatched_tokens: list[str] = Field(default_factory=list)


class GuestOrderReceipt(BaseModel):
    order_id: int
    order_status: int
    store_id: str
    group_buy_code: str
    guest_user_id: int
    total_money: int
    item_name: str


class StoreDeliverySummary(BaseModel):
    store_id: str
    store_name: str
    delivery_minimum: int | None = None
    delivery_description: str = ""
    order_status_reason: str = ""


class CartLineSelection(BaseModel):
    group_id: str
    group_name: str
    option_id: str
    option_name: str
    price: int = 0


class CartSessionLine(BaseModel):
    line_id: str
    contributor_name: str
    item_id: str
    item_name: str
    category_name: str | None = None
    quantity: int = 1
    selections: list[CartLineSelection] = Field(default_factory=list)
    line_total: int = 0
    created_at: str
    # 對應的 Nidin 跟團單號；None＝只存在本站。團長自己的飲料是 "head:…"
    official_order_id: str | None = None


class CartSessionSummary(BaseModel):
    session_id: str
    group_buy_url: str
    store_id: str
    group_buy_code: str
    session_name: str
    store_name: str
    line_count: int = 0
    total_money: int = 0
    delivery_minimum: int | None = None
    delivery_description: str = ""
    order_status_reason: str = ""
    created_at: str
    updated_at: str


class CartSessionDetail(CartSessionSummary):
    lines: list[CartSessionLine] = Field(default_factory=list)


class SubmitCartSessionOrderRequest(BaseModel):
    guest_name: str
    guest_phone: str = ""


class SubmitCartSessionOrderResponse(BaseModel):
    receipt: GuestOrderReceipt
    cart_session: CartSessionDetail


class OfficialFollowerItem(BaseModel):
    product_id: int | str
    name: str
    amount: int = 1
    price: int = 0
    subtotal: int = 0
    options: list[str] = Field(default_factory=list)
    # 與 options 同順序的所屬規格群組名稱（Nidin adjusts[].name）；對回本站菜單時避免同名選項對錯群組
    option_groups: list[str] = Field(default_factory=list)
    remark: str = ""


class OfficialFollowerOrder(BaseModel):
    order_id: int | str
    follower_name: str
    is_head: bool = False
    # Nidin 的下單時間（ISO，含 +08:00）；沒有就空字串
    created_at: str = ""
    phone: str = ""
    total_money: int = 0
    item_count: int = 0
    items: list[OfficialFollowerItem] = Field(default_factory=list)


class OfficialGroupBuySummary(BaseModel):
    group_buy_code: str
    group_buying_name: str = ""
    brand_name: str = ""
    store_name: str = ""
    store_id: str = ""
    status: int = 0
    head_name: str = ""
    head_user_id: int | str = ""
    total_money: int = 0
    total_items: int = 0
    follower_orders: list[OfficialFollowerOrder] = Field(default_factory=list)


class LeaderAuthRequest(BaseModel):
    token: str
    user_id: str = ""


class LeaderAuthStatus(BaseModel):
    is_authenticated: bool
    user_id: str = ""
    masked_token: str = ""
    auto_relogin: bool = False
    nidin_login_linked: bool = False
    expires_at: float | None = None


class NidinGroupBuy(BaseModel):
    """主揪在 Nidin 當團長的一張團購單（還在揪的購物車或已送出的訂單）。"""

    order_id: str
    store_id: str
    title: str
    group_buying_name: str = ""
    created_at: str = ""
    status_text: str = ""
    submitted: bool
    total_money: int = 0
    follower_count: int = 0
    session_id: str | None = None


class NidinGroupBuyImport(BaseModel):
    session: CartSessionDetail
    sync_error: str | None = None


class LeaderAuthCheck(BaseModel):
    """實際向 Nidin 讀取會員資料後的連線狀態。"""

    nidin_user_id: str
    display_name: str = ""
    expires_at: float | None = None
    relogged_in: bool = False
    auto_relogin: bool = False


class LeaderPasswordLoginRequest(BaseModel):
    account: str = Field(pattern=r"^09\d{8}$")
    password: str = Field(min_length=1, max_length=128)
    remember: bool = True


class AgentParsedItemPreview(BaseModel):
    item_id: str
    item_name: str
    quantity: int = 1
    selection_option_ids: list[str] = Field(default_factory=list)
    selection_option_names: list[str] = Field(default_factory=list)
    unit_price: int = 0
    line_total: int = 0


class AgentOrderSessionRequest(BaseModel):
    text: str
    contributor_name: str = ""
    dry_run: bool = False


class AgentOrderSessionResponse(BaseModel):
    action: str  # "add_to_cart" | "reply" | "preview"
    reply_message: str
    items_added: int = 0
    parsed_items: list[AgentParsedItemPreview] = Field(default_factory=list)
    total_price: int = 0
    contributor_name: str = ""
    cart_session: CartSessionDetail | None = None


class PairingInitResponse(BaseModel):
    flow_id: str
    state: str
    expires_at: float
    user_summary: str
    credential_revision: int


class PairingLeaseRequest(BaseModel):
    state: str = Field(min_length=16, max_length=256)


class PairingLeaseResponse(BaseModel):
    status: str
    lease_expires_at: float


class PairingConfirmRequest(BaseModel):
    state: str = Field(min_length=16, max_length=256)
    token: str = Field(min_length=1, max_length=16384)
    user_id: str = Field(default="", max_length=128)


class PairingCancelRequest(BaseModel):
    state: str = Field(min_length=16, max_length=256)

