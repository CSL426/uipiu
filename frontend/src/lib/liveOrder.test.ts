import { afterEach, describe, expect, it, vi } from "vitest";

import { cancelLiveOrder, contentKey, myOrderTokens, submitLiveOrder } from "./liveOrder";

const okResult = (orderId: string) => ({
  ok: true,
  status: 200,
  json: async () => ({ receipt: null, official_order_id: orderId, cancel_token: `tok-${orderId}`, session: {}, already_submitted: false }),
});

describe("liveOrder", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("treats a bare gateway error as 'maybe placed', never as 'not sent'", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 504, json: async () => { throw new SyntaxError("html"); } })));
    await expect(submitLiveOrder("s1", "sub-00000001", "小明", [])).rejects.toThrow("可能已經成立");
  });

  it("treats a dropped connection as 'maybe placed'", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    await expect(submitLiveOrder("s1", "sub-00000001", "小明", [])).rejects.toThrow("可能已經成立");
  });

  it("keeps the backend's own explanation when it gives one", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 503, json: async () => ({ detail: "連不上 Nidin，這筆還沒有送出。" }) })));
    await expect(submitLiveOrder("s1", "sub-00000001", "小明", [])).rejects.toThrow("這筆還沒有送出");
  });

  it("still lets you cancel from this tab when localStorage is blocked", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("blocked"); });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new DOMException("blocked"); });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(okResult("42"))
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ session_id: "s1", lines: [] }) });
    vi.stubGlobal("fetch", fetchMock);

    await submitLiveOrder("s1", "sub-00000001", "小明", []);
    expect(myOrderTokens("s1")).toEqual({ "42": "tok-42" });
    await cancelLiveOrder("s1", "42");

    expect(fetchMock).toHaveBeenLastCalledWith("/api/cart-sessions/s1/orders/42", expect.objectContaining({
      headers: { "X-Cancel-Token": "tok-42" },
    }));
    expect(myOrderTokens("s1")).toEqual({});
  });

  it("gives different content different keys", () => {
    expect(contentKey(["s1", "小明", [{ q: 1 }]])).toBe(contentKey(["s1", "小明", [{ q: 1 }]]));
    expect(contentKey(["s1", "小明", [{ q: 1 }]])).not.toBe(contentKey(["s1", "小明", [{ q: 2 }]]));
    expect(contentKey(["s1", "小明", [{ q: 1 }]])).not.toBe(contentKey(["s2", "小明", [{ q: 1 }]]));
  });
});
