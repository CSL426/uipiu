import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiKeysPanel } from "./ApiKeysPanel";

function response(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body };
}

const EXISTING = { id: "k1", name: "舊的 agent", scope: "order", hint: "abcd", created_at: 1, last_used_at: null, revoked: false };

describe("ApiKeysPanel", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows a new key only once and revokes after a second click", async () => {
    const created = { ...EXISTING, id: "k2", name: "Claude", scope: "leader", hint: "wxyz", key: "mynig_secret-value-wxyz" };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (input === "/api/api-keys" && !init?.method) return response([EXISTING]);
      if (input === "/api/api-keys" && init?.method === "POST") return response(created);
      if (input === "/api/api-keys/k1" && init?.method === "DELETE") return response({ revoked: true });
      throw new Error("Unexpected fetch: " + String(input));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ApiKeysPanel />);

    expect(await screen.findByText(/點單 · …abcd · 最後使用 還沒用過/)).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("例如：Claude 點餐助理"), { target: { value: "Claude" } });
    fireEvent.click(screen.getByRole("button", { name: "發一把新的" }));

    expect(await screen.findByText("mynig_secret-value-wxyz")).toBeInTheDocument();
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({ name: "Claude", scope: "order" });
    fireEvent.click(screen.getByRole("button", { name: "我存好了" }));
    expect(screen.queryByText("mynig_secret-value-wxyz")).not.toBeInTheDocument();

    const [revokeOld] = screen.getAllByRole("button", { name: "撤銷" }).slice(-1);
    fireEvent.click(revokeOld);
    expect(fetchMock).not.toHaveBeenCalledWith("/api/api-keys/k1", expect.anything());
    fireEvent.click(screen.getByRole("button", { name: "確定撤銷？" }));
    await waitFor(() => expect(screen.getByText(/點單 · …abcd · 已撤銷/)).toBeInTheDocument());
  });

  it("asks for a name before creating a key", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response([])));
    render(<ApiKeysPanel />);
    await screen.findByText("還沒有發過 key。");
    fireEvent.click(screen.getByRole("button", { name: "發一把新的" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("先幫這把 key 取個名字");
  });
});
