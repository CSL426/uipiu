import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatWidget } from "./ChatWidget";

describe("ChatWidget scrolling", () => {
  const scrollIntoView = vi.fn();
  const scrollTo = vi.fn();

  beforeEach(() => {
    Element.prototype.scrollIntoView = scrollIntoView;
    HTMLElement.prototype.scrollTo = scrollTo as unknown as typeof HTMLElement.prototype.scrollTo;
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ action: "reply", reply_message: "請問要什麼甜度？", items_added: 0 }),
    })));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    scrollIntoView.mockReset();
    scrollTo.mockReset();
  });

  it("scrolls only its own message list when a message arrives, never the whole page", async () => {
    render(
      <ChatWidget selectedSessionId="s1" contributorName="小明" menu={null} onSubmitOrder={vi.fn()} setStatusMessage={vi.fn()} />,
    );
    scrollTo.mockClear();

    fireEvent.change(screen.getByPlaceholderText(/告訴我想點什麼/), { target: { value: "小明 +1 紅茶" } });
    fireEvent.keyDown(screen.getByPlaceholderText(/告訴我想點什麼/), { key: "Enter" });
    await screen.findByText("請問要什麼甜度？");

    expect(scrollIntoView).not.toHaveBeenCalled();
    expect(scrollTo).toHaveBeenCalled();
    const target = scrollTo.mock.contexts[scrollTo.mock.contexts.length - 1] as HTMLElement;
    expect(target.classList.contains("chat-messages-container")).toBe(true);
  });
});
