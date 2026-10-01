import { describe, expect, it } from "vitest";
import { isOriginAllowed, isSenderAllowed } from "../src/background";
import { PROTOCOL_VERSION } from "../src/types";

describe("Extension Security & Origin Verification", () => {
  it("enforces protocol version 1", () => {
    expect(PROTOCOL_VERSION).toBe(1);
  });

  it("validates allowed origins correctly", () => {
    expect(isOriginAllowed("http://localhost:6464")).toBe(true);
    expect(isOriginAllowed("http://127.0.0.1:6464")).toBe(true);
    expect(isOriginAllowed("http://localhost:5173")).toBe(true);
    expect(isOriginAllowed("http://127.0.0.1:8000")).toBe(true);

    expect(isOriginAllowed("http://evil.com")).toBe(false);
    expect(isOriginAllowed("https://attacker.org:6464")).toBe(false);
    expect(isOriginAllowed("invalid-url")).toBe(false);
  });

  it("rejects localhost on a port that is not on the allow-list", () => {
    // manifest 的 externally_connectable 無法限制 port，
    // 所以這層必須自己比對完整 origin，否則本機任何服務都能連進來
    expect(isOriginAllowed("http://localhost:9999")).toBe(false);
    expect(isOriginAllowed("http://127.0.0.1:1337")).toBe(false);
    // 沒寫 port 等於 80，也不在清單上
    expect(isOriginAllowed("http://localhost")).toBe(false);
    // scheme 也要比對，不能只看 host:port
    expect(isOriginAllowed("https://localhost:6464")).toBe(false);
  });

  it("verifies sender constraints (frame, origin, admin path)", () => {
    // 合法 sender
    expect(
      isSenderAllowed({
        origin: "http://localhost:6464",
        url: "http://localhost:6464/admin",
        frameId: 0,
      } as chrome.runtime.MessageSender)
    ).toBe(true);

    // 缺少 origin -> 拒絕
    expect(
      isSenderAllowed({
        url: "http://localhost:6464/admin",
        frameId: 0,
      } as chrome.runtime.MessageSender)
    ).toBe(false);

    // 非頂層 frame (例如 iframe 嵌入) -> 拒絕
    expect(
      isSenderAllowed({
        origin: "http://localhost:6464",
        url: "http://localhost:6464/admin",
        frameId: 1,
      } as chrome.runtime.MessageSender)
    ).toBe(false);

    // 非後台路徑 -> 拒絕
    expect(
      isSenderAllowed({
        origin: "http://localhost:6464",
        url: "http://localhost:6464/public-page",
        frameId: 0,
      } as chrome.runtime.MessageSender)
    ).toBe(false);

    // 惡意網站 -> 拒絕
    expect(
      isSenderAllowed({
        origin: "http://malicious.com",
        url: "http://malicious.com/admin",
        frameId: 0,
      } as chrome.runtime.MessageSender)
    ).toBe(false);
  });
});
