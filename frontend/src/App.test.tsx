import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

const menuResponse = {
  store_id: "1717",
  store_name: "瑞光店",
  items: [
    {
      id: "drink_1",
      name: "中●阿薩姆紅茶",
      price: 35,
      group_ids: ["ice", "sweet", "topping"],
      aliases: ["中阿薩姆紅茶"],
      image_url: "https://cdn-order-v3.nidin.shop/product/images/red-tea.png",
      category_id: "tea",
      category_name: "找好茶",
    },
    {
      id: "drink_2",
      name: "中●奶茶",
      price: 50,
      group_ids: ["ice", "sweet", "topping"],
      aliases: ["中奶茶"],
      category_id: "milk",
      category_name: "找奶茶",
    },
  ],
  groups: [
    {
      id: "ice",
      name: "冰塊",
      min_select: 1,
      max_select: 1,
      options: [
        { id: "ice_normal", name: "正常冰", price: 0, aliases: [] },
        { id: "ice_less", name: "少冰", price: 0, aliases: [] },
      ],
    },
    {
      id: "sweet",
      name: "甜度",
      min_select: 1,
      max_select: 1,
      options: [
        { id: "sweet_normal", name: "正常甜", price: 0, aliases: [] },
        { id: "sweet_half", name: "半糖", price: 0, aliases: [] },
      ],
    },
    {
      id: "topping",
      name: "加料",
      min_select: 0,
      max_select: 1,
      options: [{ id: "topping_pearl", name: "珍珠", price: 5, aliases: [] }],
    },
  ],
  categories: [
    { id: "tea", name: "找好茶", sort: 1 },
    { id: "milk", name: "找奶茶", sort: 2 },
  ],
};

const emptySessionSummary = {
  session_id: "session-1",
  group_buy_url: "https://order.nidin.shop/gb/menu/1717/TESTCODE01",
  store_id: "1717",
  group_buy_code: "TESTCODE01",
  session_name: "50嵐 瑞光店",
  store_name: "瑞光店",
  line_count: 0,
  total_money: 0,
  delivery_minimum: 250,
  delivery_description: "最低滿250外送",
  order_status_reason: "滿250元有外送",
  created_at: "2026-03-09T16:00:00+00:00",
  updated_at: "2026-03-09T16:00:00+00:00",
};

const filledSessionDetail = {
  ...emptySessionSummary,
  line_count: 1,
  total_money: 40,
  lines: [
    {
      line_id: "line-1",
      contributor_name: "小明",
      item_id: "drink_1",
      item_name: "中●阿薩姆紅茶",
      category_name: "找好茶",
      quantity: 1,
      selections: [
        { group_id: "ice", group_name: "冰塊", option_id: "ice_normal", option_name: "正常冰", price: 0 },
        { group_id: "sweet", group_name: "甜度", option_id: "sweet_normal", option_name: "正常甜", price: 0 },
        { group_id: "topping", group_name: "加料", option_id: "topping_pearl", option_name: "珍珠", price: 5 },
      ],
      line_total: 40,
      created_at: "2026-03-09T16:10:00+00:00",
    },
  ],
};

function createFetchMock(...handlers: Array<(input: RequestInfo | URL, init?: RequestInit) => unknown | null>) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    for (const handler of handlers) {
      const response = handler(input, init);
      if (response !== null) {
        return response;
      }
    }

    throw new Error(`Unexpected fetch: ${String(input)}`);
  });
}

