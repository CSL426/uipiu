import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NidinStoreSearch } from "./NidinStoreSearch";

function response(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body };
}

const RESULT = {
  brands: ["青山"],
  origin: "辦公室",
  stores: [
    { store_id: "28417", name: "台北內湖店", address: "內湖路", distance_km: 2.3, rating: 4.73, rating_count: 60, score: 4.55, orderable: true },
    { store_id: "29638", name: "台北松菸店", address: "忠孝東路", distance_km: 2.6, rating: 5, rating_count: 1, score: 4.05, orderable: false },
  ],
};

describe("NidinStoreSearch", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("finds stores by brand and opens a group only after confirming", async () => {
    const session = { session_id: "new-1", session_name: "青山 台北內湖店" };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/nidin/stores?")) return response(RESULT);
      if (url === "/api/nidin/stores/28417/group-buy" && init?.method === "POST") {
        return response({ session, sync_error: null, pickup: "delivery", pickup_note: "已設成外送到「公司」（路線約 5.0 公里）。" });
      }
      throw new Error("Unexpected fetch: " + url);
    });
    vi.stubGlobal("fetch", fetchMock);
    const onOpened = vi.fn();

    render(<NidinStoreSearch onOpened={onOpened} />);
    fireEvent.change(screen.getByPlaceholderText("例如：青山"), { target: { value: "青山" } });
    fireEvent.click(screen.getByRole("button", { name: "找店" }));

    expect(await screen.findByText("台北內湖店")).toBeInTheDocument();
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/nidin/stores?brand=%E9%9D%92%E5%B1%B1&sort=distance");
    expect(screen.getByText(/距離從「辦公室」算起/)).toBeInTheDocument();
    const [first, closed] = screen.getAllByRole("button", { name: "開團" });
    expect(closed).toBeDisabled();

    fireEvent.click(first);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "確定開團？" }));
    await vi.waitFor(() => expect(onOpened).toHaveBeenCalledWith(session, "已設成外送到「公司」（路線約 5.0 公里）。"));
  });

  it("explains an unknown brand", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({ detail: "Nidin 上找不到「不存在」這個品牌。" }, 404)));
    render(<NidinStoreSearch onOpened={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText("例如：青山"), { target: { value: "不存在" } });
    fireEvent.click(screen.getByRole("button", { name: "找店" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("找不到「不存在」");
  });
});
