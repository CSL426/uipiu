import { motion } from "framer-motion";
import { Minus, Plus } from "lucide-react";
import { useMemo, useState } from "react";
import {
  createDefaultSelections,
  createLineId,
  findSelectedOptions,
  formatMoney,
  getEffectiveMinSelect,
  isCoreDrinkDimension,
  validateSelections,
} from "../lib/menu";
import type { CartLine, MenuGroup, MenuItem, MenuOption, NormalizedMenu } from "../types";

interface ItemComposerModalProps {
  activeItem: MenuItem;
  menu: NormalizedMenu;
  closeComposer: () => void;
  onAddLine: (line: CartLine) => void;
  setStatusMessage: (message: string) => void;
}

export function ItemComposerModal({ activeItem, menu, closeComposer, onAddLine, setStatusMessage }: ItemComposerModalProps) {
  const activeGroups = useMemo(() => {
    return activeItem.group_ids
      .map((groupId) => menu.groups.find((group) => group.id === groupId))
      .filter((group): group is MenuGroup => Boolean(group));
  }, [activeItem, menu]);

  const [draftSelections, setDraftSelections] = useState<Record<string, string[]>>(
    () => createDefaultSelections(activeGroups),
  );
  const [draftQuantity, setDraftQuantity] = useState(1);

  const { fixedGroups, selectableGroups } = useMemo(() => {
    if (!menu || !activeItem) {
      return { fixedGroups: [] as MenuGroup[], selectableGroups: [] as MenuGroup[] };
    }
    const fixed: MenuGroup[] = [];
    const selectable: MenuGroup[] = [];

    for (const group of activeGroups) {
      const isFixed =
        (isCoreDrinkDimension(group.name) && group.options.length === 1) ||
        (group.options.length === 1 && group.min_select >= 1) ||
        (group.options.length > 0 && group.options.every((o) => o.name.includes("固定")));

      if (isFixed) {
        fixed.push(group);
      } else {
        selectable.push(group);
      }
    }
    return { fixedGroups: fixed, selectableGroups: selectable };
  }, [activeGroups, activeItem, menu]);

  const activeItemOptionSubtotal = useMemo(() => {
    if (!activeItem || !menu) return 0;
    const selectedOpts = findSelectedOptions(activeItem, activeGroups, draftSelections);
    return selectedOpts.reduce((sum, opt) => sum + opt.price, 0);
  }, [activeItem, activeGroups, draftSelections, menu]);

  const activeItemUnitPrice = (activeItem?.price ?? 0) + activeItemOptionSubtotal;
  const activeItemTotalPrice = activeItemUnitPrice * draftQuantity;

  function toggleOption(group: MenuGroup, option: MenuOption) {
    setDraftSelections((current) => {
      const existing = current[group.id] ?? [];
      const exists = existing.includes(option.id);

      if (group.max_select === 1) {
        return {
          ...current,
          [group.id]: exists ? [] : [option.id],
        };
      }

      if (exists) {
        return {
          ...current,
          [group.id]: existing.filter((optionId) => optionId !== option.id),
        };
      }

      if (existing.length >= group.max_select) {
        return {
          ...current,
          [group.id]: [...existing.slice(1), option.id],
        };
      }

      return {
        ...current,
        [group.id]: [...existing, option.id],
      };
    });
  }

  function addActiveItemToCart() {
    if (!activeItem) {
      return;
    }

    const validationError = validateSelections(activeItem, activeGroups, draftSelections);
    if (validationError) {
      setStatusMessage(validationError);
      return;
    }

    const line: CartLine = {
      id: createLineId(),
      item: activeItem,
      quantity: draftQuantity,
      selections: findSelectedOptions(activeItem, activeGroups, draftSelections),
    };

    onAddLine(line);
    setStatusMessage(`${activeItem.name} 已加入暫存購物車。`);
    closeComposer();
  }

  return (
    <motion.section
      className="composer-backdrop"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      onClick={closeComposer}
    >
      <motion.div
        className="composer"
        initial={{ opacity: 0, y: 36, scale: 0.96 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 24, scale: 0.98 }}
        transition={{ duration: 0.22 }}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="panel-header panel-header-space">
          <div className="composer-heading">
            <p className="eyebrow">選擇口味</p>
            <h3>{activeItem.name}</h3>
          </div>
          <strong>{formatMoney(activeItem.price)}</strong>
        </div>

        {activeItem.image_url ? (
          <div className="composer-hero">
            <img src={activeItem.image_url} alt={activeItem.name} loading="lazy" />
          </div>
        ) : null}

        <div className="field-quantity-row">
          <span style={{ fontWeight: 600 }}>點餐數量</span>
          <div className="quantity-stepper">
            <button
              type="button"
              className="stepper-btn"
              onClick={() => setDraftQuantity((q) => Math.max(1, q - 1))}
              disabled={draftQuantity <= 1}
              aria-label="減少數量"
            >
              <Minus size={14} />
            </button>
            <input
              aria-label="數量"
              type="number"
              min={1}
              max={50}
              value={draftQuantity}
              onChange={(event) => setDraftQuantity(Math.max(1, Number(event.target.value) || 1))}
              style={{ width: "2.5rem", textAlign: "center", border: 0, background: "transparent", fontWeight: 700 }}
            />
            <button
              type="button"
              className="stepper-btn"
              onClick={() => setDraftQuantity((q) => Math.min(50, q + 1))}
              aria-label="增加數量"
            >
              <Plus size={14} />
            </button>
          </div>
        </div>

        {fixedGroups.length > 0 ? (
          <div className="composer-fixed-banner">
            <span className="fixed-badge-title">固定規格</span>
            <div className="fixed-badge-list">
              {fixedGroups.map((group) => (
                <span key={group.id} className="fixed-badge-item">
                  {group.name}：{group.options[0]?.name ?? "固定"}
                </span>
              ))}
            </div>
          </div>
        ) : null}

        <div className="group-stack">
          {selectableGroups.map((group) => {
            const sameNameGroups = selectableGroups.filter((g) => g.name === group.name);
            const isDuplicateName = sameNameGroups.length > 1;
            const duplicateIndex = sameNameGroups.findIndex((g) => g.id === group.id) + 1;
            const displayName = isDuplicateName ? `${group.name} (${duplicateIndex})` : group.name;
            const effectiveMinSelect = getEffectiveMinSelect(group);

            return (
              <section key={group.id} className="group-card">
                <div className="panel-header panel-header-space">
                  <div>
                    <h4>{displayName}</h4>
                    <p>
                      {effectiveMinSelect > 0
                        ? `必選 ${effectiveMinSelect} 項`
                        : "可不選"}
                      {" · "}
                      最多 {group.max_select} 項
                    </p>
                  </div>
                </div>
                <div className="option-grid">
                  {group.options.map((option) => {
                    const selected = (draftSelections[group.id] ?? []).includes(option.id);
                    return (
                      <button
                        key={option.id}
                        type="button"
                        className={selected ? "option-chip option-chip-active" : "option-chip"}
                        onClick={() => toggleOption(group, option)}
                      >
                        <span>{option.name}</span>
                        <span>{option.price > 0 ? `+${formatMoney(option.price)}` : "不加價"}</span>
                      </button>
                    );
                  })}
                </div>
              </section>
            );
          })}
        </div>

        <div className="composer-actions" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontSize: "1.05rem", fontWeight: 700, color: "var(--accent-deep)" }}>
            小計：{formatMoney(activeItemTotalPrice)}
          </span>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button className="ghost-button ghost-button-text" onClick={closeComposer}>
              取消
            </button>
            <button className="submit-button" onClick={addActiveItemToCart}>
              加入購物車
            </button>
          </div>
        </div>
      </motion.div>
    </motion.section>
  );
}
