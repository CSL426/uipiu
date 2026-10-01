export interface GroupBuyTarget {
  storeId: string;
  groupBuyCode: string;
}

export interface OrderLineDraft {
  quantity: number;
  itemName: string;
  selections: string[];
}

const GROUP_BUY_PATTERN = /\/gb\/(?:menu|login)\/(?<storeId>\d+)\/(?<groupBuyCode>[A-Za-z0-9]+)/;

export function extractGroupBuyTarget(value: string): GroupBuyTarget {
  const match = value.trim().match(GROUP_BUY_PATTERN);
  if (!match?.groups) {
    throw new Error("只支援 Nidin 訪客團購連結");
  }

  return {
    storeId: match.groups.storeId,
    groupBuyCode: match.groups.groupBuyCode,
  };
}

export function buildOrderText(lines: OrderLineDraft[]): string {
  return lines
    .map((line) => {
      const parts = [`+${line.quantity}`, line.itemName, ...line.selections.filter(Boolean)];
      return parts.join(" ");
    })
    .join("\n");
}
