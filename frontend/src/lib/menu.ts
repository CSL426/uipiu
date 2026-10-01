import type {
  AgentParsedItemPreview,
  CartLine,
  MenuGroup,
  MenuItem,
  MenuOption,
  NormalizedMenu,
  SelectedOption,
  SharedCartLine,
} from "../types";

export function createLineId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2);
}

export function formatMoney(value: number): string {
  return `NT$${value}`;
}

export function getLineTotal(line: CartLine): number {
  const selectionTotal = line.selections.reduce((sum, selection) => sum + selection.price, 0);
  return (line.item.price + selectionTotal) * line.quantity;
}

export function describeCartLine(line: CartLine): string {
  const parts = [`${line.quantity} 杯`, line.item.name];
  if (line.selections.length > 0) {
    parts.push(line.selections.map((selection) => selection.optionName).join(" / "));
  }
  return parts.join(" · ");
}

export function describeSharedLine(line: SharedCartLine): string {
  const parts = [`${line.contributor_name}`, `${line.quantity} 杯`, line.item_name];
  if (line.selections.length > 0) {
    parts.push(line.selections.map((selection) => selection.option_name).join(" / "));
  }
  return parts.join(" · ");
}

const CORE_DIMENSION_KEYWORDS = [
  "溫度", "冰塊", "冰量", "冷熱", "飲料溫度", "冰度",
  "甜度", "糖度", "甜度選擇", "糖量",
  "尺寸", "大小", "份量", "杯型", "份量選擇", "容量",
];

export function isCoreDrinkDimension(groupName: string): boolean {
  const lower = groupName.toLowerCase();
  return CORE_DIMENSION_KEYWORDS.some((k) => lower.includes(k));
}

export function getEffectiveMinSelect(group: MenuGroup): number {
  if (group.min_select > 0) {
    return group.min_select;
  }
  if (!isCoreDrinkDimension(group.name) || group.options.length <= 1) {
    return 0;
  }
  if (group.options.every((option) => option.name.includes("固定"))) {
    return 0;
  }

  // 多個可選甜度／冰塊／尺寸必選 1 項，避免未選時套用店家預設。
  return 1;
}

export function createDefaultSelections(groups: MenuGroup[]): Record<string, string[]> {
  return Object.fromEntries(
    groups.map((group) => {
      // 1. 核心手搖維度（如溫度、尺寸）若只有 1 個選項（例如：固定冰量、只有 M 杯），自動選取
      if (isCoreDrinkDimension(group.name) && group.options.length === 1) {
        return [group.id, [group.options[0].id]];
      }
      // 2. 原本 min_select > 0 的群組（如店家本身強制必選），預設選前 N 項
      if (group.min_select > 0) {
        return [group.id, group.options.slice(0, group.min_select).map((option) => option.id)];
      }
      return [group.id, []];
    }),
  );
}

export function findSelectedOptions(
  item: MenuItem,
  groups: MenuGroup[],
  selections: Record<string, string[]>,
): SelectedOption[] {
  const groupMap = new Map(groups.map((group) => [group.id, group]));
  return item.group_ids.flatMap((groupId) => {
    const group = groupMap.get(groupId);
    if (!group) {
      return [];
    }

    return (selections[groupId] ?? [])
      .map((optionId) => group.options.find((option) => option.id === optionId))
      .filter((option): option is MenuOption => Boolean(option))
      .map((option) => ({
        groupId,
        groupName: group.name,
        optionId: option.id,
        optionName: option.name,
        price: option.price,
      }));
  });
}

export function validateSelections(item: MenuItem, groups: MenuGroup[], selections: Record<string, string[]>): string | null {
  for (const groupId of item.group_ids) {
    const group = groups.find((candidate) => candidate.id === groupId);
    if (!group) {
      continue;
    }

    const effectiveMin = getEffectiveMinSelect(group);
    const selectedCount = (selections[group.id] ?? []).length;
    if (selectedCount < effectiveMin) {
      return `請選擇 ${group.name}`;
    }
  }

  return null;
}

export function resolveItemPreview(
  menu: NormalizedMenu,
  itemId: string,
  quantity: number,
  selectionOptionIds: string[],
): AgentParsedItemPreview | null {
  const item = menu.items.find((m) => m.id === itemId);
  if (!item) return null;

  const itemGroups = item.group_ids
    .map((gid) => menu.groups.find((g) => g.id === gid))
    .filter((g): g is MenuGroup => Boolean(g));

  const validOptions: MenuOption[] = [];
  const validOptionNames: string[] = [];
  const validOptionIds: string[] = [];

  for (const group of itemGroups) {
    for (const opt of group.options) {
      if (selectionOptionIds.includes(opt.id)) {
        validOptions.push(opt);
        validOptionNames.push(opt.name);
        validOptionIds.push(opt.id);
      }
    }
  }

  const extraTotal = validOptions.reduce((sum, opt) => sum + opt.price, 0);
  const unitPrice = item.price + extraTotal;
  const lineTotal = unitPrice * Math.max(1, quantity);

  return {
    item_id: item.id,
    item_name: item.name,
    quantity: Math.max(1, quantity),
    selection_option_ids: validOptionIds,
    selection_option_names: validOptionNames,
    unit_price: unitPrice,
    line_total: lineTotal,
  };
}
