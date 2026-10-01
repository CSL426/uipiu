import { createHash } from "crypto";
import { readFileSync } from "fs";
import { resolve } from "path";
import { describe, expect, it } from "vitest";

// Chrome 的 unpacked extension ID：公鑰 DER 的 SHA-256 前 32 個 hex，0-f 對應 a-p
function extensionIdFromKey(base64Key: string): string {
  const hex = createHash("sha256").update(Buffer.from(base64Key, "base64")).digest("hex").slice(0, 32);
  return [...hex].map((c) => String.fromCharCode(97 + parseInt(c, 16))).join("");
}

describe("pinned extension ID", () => {
  it("matches the ID the frontend connects to, so the two cannot silently drift apart", () => {
    const manifest = JSON.parse(readFileSync(resolve(__dirname, "../manifest.json"), "utf8"));
    const frontendSource = readFileSync(resolve(__dirname, "../../frontend/src/lib/nidin-extension.ts"), "utf8");
    const pinned = frontendSource.match(/PINNED_EXTENSION_ID = "([a-p]{32})"/)?.[1];

    expect(manifest.key).toBeTruthy();
    expect(pinned).toBe(extensionIdFromKey(manifest.key));
  });
});
