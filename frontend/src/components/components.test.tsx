import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { parseQrCredentials, parseQrFromImageFile } from "../lib/qr";
import type { NormalizedMenu } from "../types";
import { ChatWidget } from "./ChatWidget";
import { CustomSelect } from "./CustomSelect";
import { ItemComposerModal } from "./ItemComposerModal";
import { LeaderAuthModal } from "./LeaderAuthModal";

vi.mock("../lib/speech", () => ({
  speak: vi.fn(),
  stopSpeaking: vi.fn(),
  toSpeechText: vi.fn((text) => text),
}));
vi.mock("../lib/qr", () => ({
  parseQrFromImageFile: vi.fn(),
  parseQrCredentials: vi.fn(),
  generateQrDataUrl: vi.fn(),
  getNidinBookmarkletCode: vi.fn(),
}));

const menu: NormalizedMenu = {
  store_id: "store-1",
  store_name: "測試店",
  categories: [],
  items: [{ id: "tea", name: "紅茶", price: 30, aliases: [], group_ids: ["sweet"] }],
  groups: [{
    id: "sweet", name: "甜度", min_select: 1, max_select: 1,
    options: [
      { id: "normal", name: "正常甜", price: 0, aliases: [] },
      { id: "half", name: "半糖", price: 0, aliases: [] },
    ],
  }],
};

const preview = {
  action: "preview", reply_message: "請確認點單", total_price: 30,
  contributor_name: "小明", items_added: 0,
  parsed_items: [{
    item_id: "tea", item_name: "紅茶", quantity: 1,
    selection_option_ids: ["normal"], selection_option_names: ["正常甜"],
    unit_price: 30, line_total: 30,
  }],
};

function renderChat(onSubmitOrder = vi.fn().mockResolvedValue({ session_id: "session-1", lines: [] })) {
  const setStatusMessage = vi.fn();
  render(<ChatWidget selectedSessionId="session-1" contributorName="小明" menu={menu}
    onSubmitOrder={onSubmitOrder} setStatusMessage={setStatusMessage} />);
  fireEvent.change(screen.getByPlaceholderText(/告訴我想點什麼/), { target: { value: "+1 紅茶" } });
  fireEvent.click(screen.getByTitle("送出點單解析"));
  return { onSubmitOrder, setStatusMessage };
}

function renderAuth() {
  const props = {
    showAuthModal: true, leaderAuth: null, setLeaderAuth: vi.fn(),
    setShowAuthModal: vi.fn(), setStatusMessage: vi.fn(),
  };
  return { ...render(<LeaderAuthModal {...props} />), ...props };
}