describe("App", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    window.history.replaceState({}, "", "/");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads the default shared cart and filters items by category", async () => {
    const fetchMock = createFetchMock(
      (input) =>
        String(input) === "/api/cart-sessions"
          ? {
              ok: true,
              json: async () => [filledSessionDetail],
            }
          : null,
      (input) =>
        String(input) === "/api/cart-sessions/session-1/menu"
          ? {
              ok: true,
              json: async () => menuResponse,
            }
          : null,
      (input) =>
        String(input) === "/api/cart-sessions/session-1"
          ? {
              ok: true,
              json: async () => filledSessionDetail,
            }
          : null,
    );

    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    await screen.findByText("滿250元有外送");
    expect(screen.getByAltText("中●阿薩姆紅茶")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("分類"), { target: { value: "milk" } });

    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /中●阿薩姆紅茶/ })).not.toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /中●奶茶/ })).toBeInTheDocument();
  });

  it("requires a name, sends the selection to Nidin, remembers the name, and lets the orderer cancel", async () => {
    const mine = { ...filledSessionDetail, lines: [{ ...filledSessionDetail.lines[0], official_order_id: "305443999" }] };
    const fetchMock = createFetchMock((input, init) => {
      const url = String(input);
      if (url === "/api/cart-sessions") return { ok: true, json: async () => [emptySessionSummary] };
      if (url === "/api/cart-sessions/session-1/menu") return { ok: true, json: async () => menuResponse };
      if (url === "/api/cart-sessions/session-1") return { ok: true, json: async () => ({ ...emptySessionSummary, lines: [] }) };
      if (url === "/api/cart-sessions/session-1/refresh") return { ok: false, json: async () => ({}) };
      if (url === "/api/cart-sessions/session-1/orders" && init?.method === "POST") {
        return {
          ok: true,
          json: async () => ({
            receipt: {
              order_id: 305443999, order_status: 31, store_id: "1717", group_buy_code: "TESTCODE01",
              guest_user_id: 9000001, total_money: 40, item_name: "中●阿薩姆紅茶",
            },
            official_order_id: "305443999",
            cancel_token: "cancel-secret",
            session: mine,
            already_submitted: false,
          }),
        };
      }
      if (url === "/api/cart-sessions/session-1/orders/305443999" && init?.method === "DELETE") {
        return { ok: true, json: async () => ({ ...emptySessionSummary, lines: [] }) };
      }
      return null;
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    await screen.findByRole("button", { name: /中●阿薩姆紅茶/ });
    fireEvent.click(screen.getByRole("button", { name: /中●阿薩姆紅茶/ }));
    fireEvent.click(await screen.findByRole("button", { name: "加入購物車" }));
    expect(screen.getByText("1 x 中●阿薩姆紅茶")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "送出訂單" }));
    await screen.findByText("先填你的名字，它會是你在 Nidin 上這筆單的名字。");
    expect(fetchMock).not.toHaveBeenCalledWith("/api/cart-sessions/session-1/orders", expect.anything());

    fireEvent.change(screen.getByPlaceholderText("你的名字（會顯示在 Nidin 上）"), { target: { value: "小明" } });
    expect(localStorage.getItem("nidin_contributor_name")).toBe("小明");
    fireEvent.change(screen.getByPlaceholderText("團長聯絡用，不填也可以"), { target: { value: "0912345678" } });
    fireEvent.click(screen.getByRole("button", { name: "送出訂單" }));

    await screen.findByText("已送到 Nidin，單號 305443999。");
    await waitFor(() => expect(screen.queryByText("1 x 中●阿薩姆紅茶")).not.toBeInTheDocument());
    const [, init] = fetchMock.mock.calls.find(([url]) => String(url) === "/api/cart-sessions/session-1/orders")!;
    const body = JSON.parse(String(init?.body));
    expect(body.submission_id).toMatch(/^cart-[A-Za-z0-9_-]+$/);
    expect(body.contributor_name).toBe("小明");
    expect(body.contributor_phone).toBe("0912345678");
    expect(body.lines).toEqual([{ item_id: "drink_1", quantity: 1, selection_option_ids: ["ice_normal", "sweet_normal"] }]);
    expect(screen.getByText("訂單編號：305443999")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "取消這筆" }));
    await screen.findByText("已在 Nidin 取消單號 305443999。");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/cart-sessions/session-1/orders/305443999",
      expect.objectContaining({ method: "DELETE", headers: { "X-Cancel-Token": "cancel-secret" } }),
    );
  });

  it("names Ocard for an Ocard group and never sends a phone number there", async () => {
    const ocardSummary = {
      ...emptySessionSummary,
      provider: "ocard",
      group_buy_url: "https://order.ocard.co/teabrand/Store01?group=GRP0001",
      store_id: "teabrand/Store01",
      group_buy_code: "GRP0001",
    };
    // 之前在 Nidin 團留過電話：換到 Ocard 團也不能送出去
    localStorage.setItem("nidin_contributor_phone", "0912345678");
    const fetchMock = createFetchMock((input, init) => {
      const url = String(input);
      if (url === "/api/cart-sessions") return { ok: true, json: async () => [ocardSummary] };
      if (url === "/api/cart-sessions/session-1/menu") return { ok: true, json: async () => menuResponse };
      if (url === "/api/cart-sessions/session-1") return { ok: true, json: async () => ({ ...ocardSummary, lines: [] }) };
      if (url === "/api/cart-sessions/session-1/refresh") return { ok: false, json: async () => ({}) };
      if (url === "/api/cart-sessions/session-1/orders" && init?.method === "POST") {
        return {
          ok: true,
          json: async () => ({
            receipt: null,
            official_order_id: "ORDNEW01",
            cancel_token: "cancel-secret",
            session: { ...ocardSummary, lines: [] },
            already_submitted: false,
          }),
        };
      }
      return null;
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    await screen.findByRole("button", { name: /中●阿薩姆紅茶/ });
    expect(screen.queryByPlaceholderText("團長聯絡用，不填也可以")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /中●阿薩姆紅茶/ }));
    fireEvent.click(await screen.findByRole("button", { name: "加入購物車" }));
    fireEvent.change(screen.getByPlaceholderText("你的名字（會顯示在 Ocard 上）"), { target: { value: "小美" } });
    fireEvent.click(screen.getByRole("button", { name: "送出訂單" }));

    await screen.findByText("已送到 Ocard，單號 ORDNEW01。");
    const [, init] = fetchMock.mock.calls.find(([url]) => String(url) === "/api/cart-sessions/session-1/orders")!;
    expect(JSON.parse(String(init?.body)).contributor_phone).toBe("");
  });

  it("does not let a slow, older refresh wipe an order that was just sent", async () => {
    localStorage.setItem("nidin_contributor_name", "小明");
    const mine = { ...filledSessionDetail, lines: [{ ...filledSessionDetail.lines[0], official_order_id: "305443999" }] };
    let finishRefresh!: (value: unknown) => void;
    const slowRefresh = new Promise((resolve) => { finishRefresh = resolve; });
    vi.stubGlobal("fetch", createFetchMock((input, init) => {
      const url = String(input);
      if (url === "/api/cart-sessions") return { ok: true, json: async () => [emptySessionSummary] };
      if (url.endsWith("/menu")) return { ok: true, json: async () => menuResponse };
      if (url === "/api/cart-sessions/session-1") return { ok: true, json: async () => ({ ...emptySessionSummary, lines: [] }) };
      if (url.endsWith("/refresh")) return slowRefresh;
      if (url === "/api/cart-sessions/session-1/orders" && init?.method === "POST") {
        return { ok: true, json: async () => ({ receipt: null, official_order_id: "305443999", cancel_token: "t", session: mine, already_submitted: false }) };
      }
      return null;
    }));
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /中●阿薩姆紅茶/ }));
    fireEvent.click(await screen.findByRole("button", { name: "加入購物車" }));
    fireEvent.click(screen.getByRole("button", { name: "送出訂單" }));
    await screen.findByText("已送到 Nidin，單號 305443999。");
    expect(screen.getByRole("button", { name: "取消這筆" })).toBeInTheDocument();

    // 送單之前就發出去的刷新現在才回來，內容是還沒有這筆的舊狀態
    finishRefresh({ ok: true, json: async () => ({ session: { ...emptySessionSummary, lines: [] }, synced: true, message: null }) });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByRole("button", { name: "取消這筆" })).toBeInTheDocument();
  });

  it.each([true, false])("keeps drafts added while a Nidin submission is pending (Nidin ok: %s)", async (nidinOk) => {
    localStorage.setItem("nidin_contributor_name", "小明");
    let finishOfficial!: (value: unknown) => void;
    const officialPending = new Promise((resolve) => { finishOfficial = resolve; });
    vi.stubGlobal("fetch", createFetchMock((input) => {
      const url = String(input);
      if (url === "/api/cart-sessions") return { ok: true, json: async () => [emptySessionSummary] };
      if (url.endsWith("/menu")) return { ok: true, json: async () => menuResponse };
      if (url === "/api/cart-sessions/session-1") return { ok: true, json: async () => ({ ...emptySessionSummary, lines: [] }) };
      if (url.endsWith("/refresh")) return { ok: false, json: async () => ({}) };
      if (url === "/api/cart-sessions/session-1/orders") return officialPending;
      return null;
    }));
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /中●阿薩姆紅茶/ }));
    fireEvent.click(await screen.findByRole("button", { name: "加入購物車" }));
    fireEvent.click(screen.getByRole("button", { name: "送出訂單" }));
    fireEvent.click(screen.getByRole("button", { name: /中●奶茶/ }));
    fireEvent.click(await screen.findByRole("button", { name: "加入購物車" }));
    expect(screen.getByText("1 x 中●奶茶")).toBeInTheDocument();
    finishOfficial(nidinOk
      ? {
          ok: true,
          json: async () => ({
            receipt: null, official_order_id: "305443999", cancel_token: "t",
            session: filledSessionDetail, already_submitted: false,
          }),
        }
      : { ok: false, json: async () => ({ detail: "這個團在 Nidin 上已經截止，不能再點了。" }) });
    await screen.findByText(nidinOk ? "已送到 Nidin，單號 305443999。" : "這個團在 Nidin 上已經截止，不能再點了。");
    fireEvent.click(screen.getByRole("button", { name: /待送出/ }));
    expect(screen.getByText("1 x 中●奶茶")).toBeInTheDocument();
    // 送成功才移除送出的那杯；Nidin 拒絕時兩杯都保留，讓人改完再送
    expect(screen.queryByText("1 x 中●阿薩姆紅茶") !== null).toBe(!nidinOk);
  });

  it("lets the leader delete a follower's Nidin order from the admin page after confirming", async () => {
    window.history.replaceState({}, "", "/admin");
    const follower = { ...filledSessionDetail.lines[0], line_id: "l-1", contributor_name: "小華", official_order_id: "300000004" };
    const head = { ...filledSessionDetail.lines[0], line_id: "l-2", contributor_name: "團長", official_order_id: "head:M0300000002" };
    const withOrders = { ...filledSessionDetail, lines: [follower, head] };
    let deleted = false;
    const fetchSpy = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/auth/me") return { ok: true, json: async () => ({ id: 1, username: "leader", is_admin: false }) };
      if (url.endsWith("/payments")) return { ok: true, json: async () => ({}) };
      if (url === "/api/leader/auth") return { ok: true, json: async () => ({ is_authenticated: false }) };
      if (url === "/api/admin/cart-sessions") return { ok: true, json: async () => [emptySessionSummary] };
      if (url === "/api/cart-sessions/session-1") {
        return { ok: true, json: async () => (deleted ? { ...withOrders, lines: [head] } : withOrders) };
      }
      if (url === "/api/cart-sessions/session-1/orders/300000004" && init?.method === "DELETE") {
        deleted = true;
        return { ok: true, json: async () => ({ ...withOrders, lines: [head] }) };
      }
      return { ok: true, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchSpy);

    render(<App />);

    const button = await screen.findByRole("button", { name: "刪除這筆" });
    expect(screen.getAllByRole("button", { name: "刪除這筆" })).toHaveLength(1);
    fireEvent.click(button);
    expect(fetchSpy).not.toHaveBeenCalledWith("/api/cart-sessions/session-1/orders/300000004", expect.anything());
    fireEvent.click(screen.getByRole("button", { name: "確定刪除？" }));

    await screen.findByText("已在 Nidin 刪除單號 300000004。");
    expect(fetchSpy).toHaveBeenCalledWith("/api/cart-sessions/session-1/orders/300000004", { method: "DELETE" });
    await waitFor(() => expect(screen.queryByRole("button", { name: "刪除這筆" })).not.toBeInTheDocument());
  });

  it("removes a group from this site only after confirming", async () => {
    window.history.replaceState({}, "", "/admin");
    let removed = false;
    const fetchSpy = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/auth/me") return { ok: true, json: async () => ({ id: 1, username: "leader", is_admin: false }) };
      if (url.endsWith("/payments")) return { ok: true, json: async () => ({}) };
      if (url === "/api/leader/auth") return { ok: true, json: async () => ({ is_authenticated: false }) };
      if (url === "/api/admin/cart-sessions") return { ok: true, json: async () => (removed ? [] : [emptySessionSummary]) };
      if (url === "/api/cart-sessions/session-1" && init?.method === "DELETE") {
        removed = true;
        return { ok: true, json: async () => ({ deleted: true }) };
      }
      if (url === "/api/cart-sessions/session-1") return { ok: true, json: async () => ({ ...emptySessionSummary, lines: [] }) };
      return { ok: true, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchSpy);

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: "從本站移除" }));
    expect(fetchSpy).not.toHaveBeenCalledWith("/api/cart-sessions/session-1", { method: "DELETE" });
    fireEvent.click(screen.getByRole("button", { name: "確定移除？" }));

    await screen.findByText("已從本站移除 50嵐 瑞光店（平台上的團不受影響）。");
    expect(fetchSpy).toHaveBeenCalledWith("/api/cart-sessions/session-1", { method: "DELETE" });
  });

  it("creates a new cart session from the admin page and shows its summary", async () => {
    window.history.replaceState({}, "", "/admin");

    const createdSession = { ...emptySessionSummary, session_id: "session-2", group_buy_code: "NEWCODE" };

    const fetchSpy = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/auth/me") return { ok: true, json: async () => ({ id: 1, username: "leader", is_admin: false }) };
      if (String(input).endsWith("/payments")) return { ok: true, json: async () => ({}) };
      if (String(input) === "/api/leader/auth") return { ok: true, json: async () => ({ is_authenticated: false }) };
      if (String(input) === "/api/admin/cart-sessions" && (!init || init.method === undefined)) {
        return {
          ok: true,
          json: async () => [],
        };
      }

      if (String(input) === "/api/cart-sessions" && init?.method === "POST") {
        return {
          ok: true,
          json: async () => createdSession,
        };
      }

      if (String(input) === "/api/cart-sessions/session-2") {
        return {
          ok: true,
          json: async () => ({ ...createdSession, lines: [] }),
        };
      }

      throw new Error(`Unexpected fetch: ${String(input)}`);
    });

    vi.stubGlobal("fetch", fetchSpy);

    render(<App />);

    await screen.findByText("新增團購");

    fireEvent.change(screen.getByLabelText("揪團連結"), {
      target: { value: "https://order.nidin.shop/gb/menu/1717/NEWCODE" },
    });
    fireEvent.click(screen.getByRole("button", { name: "建立購物車" }));

    await screen.findByText("已新增 50嵐 瑞光店。");
    expect(screen.getAllByText("瑞光店")).toHaveLength(1);
    expect(screen.getByText("目前還沒有加入任何品項。")).toBeInTheDocument();
    expect(screen.getByText("目前已收單金額")).toBeInTheDocument();
    expect(screen.queryByText("NEWCODE")).not.toBeInTheDocument();
  });

  it("clears the shared cart from the admin page", async () => {
    window.history.replaceState({}, "", "/admin");

    const clearedSessionDetail = { ...emptySessionSummary, lines: [] };

    const fetchSpy = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/auth/me") return { ok: true, json: async () => ({ id: 1, username: "leader", is_admin: false }) };
      if (String(input).endsWith("/payments")) return { ok: true, json: async () => ({}) };
      if (String(input) === "/api/leader/auth") return { ok: true, json: async () => ({ is_authenticated: false }) };
      if (String(input) === "/api/admin/cart-sessions" && (!init || init.method === undefined)) {
        return {
          ok: true,
          json: async () => [filledSessionDetail],
        };
      }

      if (String(input) === "/api/cart-sessions/session-1" && (!init || init.method === undefined)) {
        return {
          ok: true,
          json: async () => filledSessionDetail,
        };
      }

      if (String(input) === "/api/cart-sessions/session-1/lines" && init?.method === "DELETE") {
        return {
          ok: true,
          json: async () => clearedSessionDetail,
        };
      }

      throw new Error(`Unexpected fetch: ${String(input)}`);
    });

    vi.stubGlobal("fetch", fetchSpy);

    render(<App />);

    await screen.findByText("中●阿薩姆紅茶");
    fireEvent.click(screen.getByRole("button", { name: "清空已收單" }));

    await screen.findByText("已清空 50嵐 瑞光店。");
    expect(screen.getByText("目前還沒有加入任何品項。")).toBeInTheDocument();
    expect(screen.getByText("目前已收單金額")).toBeInTheDocument();
  });

  it("displays order badges on menu items and allows viewing and cloning group orders", async () => {
    const fetchMock = createFetchMock(
      (input) =>
        String(input) === "/api/cart-sessions"
          ? {
              ok: true,
              json: async () => [filledSessionDetail],
            }
          : null,
      (input) =>
        String(input) === "/api/cart-sessions/session-1/menu"
          ? {
              ok: true,
              json: async () => menuResponse,
            }
          : null,
      (input) =>
        String(input) === "/api/cart-sessions/session-1"
          ? {
              ok: true,
              json: async () => filledSessionDetail,
            }
          : null,
    );

    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    // 1. 驗證菜單卡片包含「點餐」文字徽章
    await screen.findByText("滿250元有外送");
    const orderBadges = screen.getAllByText("點餐");
    expect(orderBadges.length).toBeGreaterThan(0);

    // 2. 驗證分頁切換到「本團訂單」
    const ordersTabBtn = screen.getByRole("button", { name: /本團訂單/ });
    expect(ordersTabBtn).toBeInTheDocument();
    fireEvent.click(ordersTabBtn);

    // 3. 驗證訂單顯示內容
    expect(screen.getByText("外送門檻進度")).toBeInTheDocument();
    expect(screen.getByText("差 NT$210 免運")).toBeInTheDocument();
    expect(screen.getByText("中●阿薩姆紅茶 × 1")).toBeInTheDocument();
    expect(screen.getByText("小明")).toBeInTheDocument();

    // 4. 驗證「+1 跟著點」按鈕功能
    const cloneBtn = screen.getByRole("button", { name: "+1 跟著點" });
    fireEvent.click(cloneBtn);

    // 點擊後應自動切回「待送出」Tab 並加入暫存清單
    expect(screen.getByText("1 x 中●阿薩姆紅茶")).toBeInTheDocument();
  });

  it.each(["2026-09-17T08:30:00+00:00", null])("shows cached menu freshness (%s)", async (fetchedAt) => {
    vi.stubGlobal("fetch", createFetchMock((input) => {
      const url = String(input);
      if (url === "/api/cart-sessions") return { ok: true, json: async () => [filledSessionDetail] };
      if (url.endsWith("/menu")) return { ok: true, json: async () => ({ ...menuResponse, menu_source: "cache", menu_fetched_at: fetchedAt }) };
      if (url === "/api/cart-sessions/session-1") return { ok: true, json: async () => filledSessionDetail };
      return null;
    }));
    render(<App />);
    const notice = await screen.findByRole("status");
    expect(notice).toHaveTextContent("使用上次菜單");
    if (fetchedAt) {
      expect(notice.querySelector("time")).toHaveAttribute("datetime", fetchedAt);
      expect(notice).toHaveTextContent("臺北時間");
    } else {
      expect(notice).toHaveTextContent("更新時間未記錄");
    }
    expect(screen.getByAltText("中●阿薩姆紅茶")).toBeInTheDocument();
  });

  it("isolates payment marks by session and restores them from the server after remount", async () => {
    window.history.replaceState({}, "", "/admin");
    localStorage.setItem("nidin_paid_contributors", JSON.stringify({ 小明: true }));
    localStorage.setItem("nidin_paid_contributors_v2", JSON.stringify({ '["session-1","小明"]': true }));
    const second = { ...filledSessionDetail, session_id: "session-2", session_name: "第二團" };
    const payments: Record<string, Record<string, boolean>> = { "session-1": {}, "session-2": {} };
    const fetchMock = createFetchMock((input, init) => {
      const url = String(input);
      if (url === "/api/auth/me") return { ok: true, json: async () => ({ id: 1, username: "leader", is_admin: false }) };
      if (url === "/api/leader/auth") return { ok: true, json: async () => ({ is_authenticated: false }) };
      if (url === "/api/admin/cart-sessions") return { ok: true, json: async () => [filledSessionDetail, second] };
      if (url.endsWith("/payments")) {
        const sessionId = url.split("/")[3];
        if (init?.method === "PUT") {
          const body = JSON.parse(String(init.body));
          payments[sessionId][body.contributor_name] = body.paid;
        }
        return { ok: true, json: async () => ({ ...payments[sessionId] }) };
      }
      if (url === "/api/cart-sessions/session-1") return { ok: true, json: async () => filledSessionDetail };
      if (url === "/api/cart-sessions/session-2") return { ok: true, json: async () => second };
      return null;
    });
    vi.stubGlobal("fetch", fetchMock);
    const firstRender = render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /分飲料與對帳表/ }));
    fireEvent.click(screen.getByRole("button", { name: "待收款" }));
    expect(await screen.findByRole("button", { name: "已收清" })).toHaveAttribute("aria-pressed", "true");
    expect(fetchMock).toHaveBeenCalledWith("/api/cart-sessions/session-1/payments", expect.objectContaining({
      method: "PUT", body: JSON.stringify({ contributor_name: "小明", paid: true }),
    }));
    fireEvent.click(screen.getByRole("button", { name: /第二團/ }));
    await screen.findByRole("heading", { name: "第二團" });
    expect(screen.getByRole("button", { name: "待收款" })).toHaveAttribute("aria-pressed", "false");
    firstRender.unmount();
    localStorage.clear();
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /分飲料與對帳表/ }));
    expect(screen.getByRole("button", { name: "已收清" })).toBeInTheDocument();
  });

  it("keeps the payment mark unchanged when saving fails", async () => {
    window.history.replaceState({}, "", "/admin");
    vi.stubGlobal("fetch", createFetchMock((input, init) => {
      const url = String(input);
      if (url === "/api/auth/me") return { ok: true, json: async () => ({ id: 1, username: "leader", is_admin: false }) };
      if (url === "/api/leader/auth") return { ok: true, json: async () => ({ is_authenticated: false }) };
      if (url === "/api/admin/cart-sessions") return { ok: true, json: async () => [filledSessionDetail] };
      if (url.endsWith("/payments")) return { ok: init?.method !== "PUT", json: async () => ({}) };
      if (url === "/api/cart-sessions/session-1") return { ok: true, json: async () => filledSessionDetail };
      return null;
    }));
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /分飲料與對帳表/ }));
    fireEvent.click(screen.getByRole("button", { name: "待收款" }));
    await screen.findByText("儲存收款狀態失敗，請重新整理後再試。");
    expect(screen.getByRole("button", { name: "待收款" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByRole("button", { name: "已收清" })).not.toBeInTheDocument();
  });
});
