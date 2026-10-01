/**
 * 點單即送 Nidin 的前端介面。
 *
 * 送出後 Nidin 上的跟團單就是真相；本站畫面靠 refresh 撈回來。取消只能由團長在 Nidin 刪單，
 * 後端代刪時要驗證「是不是點這筆的人」——憑證是送單時後端發的 cancel_token，只存在點單人的瀏覽器。
 */
import type { CartSessionDetail, GuestOrderReceipt } from "../types";

export interface LiveOrderLine {
  item_id: string;
  quantity: number;
  selection_option_ids: string[];
}

export interface SubmitOrderResult {
  receipt: GuestOrderReceipt | null;
  official_order_id: string;
  cancel_token: string;
  session: CartSessionDetail;
  already_submitted: boolean;
}

export interface RefreshResult {
  session: CartSessionDetail;
  synced: boolean;
  message: string | null;
}

const NAME_KEY = "nidin_contributor_name";
const PHONE_KEY = "nidin_contributor_phone";
const ORDERS_KEY = "nidin_my_orders";

type MyOrders = Record<string, Record<string, string>>; // sessionId -> officialOrderId -> cancelToken

// localStorage 被封鎖（無痕、隱私設定）時的備援：至少這個分頁還能取消剛送出的單
const memoryOrders: MyOrders = {};

const UNCERTAIN_MESSAGE =
  "送出後沒有收到確定結果，這筆可能已經成立。請等一分鐘後按「重新整理」確認，不要直接重點。";

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // 無痕模式或儲存被封鎖：只是下次要重填，不影響送單
  }
}

function readText(key: string): string {
  try {
    return localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

function saveText(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // 同上
  }
}

export const readContributorName = () => readText(NAME_KEY);
export const saveContributorName = (name: string) => saveText(NAME_KEY, name);
/** 選填，給團長聯絡用；沒填就不送到 Nidin */
export const readContributorPhone = () => readText(PHONE_KEY);
export const saveContributorPhone = (phone: string) => saveText(PHONE_KEY, phone);

export function myOrderTokens(sessionId: string): Record<string, string> {
  return { ...(memoryOrders[sessionId] ?? {}), ...(readJson<MyOrders>(ORDERS_KEY, {})[sessionId] ?? {}) };
}

function rememberOrder(sessionId: string, orderId: string, token: string): void {
  memoryOrders[sessionId] = { ...(memoryOrders[sessionId] ?? {}), [orderId]: token };
  const all = readJson<MyOrders>(ORDERS_KEY, {});
  writeJson(ORDERS_KEY, { ...all, [sessionId]: { ...(all[sessionId] ?? {}), [orderId]: token } });
}

function forgetOrder(sessionId: string, orderId: string): void {
  const { [orderId]: _memory, ...memoryRest } = memoryOrders[sessionId] ?? {};
  memoryOrders[sessionId] = memoryRest;
  const all = readJson<MyOrders>(ORDERS_KEY, {});
  const { [orderId]: _removed, ...rest } = all[sessionId] ?? {};
  writeJson(ORDERS_KEY, { ...all, [sessionId]: rest });
}

async function detailOf(response: Response, fallback: string): Promise<string> {
  const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
  if (typeof body.detail === "string") return body.detail;
  // 沒有後端說明的 5xx 多半是代理逾時（nginx 502/504）：請求可能已經在 Nidin 成立
  if (response.status >= 500) return UNCERTAIN_MESSAGE;
  if (Array.isArray(body.detail)) {
    const first = body.detail[0] as { msg?: string } | undefined;
    if (first?.msg?.includes("請先填你的名字")) return "請先填你的名字。";
    if (first?.msg?.includes("手機格式錯誤")) return "電話格式不對：請填 09 開頭的 10 碼手機，或留空。";
  }
  return fallback;
}

export async function submitLiveOrder(
  sessionId: string,
  submissionId: string,
  contributorName: string,
  lines: LiveOrderLine[],
  contributorPhone = "",
): Promise<SubmitOrderResult> {
  let response: Response;
  try {
    response = await fetch(`/api/cart-sessions/${sessionId}/orders`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        submission_id: submissionId,
        contributor_name: contributorName.trim(),
        contributor_phone: contributorPhone.trim(),
        lines,
      }),
    });
  } catch {
    // 連線中斷時無從得知後端有沒有送出
    throw new Error(UNCERTAIN_MESSAGE);
  }
  if (!response.ok) {
    throw new Error(await detailOf(response, "送出失敗，這筆沒有送到 Nidin。"));
  }
  const result = (await response.json()) as SubmitOrderResult;
  rememberOrder(sessionId, result.official_order_id, result.cancel_token);
  return result;
}

export async function cancelLiveOrder(sessionId: string, officialOrderId: string): Promise<CartSessionDetail> {
  const token = myOrderTokens(sessionId)[officialOrderId];
  const response = await fetch(`/api/cart-sessions/${sessionId}/orders/${officialOrderId}`, {
    method: "DELETE",
    headers: token ? { "X-Cancel-Token": token } : {},
  });
  if (!response.ok) {
    throw new Error(await detailOf(response, "取消失敗，請稍後再試或聯絡主揪。"));
  }
  forgetOrder(sessionId, officialOrderId);
  return (await response.json()) as CartSessionDetail;
}

export async function refreshFromNidin(sessionId: string): Promise<RefreshResult | null> {
  try {
    const response = await fetch(`/api/cart-sessions/${sessionId}/refresh`, { method: "POST" });
    if (!response.ok) return null;
    return (await response.json()) as RefreshResult;
  } catch {
    return null;
  }
}

/** 內容指紋：同一個 submission_id 只能代表同一團、同一人、同一份內容 */
export function contentKey(value: unknown): string {
  const text = JSON.stringify(value);
  let hash = 5381;
  for (let i = 0; i < text.length; i += 1) hash = ((hash << 5) + hash + text.charCodeAt(i)) >>> 0;
  return hash.toString(36);
}

/** submission_id：同一批內容重按沿用同一個，後端才擋得住逾時後的重複下單 */
export function newSubmissionId(prefix: string): string {
  const random =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${random}`.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 64);
}