describe("extracted component behavior", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => vi.unstubAllGlobals());

  it("selects a custom option and closes the menu after selection or an outside click", async () => {
    const onChange = vi.fn();
    render(<CustomSelect value="tea" onChange={onChange} options={[
      { value: "tea", label: "茶類" }, { value: "milk", label: "奶類" },
    ]} />);
    const trigger = screen.getByRole("button", { name: "茶類" });
    fireEvent.click(trigger);
    fireEvent.click(within(screen.getByRole("listbox")).getByRole("option", { name: "奶類" }));
    expect(onChange).toHaveBeenCalledWith("milk");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    await waitFor(() => expect(screen.queryByRole("listbox")).not.toBeInTheDocument());
    fireEvent.click(trigger);
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    fireEvent.mouseDown(document.body);
    await waitFor(() => expect(screen.queryByRole("listbox")).not.toBeInTheDocument());
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("does not open or change a disabled custom select", () => {
    const onChange = vi.fn();
    render(<CustomSelect value="tea" onChange={onChange} disabled
      ariaLabel="分類" options={[{ value: "tea", label: "茶類" }]} />);
    fireEvent.click(screen.getByRole("button", { name: "茶類" }));
    expect(screen.getByLabelText("分類")).toBeDisabled();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("previews a chat order, then sends the edited quantity and sweetness to Nidin", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => preview });
    vi.stubGlobal("fetch", fetchMock);
    const { onSubmitOrder } = renderChat();
    await screen.findByRole("button", { name: "確認送出" });
    expect(onSubmitOrder).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledWith("/api/cart-sessions/session-1/agent-order", expect.objectContaining({
      method: "POST", body: JSON.stringify({ text: "+1 紅茶", contributor_name: "小明", dry_run: true }),
    }));
    fireEvent.click(screen.getByRole("button", { name: "增加杯數" }));
    fireEvent.click(screen.getByTitle("點擊調整甜度"));
    fireEvent.click(screen.getByRole("button", { name: "半糖" }));
    expect(screen.getByText("共 2 杯")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "確認送出" }));
    await screen.findByText("已送到 Nidin");
    expect(onSubmitOrder).toHaveBeenCalledTimes(1);
    const [submissionId, name, lines] = onSubmitOrder.mock.calls[0];
    expect(submissionId).toMatch(/^chat-[A-Za-z0-9_-]+$/);
    expect(name).toBe("小明");
    expect(lines).toEqual([{ item_id: "tea", quantity: 2, selection_option_ids: ["half"] }]);
    expect(screen.getByRole("button", { name: "增加杯數" })).toBeDisabled();
  });

  it("keeps a failed send editable and retries with the same submission id", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce({ ok: true, json: async () => preview }));
    const onSubmitOrder = vi.fn()
      .mockRejectedValueOnce(new Error("這個團在 Nidin 上已經截止，不能再點了。"))
      .mockResolvedValueOnce({ session_id: "session-1", lines: [] });
    const { setStatusMessage } = renderChat(onSubmitOrder);
    fireEvent.click(await screen.findByRole("button", { name: "確認送出" }));
    await waitFor(() => expect(setStatusMessage).toHaveBeenLastCalledWith("這個團在 Nidin 上已經截止，不能再點了。"));
    expect(screen.getByText(/⚠️ 這個團在 Nidin 上已經截止/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "增加杯數" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "確認送出" }));
    await screen.findByText("已送到 Nidin");
    expect(onSubmitOrder.mock.calls[1][0]).toBe(onSubmitOrder.mock.calls[0][0]);
  });

  it("uses the name field at confirm time and a new submission id when the content changed", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce({ ok: true, json: async () => ({ ...preview, contributor_name: "訪客" }) }));
    const onSubmitOrder = vi.fn()
      .mockRejectedValueOnce(new Error("暫時失敗"))
      .mockResolvedValueOnce({ session_id: "session-1", lines: [] });
    const setStatusMessage = vi.fn();
    render(<ChatWidget selectedSessionId="session-1" contributorName="阿美" menu={menu}
      onSubmitOrder={onSubmitOrder} setStatusMessage={setStatusMessage} />);
    fireEvent.change(screen.getByPlaceholderText(/告訴我想點什麼/), { target: { value: "+1 紅茶" } });
    fireEvent.click(screen.getByTitle("送出點單解析"));
    fireEvent.click(await screen.findByRole("button", { name: "確認送出" }));
    await waitFor(() => expect(onSubmitOrder).toHaveBeenCalledTimes(1));
    expect(onSubmitOrder.mock.calls[0][1]).toBe("阿美");

    fireEvent.click(screen.getByRole("button", { name: "增加杯數" }));
    fireEvent.click(screen.getByRole("button", { name: "確認送出" }));
    await screen.findByText("已送到 Nidin");
    expect(onSubmitOrder.mock.calls[1][0]).not.toBe(onSubmitOrder.mock.calls[0][0]);
  });

  it("locks a cancelled preview so it can no longer be sent", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce({ ok: true, json: async () => preview }));
    const { onSubmitOrder } = renderChat();
    fireEvent.click(await screen.findByRole("button", { name: "取消" }));
    expect(await screen.findByText("已取消")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "確認送出" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "增加杯數" })).toBeDisabled();
    expect(onSubmitOrder).not.toHaveBeenCalled();
  });

  it.each(["manual", "qr"])("saves %s credentials and forwards authentication and status to the parent", async (source) => {
    const auth = { is_authenticated: true, user_id: "123", masked_token: "test***" };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => auth });
    vi.stubGlobal("fetch", fetchMock);
    const { container, setLeaderAuth, setShowAuthModal, setStatusMessage } = renderAuth();
    if (source === "qr") {
      vi.mocked(parseQrFromImageFile).mockResolvedValue("credential-payload");
      vi.mocked(parseQrCredentials).mockReturnValue({ token: "test-token", userId: "123" });
      fireEvent.click(screen.getByRole("button", { name: "掃碼/匯入" }));
      const file = new File(["qr"], "credentials.png", { type: "image/png" });
      fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [file] } });
      await screen.findByText("✓ 成功從 QR 碼讀取憑證！已為您自動填入。");
      expect(parseQrFromImageFile).toHaveBeenCalledWith(file);
      expect(parseQrCredentials).toHaveBeenCalledWith("credential-payload");
    } else {
      fireEvent.click(screen.getByRole("button", { name: "手動輸入" }));
      fireEvent.change(screen.getByLabelText("MC-API-Token (必填)"), { target: { value: "test-token" } });
      fireEvent.change(screen.getByLabelText("MC-API-User (選填)"), { target: { value: "123" } });
    }
    expect(screen.getByLabelText("MC-API-Token (必填)")).toHaveValue("test-token");
    fireEvent.click(screen.getByRole("button", { name: "儲存憑證" }));
    await waitFor(() => expect(setLeaderAuth).toHaveBeenCalledWith(auth));
    expect(fetchMock).toHaveBeenCalledWith("/api/leader/auth", expect.objectContaining({
      method: "POST", body: JSON.stringify({ token: "test-token", user_id: "123" }),
    }));
    expect(setShowAuthModal).toHaveBeenCalledWith(false);
    expect(setStatusMessage).toHaveBeenCalledWith("團長憑證已更新成功！");
  });

  it("resets discarded composer choices and quantity when reopened, then adds defaults", () => {
    const props = { activeItem: menu.items[0], menu, closeComposer: vi.fn(), onAddLine: vi.fn(), setStatusMessage: vi.fn() };
    const { rerender } = render(<ItemComposerModal key="first-open" {...props} />);
    fireEvent.click(screen.getByRole("button", { name: /半糖/ }));
    fireEvent.click(screen.getByRole("button", { name: "增加數量" }));
    expect(screen.getByLabelText("數量")).toHaveValue(2);
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(props.closeComposer).toHaveBeenCalledTimes(1);
    expect(props.onAddLine).not.toHaveBeenCalled();
    rerender(<ItemComposerModal key="second-open" {...props} />);
    expect(screen.getByLabelText("數量")).toHaveValue(1);
    fireEvent.click(screen.getByRole("button", { name: "加入購物車" }));
    expect(props.onAddLine).toHaveBeenCalledWith(expect.objectContaining({
      item: menu.items[0], quantity: 1,
      selections: [expect.objectContaining({ optionId: "normal", optionName: "正常甜" })],
    }));
    expect(props.closeComposer).toHaveBeenCalledTimes(2);
  });
});
