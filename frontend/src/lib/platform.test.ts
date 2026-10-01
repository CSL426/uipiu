import { describe, expect, it } from "vitest";

import { acceptsPhone, platformLabel } from "./platform";

describe("platform", () => {
  it("treats sessions without a provider as Nidin", () => {
    expect(platformLabel(undefined)).toBe("Nidin");
    expect(acceptsPhone(undefined)).toBe(true);
  });

  it("names Ocard and drops the phone field there", () => {
    expect(platformLabel("ocard")).toBe("Ocard");
    expect(acceptsPhone("ocard")).toBe(false);
  });
});
