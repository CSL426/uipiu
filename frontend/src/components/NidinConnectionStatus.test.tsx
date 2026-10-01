import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { formatExpiry, NidinConnectionStatus } from "./NidinConnectionStatus";

function response(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body };
}

describe("NidinConnectionStatus", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("checks against Nidin as soon as it opens and shows who the token belongs to", async () => {
    const expires = new Date(2026, 9, 24, 16, 14).getTime() / 1000;
    const fetchMock = vi.fn(async () =>
      response({ nidin_user_id: "12345678", display_name: "阿主", expires_at: expires, relogged_in: false, auto_relogin: true }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<NidinConnectionStatus />);

    expect(screen.getByRole("status")).toHaveTextContent("正在向 Nidin 確認連線");
    expect(await screen.findByText("Nidin 連線正常")).toBeInTheDocument();
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("會員：阿主（ID 12345678）");
    expect(status).toHaveTextContent("憑證有效到：2026/10/24 16:14");
    expect(status).toHaveTextContent("自動重新登入");
    expect(fetchMock).toHaveBeenCalledWith("/api/leader/auth/check", { method: "POST" });
  });

  it("tells the parent when the check had to log in again", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      response({ nidin_user_id: "1", display_name: "", expires_at: null, relogged_in: true, auto_relogin: true }),
    ));
    const onRelogged = vi.fn();

    render(<NidinConnectionStatus onRelogged={onRelogged} />);

    expect(await screen.findByText("剛才舊憑證已失效，已自動重新登入。")).toBeInTheDocument();
    expect(onRelogged).toHaveBeenCalledOnce();
  });

  it("shows the server's reason when Nidin rejects the credential, and can retry", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response({ detail: "Nidin 憑證已失效，請到「設定 Nidin 連線」重新綁定。" }, 401))
      .mockResolvedValueOnce(response({ nidin_user_id: "1", display_name: "阿主", expires_at: null, relogged_in: false, auto_relogin: false }));
    vi.stubGlobal("fetch", fetchMock);

    render(<NidinConnectionStatus />);

    expect(await screen.findByText(/重新綁定/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新檢查" }));
    expect(await screen.findByText("Nidin 連線正常")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("到期後要回來重新登入一次");
  });

  it("formats the expiry in local time", () => {
    expect(formatExpiry(new Date(2026, 0, 5, 9, 3).getTime() / 1000)).toBe("2026/01/05 09:03");
  });
});
