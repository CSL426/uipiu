import { describe, expect, it } from "vitest";

import { buildOrderText, extractGroupBuyTarget } from "./order";

describe("extractGroupBuyTarget", () => {
  it("parses guest group-buy URLs", () => {
    expect(
      extractGroupBuyTarget("https://order.nidin.shop/gb/menu/1717/TESTCODE01"),
    ).toEqual({
      groupBuyCode: "TESTCODE01",
      storeId: "1717",
    });
  });
});

describe("buildOrderText", () => {
  it("serializes multiple cart lines with selected options", () => {
    expect(
      buildOrderText([
        {
          quantity: 1,
          itemName: "中●阿薩姆紅茶",
          selections: ["正常冰", "正常甜", "珍珠"],
        },
        {
          quantity: 2,
          itemName: "中●奶茶",
          selections: ["正常冰", "半糖"],
        },
      ]),
    ).toBe("+1 中●阿薩姆紅茶 正常冰 正常甜 珍珠\n+2 中●奶茶 正常冰 半糖");
  });
});
