import { describe, expect, it } from "vitest";
import { getNidinBookmarkletCode, parseQrCredentials } from "./qr";

describe("qr lib", () => {
  it("parses valid JSON credentials with various key names", () => {
    const json1 = JSON.stringify({ token: "token-123", userId: "user-456" });
    expect(parseQrCredentials(json1)).toEqual({ token: "token-123", userId: "user-456" });

    const json2 = JSON.stringify({ Nidin_token: "token-abc", Nidin_userId: "user-def" });
    expect(parseQrCredentials(json2)).toEqual({ token: "token-abc", userId: "user-def" });

    const json3 = JSON.stringify({ mc_api_token: "token-xyz", mc_api_user: "999" });
    expect(parseQrCredentials(json3)).toEqual({ token: "token-xyz", userId: "999" });
  });

  it("parses query string credentials", () => {
    const query = "?Nidin_token=token-query&Nidin_userId=user-query";
    expect(parseQrCredentials(query)).toEqual({ token: "token-query", userId: "user-query" });
  });

  it("parses raw JWT token string", () => {
    const jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.doNotReal";
    expect(parseQrCredentials(jwt)).toEqual({ token: jwt, userId: "" });
  });

  it("returns null for invalid or empty text", () => {
    expect(parseQrCredentials("")).toBeNull();
    expect(parseQrCredentials("   ")).toBeNull();
    expect(parseQrCredentials("hello world")).toBeNull();
  });

  it("generates bookmarklet javascript code", () => {
    const bookmarklet = getNidinBookmarkletCode();
    expect(bookmarklet.startsWith("javascript:(function()")).toBe(true);
    expect(bookmarklet.includes("Nidin_token")).toBe(true);
  });
});
