import { AnimatePresence, motion } from "framer-motion";
import { Check, ChevronDown, Minus, Plus, Search, X } from "lucide-react";
import { useMemo, useState } from "react";
import { createDefaultSelections, formatMoney, resolveItemPreview } from "../lib/menu";
import type { AgentParsedItemPreview, MenuGroup, MenuItem, MenuOption, NormalizedMenu } from "../types";

export function PreviewItemRow({
  item,
  menu,
  onUpdateItem,
  disabled = false,
}: {
  item: AgentParsedItemPreview;
  menu: NormalizedMenu | null;
  onUpdateItem: (updated: AgentParsedItemPreview) => void;
  disabled?: boolean;
}) {
  const [isOpenDrinkPicker, setIsOpenDrinkPicker] = useState(false);
  const [drinkSearch, setDrinkSearch] = useState("");
  const [activeGroupId, setActiveGroupId] = useState<string | null>(null);

  const menuItem = useMemo(() => {
    return menu?.items.find((m) => m.id === item.item_id) ?? null;
  }, [menu, item.item_id]);

  const itemGroups = useMemo(() => {
    if (!menu || !menuItem) return [];
    return menuItem.group_ids
      .map((gid) => menu.groups.find((g) => g.id === gid))
      .filter((g): g is MenuGroup => Boolean(g));
  }, [menu, menuItem]);

  const activeGroup = useMemo(() => {
    if (!activeGroupId) return null;
    return itemGroups.find((g) => g.id === activeGroupId) ?? null;
  }, [itemGroups, activeGroupId]);

  const filteredDrinks = useMemo(() => {
    if (!menu) return [];
    const query = drinkSearch.trim().toLowerCase();
    if (!query) return menu.items;
    return menu.items.filter(
      (m) =>
        m.name.toLowerCase().includes(query) ||
        (m.category_name && m.category_name.toLowerCase().includes(query)) ||
        m.aliases.some((a) => a.toLowerCase().includes(query)),
    );
  }, [menu, drinkSearch]);

  function handleQuantityChange(delta: number) {
    if (disabled) return;
    const newQty = Math.max(1, item.quantity + delta);
    onUpdateItem({
      ...item,
      quantity: newQty,
      line_total: item.unit_price * newQty,
    });
  }

  function handleSelectDrink(newMenuItem: MenuItem) {
    if (disabled || !menu) return;
    const newGroups = newMenuItem.group_ids
      .map((gid) => menu.groups.find((g) => g.id === gid))
      .filter((g): g is MenuGroup => Boolean(g));

    const defaults = createDefaultSelections(newGroups);
    const oldNames = new Set(item.selection_option_names);
    const selectedIds: string[] = [];

    for (const group of newGroups) {
      const matchingOpt = group.options.find((opt) => oldNames.has(opt.name));
      if (matchingOpt) {
        selectedIds.push(matchingOpt.id);
      } else {
        const defs = defaults[group.id] ?? [];
        selectedIds.push(...defs);
      }
    }

    const resolved = resolveItemPreview(menu, newMenuItem.id, item.quantity, selectedIds);
    if (resolved) {
      onUpdateItem(resolved);
    }
    setIsOpenDrinkPicker(false);
    setActiveGroupId(null);
  }

  function handleToggleOption(group: MenuGroup, option: MenuOption) {
    if (disabled || !menu || !menuItem) return;
    const isAlreadySelected = item.selection_option_ids.includes(option.id);
    let updatedOptionIds: string[] = [];

    if (group.max_select === 1) {
      if (isAlreadySelected) {
        if (group.min_select === 0) {
          updatedOptionIds = item.selection_option_ids.filter((id) => id !== option.id);
        } else {
          updatedOptionIds = item.selection_option_ids;
        }
      } else {
        const groupOptIds = new Set(group.options.map((o) => o.id));
        updatedOptionIds = [
          ...item.selection_option_ids.filter((id) => !groupOptIds.has(id)),
          option.id,
        ];
      }
      setActiveGroupId(null);
    } else {
      if (isAlreadySelected) {
        updatedOptionIds = item.selection_option_ids.filter((id) => id !== option.id);
      } else {
        const currentInGroup = group.options.filter((o) => item.selection_option_ids.includes(o.id));
        if (group.max_select > 0 && currentInGroup.length >= group.max_select) {
          const toRemove = currentInGroup[0].id;
          updatedOptionIds = [
            ...item.selection_option_ids.filter((id) => id !== toRemove),
            option.id,
          ];
        } else {
          updatedOptionIds = [...item.selection_option_ids, option.id];
        }
      }
    }

    const resolved = resolveItemPreview(menu, menuItem.id, item.quantity, updatedOptionIds);
    if (resolved) {
      onUpdateItem(resolved);
    }
  }

  return (
    <div className="preview-item-editor">
      <div className="preview-item-top">
        <div className="preview-item-drink-wrap">
          <button
            type="button"
            className={`preview-drink-select-btn ${isOpenDrinkPicker ? "open" : ""}`}
            onClick={() => {
              if (disabled || !menu) return;
              setIsOpenDrinkPicker((prev) => !prev);
              setActiveGroupId(null);
            }}
            disabled={disabled || !menu}
            title="點擊更換別的飲料"
          >
            <span className="preview-drink-name">{item.item_name}</span>
            <ChevronDown size={13} className={`preview-drink-arrow ${isOpenDrinkPicker ? "rotate" : ""}`} />
          </button>

          <div className="preview-qty-stepper">
            <button
              type="button"
              onClick={() => handleQuantityChange(-1)}
              disabled={disabled || item.quantity <= 1}
              aria-label="減少杯數"
            >
              <Minus size={11} />
            </button>
            <span className="preview-qty-val">{item.quantity}</span>
            <button
              type="button"
              onClick={() => handleQuantityChange(1)}
              disabled={disabled}
              aria-label="增加杯數"
            >
              <Plus size={11} />
            </button>
          </div>
        </div>

        <strong className="preview-item-subtotal">{formatMoney(item.line_total)}</strong>
      </div>

      {/* 規格標籤列 */}
      <div className="preview-item-specs">
        {itemGroups.length > 0 ? (
          itemGroups.map((group) => {
            const selectedOpts = group.options.filter((opt) =>
              item.selection_option_ids.includes(opt.id),
            );
            const isGroupActive = activeGroupId === group.id;

            if (selectedOpts.length > 0) {
              return (
                <button
                  key={group.id}
                  type="button"
                  className={`preview-spec-chip interactive ${isGroupActive ? "active" : ""}`}
                  onClick={() => {
                    if (disabled) return;
                    setIsOpenDrinkPicker(false);
                    setActiveGroupId((prev) => (prev === group.id ? null : group.id));
                  }}
                  disabled={disabled}
                  title={`點擊調整${group.name}`}
                >
                  <span className="spec-group-prefix">{group.name}:</span>
                  <strong className="spec-group-val">
                    {selectedOpts.map((o) => o.name).join(", ")}
                  </strong>
                  <ChevronDown size={11} className={`spec-chip-arrow ${isGroupActive ? "rotate" : ""}`} />
                </button>
              );
            }

            return (
              <button
                key={group.id}
                type="button"
                className={`preview-spec-chip interactive unselected ${isGroupActive ? "active" : ""}`}
                onClick={() => {
                  if (disabled) return;
                  setIsOpenDrinkPicker(false);
                  setActiveGroupId((prev) => (prev === group.id ? null : group.id));
                }}
                disabled={disabled}
                title={`點擊選擇${group.name}`}
              >
                <span>+ {group.name}</span>
                <ChevronDown size={11} className={`spec-chip-arrow ${isGroupActive ? "rotate" : ""}`} />
              </button>
            );
          })
        ) : (
          item.selection_option_names.map((spec, sidx) => (
            <span key={sidx} className="preview-spec-chip">
              {spec}
            </span>
          ))
        )}
      </div>

      {/* 飲料更換選單 */}
      <AnimatePresence>
        {isOpenDrinkPicker && menu && !disabled ? (
          <motion.div
            className="preview-shelf preview-drink-shelf"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.18 }}
          >
            <div className="preview-shelf-header">
              <span>更換飲料：</span>
              <button
                type="button"
                className="shelf-close-btn"
                onClick={() => setIsOpenDrinkPicker(false)}
                title="收合"
              >
                <X size={13} />
              </button>
            </div>
            <div className="preview-drink-search-box">
              <Search size={13} />
              <input
                type="text"
                className="preview-drink-search-input"
                placeholder="搜尋飲料名稱..."
                value={drinkSearch}
                onChange={(e) => setDrinkSearch(e.target.value)}
                autoFocus
              />
              {drinkSearch ? (
                <button
                  type="button"
                  className="shelf-clear-search"
                  onClick={() => setDrinkSearch("")}
                >
                  <X size={11} />
                </button>
              ) : null}
            </div>
            <div className="preview-drink-list">
              {filteredDrinks.map((drink) => {
                const isCurrent = drink.id === item.item_id;
                return (
                  <button
                    key={drink.id}
                    type="button"
                    className={`preview-drink-item-btn ${isCurrent ? "current" : ""}`}
                    onClick={() => handleSelectDrink(drink)}
                  >
                    <div className="preview-drink-item-left">
                      <strong>{drink.name}</strong>
                      {drink.category_name ? (
                        <span className="preview-drink-cat">{drink.category_name}</span>
                      ) : null}
                    </div>
                    <div className="preview-drink-item-right">
                      <span className="preview-drink-price">{formatMoney(drink.price)}</span>
                      {isCurrent ? <Check size={13} className="shelf-check-icon" /> : null}
                    </div>
                  </button>
                );
              })}
              {filteredDrinks.length === 0 ? (
                <div className="preview-drink-empty">找不到「{drinkSearch}」</div>
              ) : null}
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>

      {/* 規格調整選單 */}
      <AnimatePresence>
        {activeGroupId && activeGroup && !disabled ? (
          <motion.div
            className="preview-shelf"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.18 }}
          >
            <div className="preview-shelf-header">
              <span>
                選擇<strong>{activeGroup.name}</strong>
                <span className="shelf-rule-tag">
                  {activeGroup.max_select === 1 ? "單選" : `最多可選 ${activeGroup.max_select} 項`}
                </span>
              </span>
              <button
                type="button"
                className="shelf-close-btn"
                onClick={() => setActiveGroupId(null)}
                title="收合"
              >
                <X size={13} />
              </button>
            </div>
            <div className="preview-shelf-options">
              {activeGroup.options.map((opt) => {
                const isSelected = item.selection_option_ids.includes(opt.id);
                return (
                  <button
                    key={opt.id}
                    type="button"
                    className={`preview-shelf-opt-btn ${isSelected ? "selected" : ""}`}
                    onClick={() => handleToggleOption(activeGroup, opt)}
                  >
                    <span className="shelf-opt-name">{opt.name}</span>
                    {opt.price > 0 ? (
                      <span className="shelf-opt-price">+{formatMoney(opt.price)}</span>
                    ) : null}
                    {isSelected ? <Check size={12} className="shelf-check-icon" /> : null}
                  </button>
                );
              })}
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}
