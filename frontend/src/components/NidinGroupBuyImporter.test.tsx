import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NidinGroupBuyImporter } from "./NidinGroupBuyImporter";

function response(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body };
}

const OPEN = {
  order_id: "300000002", store_id: "15958", title: "得正 示範店A", group_buying_name: "測試開的團",
  created_at: "2026-09-24 16:57:37", status_text: "揪團中（尚未送單）", submitted: false, total_money: 0, follower_count: 0, session_id: null,
};
const DONE = {
  ...OPEN, order_id: "300000003", title: "示範品牌 示範店B", created_at: "2026-09-23 13:25:29",
  status_text: "完成", submitted: true, total_money: 910, follower_count: 14, session_id: "sess-1",
};

describe("NidinGroupBuyImporter", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("lists Nidin group buys, imports one, and turns its button into 查看", async () => {
    const session = { session_id: "new-1", session_name: "得正 示範店A", line_count: 2 };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (input === "/api/nidin/group-buys") return response([OPEN, DONE]);
      if (input === "/api/nidin/group-buys/300000002/import") return response({ session, sync_error: null });
      throw new Error("Unexpected fetch: " + String(input));
    });
    vi.stubGlobal("fetch", fetchMock);
    const onImported = vi.fn();
    const onOpen = vi.fn();

    render(<NidinGroupBuyImporter onImported={onImported} onOpen={onOpen} />);

    expect(await screen.findByText("得正 示範店A")).toBeInTheDocument();
    expect(screen.getByText(/09-23 13:25 · 完成 · 14 人/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看" }));
    expect(onOpen).toHaveBeenCalledWith("sess-1");

    fireEvent.click(screen.getByRole("button", { name: "匯入" }));
    await vi.waitFor(() => expect(onImported).toHaveBeenCalledWith(session, null));
    expect(screen.getAllByRole("button", { name: "查看" })).toHaveLength(2);
  });

  it("shows why Nidin could not be read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({ detail: "尚未連結 Nidin，請先到「設定 Nidin 連線」登入。" }, 401)));

    render(<NidinGroupBuyImporter onImported={vi.fn()} onOpen={vi.fn()} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("尚未連結 Nidin");
  });
});
