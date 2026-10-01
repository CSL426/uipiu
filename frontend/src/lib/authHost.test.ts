import { describe, expect, it } from "vitest";

import { isLoopbackHostname, resolveTicketUrl } from "./authHost";

describe("isLoopbackHostname", () => {
  it("認得 localhost 與 127.0.0.1", () => {
    expect(isLoopbackHostname("localhost")).toBe(true);
    expect(isLoopbackHostname("127.0.0.1")).toBe(true);
    expect(isLoopbackHostname("192.168.1.100")).toBe(false);
  });
});

describe("resolveTicketUrl", () => {
  it("localhost 網址換成區網位址", () => {
    expect(
      resolveTicketUrl("T1", "http://localhost:6464/admin?auth_ticket=T1", "192.168.1.100:6464", "auth_ticket"),
    ).toBe("http://192.168.1.100:6464/admin?auth_ticket=T1");
  });

  it("已經是對外網址就原封不動", () => {
    const external = "https://nidin.example.com/admin?auth_ticket=T1";
    expect(resolveTicketUrl("T1", external, "192.168.1.100:6464", "auth_ticket")).toBe(external);
  });

  it("沒設定位址時原封不動", () => {
    const original = "http://localhost:6464/admin?auth_ticket=T1";
    expect(resolveTicketUrl("T1", original, "", "auth_ticket")).toBe(original);
  });

  it("快速登入用 claim_ticket 參數", () => {
    expect(
      resolveTicketUrl("T2", "http://localhost:6464/admin?claim_ticket=T2", "192.168.1.100:6464", "claim_ticket"),
    ).toBe("http://192.168.1.100:6464/admin?claim_ticket=T2");
  });

  it("網址解析失敗時退回用目前的 protocol 組出來", () => {
    const result = resolveTicketUrl("T3", "not-a-url", "192.168.1.100:6464", "auth_ticket");
    expect(result).toContain("192.168.1.100:6464/admin?auth_ticket=T3");
  });
});
