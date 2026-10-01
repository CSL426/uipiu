import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AdminAccess } from "./AdminAccess";

const leader = { id: 1, username: "organizer", is_admin: false };
function ProtectedState() {
  const [draft, setDraft] = useState("");
  return <label>管理草稿<input value={draft} onChange={(event) => setDraft(event.target.value)} /></label>;
}
function response(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body };
}

describe("AdminAccess", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/admin");
  });

  it("gates children until login succeeds, reports invalid credentials and resets state after logout", async () => {
    let loginCount = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (input === "/api/auth/me") return response({}, 401);
      if (input === "/api/auth/login") return ++loginCount === 1 ? response({}, 401) : response(leader);
      if (input === "/api/auth/logout") return response({});
      throw new Error("Unexpected fetch");
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AdminAccess><ProtectedState /></AdminAccess>);
    expect(screen.queryByLabelText("管理草稿")).not.toBeInTheDocument();
    await screen.findByRole("heading", { name: "主揪登入" });
    fireEvent.click(screen.getByRole("button", { name: "本站帳號" }));
    fireEvent.change(screen.getByLabelText("主揪帳號"), { target: { value: "organizer" } });
    fireEvent.change(screen.getByLabelText("密碼"), { target: { value: "password-123456" } });
    fireEvent.click(screen.getByRole("button", { name: "登入" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("帳號或密碼不正確");
    expect(screen.queryByLabelText("管理草稿")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "登入" }));
    await screen.findByText("已登入：organizer");
    expect(screen.queryByText("新增主揪帳號")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("管理草稿"), { target: { value: "上一位主揪的資料" } });
    fireEvent.click(screen.getByRole("button", { name: "登出" }));
    await screen.findByRole("heading", { name: "主揪登入" });
    expect(screen.queryByLabelText("管理草稿")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("主揪帳號"), { target: { value: "organizer" } });
    fireEvent.change(screen.getByLabelText("密碼"), { target: { value: "password-123456" } });
    fireEvent.click(screen.getByRole("button", { name: "登入" }));
    expect(await screen.findByLabelText("管理草稿")).toHaveValue("");
    expect(fetchMock).toHaveBeenCalledWith("/api/auth/login", expect.objectContaining({
      method: "POST", body: JSON.stringify({ username: "organizer", password: "password-123456" }),
    }));
  });

  it("treats a password that fails the format check as wrong credentials, not a server outage", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (input === "/api/auth/me") return response({}, 401);
      if (input === "/api/auth/login") return response({ detail: [] }, 422);
      if (input === "/api/auth/qr/ticket") return response({}, 500);
      throw new Error("Unexpected fetch: " + String(input));
    }));
    render(<AdminAccess><ProtectedState /></AdminAccess>);
    await screen.findByRole("heading", { name: "主揪登入" });
    fireEvent.click(screen.getByRole("button", { name: "本站帳號" }));
    fireEvent.change(screen.getByLabelText("主揪帳號"), { target: { value: "0983000000" } });
    fireEvent.change(screen.getByLabelText("密碼"), { target: { value: "short" } });
    fireEvent.click(screen.getByRole("button", { name: "登入" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("帳號或密碼不正確");
    expect(alert).toHaveTextContent("Nidin 帳號");
  });

  it("logs in with a linked Nidin account by default and relays why an unlinked one is refused", async () => {
    let attempts = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (input === "/api/auth/me") return response({}, 401);
      if (input === "/api/auth/qr/ticket") return response({}, 500);
      if (input === "/api/auth/nidin-login") {
        return ++attempts === 1 ? response({ detail: "這個 Nidin 帳號還沒連結到本站。" }, 403) : response(leader);
      }
      throw new Error("Unexpected fetch: " + String(input));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AdminAccess><ProtectedState /></AdminAccess>);
    await screen.findByRole("heading", { name: "主揪登入" });
    fireEvent.change(screen.getByLabelText("Nidin 手機號碼"), { target: { value: "0912345678" } });
    fireEvent.change(screen.getByLabelText("Nidin 密碼"), { target: { value: "nidin-pw" } });
    fireEvent.click(screen.getByRole("button", { name: "登入" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("還沒連結到本站");

    fireEvent.click(screen.getByRole("button", { name: "登入" }));
    await screen.findByText("已登入：organizer");
    expect(fetchMock).toHaveBeenCalledWith("/api/auth/nidin-login", expect.objectContaining({
      body: JSON.stringify({ account: "0912345678", password: "nidin-pw", remember: true }),
    }));
  });

  it("allows an administrator to create an organizer and clears the password afterward", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (input === "/api/auth/me") return response({ ...leader, is_admin: true });
      if (input === "/api/auth/accounts") return response({ ...leader, id: 2, username: "new-leader" });
      throw new Error("Unexpected fetch");
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AdminAccess><ProtectedState /></AdminAccess>);
    fireEvent.click(await screen.findByText("新增主揪帳號"));
    fireEvent.change(screen.getByLabelText("新主揪帳號"), { target: { value: "new-leader" } });
    fireEvent.change(screen.getByLabelText("新主揪密碼"), { target: { value: "new-password-123" } });
    fireEvent.click(screen.getByRole("button", { name: "建立主揪" }));
    await screen.findByText("已新增主揪 new-leader。");
    expect(screen.getByLabelText("新主揪密碼")).toHaveValue("");
    expect(fetchMock).toHaveBeenCalledWith("/api/auth/accounts", expect.objectContaining({
      method: "POST", body: JSON.stringify({ username: "new-leader", password: "new-password-123" }),
    }));
  });

  it("shows a connection error without mounting protected content", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    render(<AdminAccess><ProtectedState /></AdminAccess>);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("無法確認登入狀態"));
    expect(screen.queryByLabelText("管理草稿")).not.toBeInTheDocument();
  });

  it("supports QR code login by requesting ticket, polling, and claiming directly side-by-side", async () => {
    let polled = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (input === "/api/auth/me") return response({}, 401);
      if (input === "/api/auth/qr/ticket") {
        return response({
          ticket: "ticket-123",
          expires_at: Date.now() + 300000,
          auth_url: "http://localhost/admin?auth_ticket=ticket-123",
        });
      }
      if (typeof input === "string" && input.startsWith("/api/auth/qr/status?ticket=ticket-123")) {
        polled++;
        return response({ status: polled >= 2 ? "authorized" : "pending", username: "organizer" });
      }
      if (input === "/api/auth/qr/claim") {
        return response(leader);
      }
      throw new Error("Unexpected fetch: " + String(input));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AdminAccess><ProtectedState /></AdminAccess>);
    await screen.findByRole("heading", { name: "主揪登入" });

    // Both password inputs and QR code are visible side-by-side without clicking any tab
    expect(screen.getByLabelText("Nidin 手機號碼")).toBeInTheDocument();
    expect(screen.getByLabelText("Nidin 密碼")).toBeInTheDocument();
    await screen.findByAltText("主揪登入 QR 碼");

    // 每 2 秒輪詢一次、第 2 次才授權，至少要 4 秒；留足餘裕以免機器忙時誤判失敗
    await waitFor(() => expect(screen.getByText("已登入：organizer")).toBeInTheDocument(), { timeout: 8000 });
  }, 12000);

  it("turns a scanning phone into a dedicated approve screen instead of another login page with its own QR", async () => {
    window.history.pushState({}, "", "/admin?auth_ticket=ticket-from-pc");
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (input === "/api/auth/me") return response({}, 401);
      if (input === "/api/auth/login") return response(leader);
      if (input === "/api/auth/qr/authorize") return response({ ok: true });
      throw new Error("Unexpected fetch: " + String(input));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AdminAccess><ProtectedState /></AdminAccess>);
    await screen.findByRole("heading", { name: "📱 授權電腦登入" });
    expect(screen.queryByAltText("主揪登入 QR 碼")).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalledWith("/api/auth/qr/ticket", expect.anything());

    fireEvent.click(screen.getByRole("button", { name: "本站帳號" }));
    fireEvent.change(screen.getByLabelText("主揪帳號"), { target: { value: "organizer" } });
    fireEvent.change(screen.getByLabelText("密碼"), { target: { value: "password-123456" } });
    fireEvent.click(screen.getByRole("button", { name: "登入" }));

    await screen.findByRole("heading", { name: "📱 要讓電腦登入嗎？" });
    expect(screen.queryByLabelText("管理草稿")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "確認，讓電腦登入" }));

    await screen.findByRole("heading", { name: "✓ 電腦已登入" });
    expect(fetchMock).toHaveBeenCalledWith("/api/auth/qr/authorize", expect.objectContaining({
      body: JSON.stringify({ ticket: "ticket-from-pc" }),
    }));
    fireEvent.click(screen.getByRole("button", { name: "在手機上進入後台" }));
    await screen.findByLabelText("管理草稿");
  });

  it("claims login automatically when claim_ticket query param is present", async () => {
    window.history.pushState({}, "", "/admin?claim_ticket=ticket-quick");

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (input === "/api/auth/qr/claim") return response(leader);
      if (input === "/api/auth/me") return response(leader);
      throw new Error("Unexpected fetch: " + String(input));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AdminAccess><ProtectedState /></AdminAccess>);
    await screen.findByText("已登入：organizer");
  });

  it("opens quick login modal for logged in organizers", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (input === "/api/auth/me") return response(leader);
      if (input === "/api/auth/qr/quick-ticket") {
        return response({
          ticket: "quick-456",
          expires_at: Date.now() + 300000,
          auth_url: "http://localhost/admin?claim_ticket=quick-456",
        });
      }
      throw new Error("Unexpected fetch: " + String(input));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AdminAccess><ProtectedState /></AdminAccess>);
    await screen.findByText("已登入：organizer");

    fireEvent.click(screen.getByRole("button", { name: /手機快速登入/ }));
    await screen.findByRole("heading", { name: "手機快速登入 QR 碼" });
    await screen.findByAltText("手機快速登入 QR 碼");
  });
});
