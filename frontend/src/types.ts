export interface LeaderAuthStatus {
  is_authenticated: boolean;
  user_id: string;
  masked_token: string;
  auto_relogin?: boolean;
  nidin_login_linked?: boolean;
  expires_at?: number | null;
}

export interface LeaderAuthCheck {
  nidin_user_id: string;
  display_name: string;
  expires_at: number | null;
  relogged_in: boolean;
  auto_relogin: boolean;
}

export interface AgentParsedItemPreview {
  item_id: string;
  item_name: string;
  quantity: number;
  selection_option_ids: string[];
  selection_option_names: string[];
  unit_price: number;
  line_total: number;
}

export interface AgentOrderResponse {
  action: string;
  reply_message: string;
  items_added: number;
  parsed_items?: AgentParsedItemPreview[];
  total_price?: number;
  contributor_name?: string;
  cart_session?: CartSessionDetail;
}

export interface ChatMessage {
  id: string;
  sender: "user" | "bot";
  text: string;
  createdAt: string;
  preview?: {
    parsed_items: AgentParsedItemPreview[];
    total_price: number;
    contributor_name: string;
    confirmed?: boolean;
    /** 使用者按了取消：卡片鎖住，不能再送出 */
    cancelled?: boolean;
    rawText: string;
  };
  quickReplies?: string[];
}

export interface CustomSelectOption {
  value: string;
  label: string;
  sublabel?: string;
}

export interface MenuOption {
  id: string;
  name: string;
  price: number;
  aliases: string[];
}

export interface MenuGroup {
  id: string;
  name: string;
  min_select: number;
  max_select: number;
  options: MenuOption[];
}

export interface MenuCategory {
  id: string;
  name: string;
  sort: number;
}

export interface MenuItem {
  id: string;
  name: string;
  price: number;
  group_ids: string[];
  aliases: string[];
  image_url?: string | null;
  category_id?: string | null;
  category_name?: string | null;
}

export interface NormalizedMenu {
  store_id: string;
  store_name: string;
  items: MenuItem[];
  groups: MenuGroup[];
  categories: MenuCategory[];
  menu_source?: "live" | "cache";
  menu_fetched_at?: string | null;
}

export interface CartSessionSummary {
  session_id: string;
  group_buy_url: string;
  store_id: string;
  group_buy_code: string;
  session_name: string;
  store_name: string;
  line_count: number;
  total_money: number;
  delivery_minimum: number | null;
  delivery_description: string;
  order_status_reason: string;
  created_at: string;
  updated_at: string;
}

export interface SharedCartSelection {
  group_id: string;
  group_name: string;
  option_id: string;
  option_name: string;
  price: number;
}

export interface SharedCartLine {
  line_id: string;
  contributor_name: string;
  item_id: string;
  item_name: string;
  category_name?: string | null;
  quantity: number;
  selections: SharedCartSelection[];
  line_total: number;
  created_at: string;
  /** 對應的 Nidin 跟團單號；團長自己的飲料是 "head:…"；null＝只存在本站 */
  official_order_id?: string | null;
}

export interface CartSessionDetail extends CartSessionSummary {
  lines: SharedCartLine[];
}

export interface SelectedOption {
  groupId: string;
  groupName: string;
  optionId: string;
  optionName: string;
  price: number;
}

export interface CartLine {
  id: string;
  item: MenuItem;
  quantity: number;
  selections: SelectedOption[];
}

export interface GuestOrderReceipt {
  order_id: number;
  order_status: number;
  store_id: string;
  group_buy_code: string;
  guest_user_id: number;
  total_money: number;
  item_name: string;
}

export interface NidinGroupBuy {
  order_id: string;
  store_id: string;
  title: string;
  group_buying_name: string;
  created_at: string;
  status_text: string;
  submitted: boolean;
  total_money: number;
  follower_count: number;
  session_id: string | null;
}

export interface NidinGroupBuyImport {
  session: CartSessionDetail;
  sync_error: string | null;
}
