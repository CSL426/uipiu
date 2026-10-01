import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NidinPasswordLogin } from "./NidinPasswordLogin";

function response(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body };
}

function fillAndSubmit() {
  fireEvent.change(screen.getByLabelText("Nidin 手機號碼"), { target: { value: " 0912345678 " } });
  fireEvent.change(screen.getByLabelText("Nidin 密碼"), { target: { value: "secret" } });
  fireEvent.click(screen.getByRole("button", { name: "登入並綁定" }));
}

describe("NidinPasswordLogin", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("logs in with remember-me on by default and hands the bound status back", async () => {
    const bound = { is_authenticated: true, user_id: "777", masked_token: "tok...1234", auto_relogin: true };
    const fetchMock = vi.fn(async () => response(bound));
    vi.stubGlobal("fetch", fetchMock);
    const onBound = vi.fn();

    render(<NidinPasswordLogin leaderAuth={null} onBound={onBound} onClear={vi.fn()} onCancel={vi.fn()} />);
    fillAndSubmit();

    await vi.waitFor(() => expect(onBound).toHaveBeenCalledWith(bound));
    expect(fetchMock).toHaveBeenCalledWith("/api/leader/auth/password", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ account: "0912345678", password: "secret", remember: true }),
    }));
  });

  it("shows Nidin's refusal message and keeps the dialog open", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({ detail: "帳號或密碼錯誤" }, 400)));
    const onBound = vi.fn();

    render(<NidinPasswordLogin leaderAuth={null} onBound={onBound} onClear={vi.fn()} onCancel={vi.fn()} />);
    fillAndSubmit();

    expect(await screen.findByRole("alert")).toHaveTextContent("帳號或密碼錯誤");
    expect(onBound).not.toHaveBeenCalled();
  });
});
