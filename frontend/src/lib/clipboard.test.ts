import { afterEach, describe, expect, it, vi } from "vitest";

import { copyText } from "./clipboard";

describe("copyText", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("falls back to execCommand when the page is served over plain http on a LAN IP", async () => {
    vi.stubGlobal("navigator", { ...navigator, clipboard: undefined });
    let copied = "";
    document.execCommand = vi.fn(() => {
      copied = document.querySelector("textarea")?.value ?? "";
      return true;
    });

    await expect(copyText("http://192.168.1.100:6464/admin?auth_ticket=abc")).resolves.toBe(true);
    expect(copied).toBe("http://192.168.1.100:6464/admin?auth_ticket=abc");
    expect(document.querySelector("textarea")).toBeNull();
  });

  it("reports failure so the caller can show the text for manual copying", async () => {
    vi.stubGlobal("navigator", { ...navigator, clipboard: undefined });
    document.execCommand = vi.fn(() => false);

    await expect(copyText("x")).resolves.toBe(false);
  });
});
