import { motion } from "framer-motion";
import { Plus, Search } from "lucide-react";

import { formatMoney } from "../lib/menu";
import type { MenuItem, NormalizedMenu } from "../types";
import { CustomSelect } from "./CustomSelect";

interface MenuCatalogProps {
  menu: NormalizedMenu | null;
  filteredItems: MenuItem[];
  search: string;
  selectedCategoryId: string;
  onSearchChange: (value: string) => void;
  onCategoryChange: (value: string) => void;
  onSelectItem: (item: MenuItem) => void;
}

export function MenuCatalog({
  menu,
  filteredItems,
  search,
  selectedCategoryId,
  onSearchChange,
  onCategoryChange,
  onSelectItem,
}: MenuCatalogProps) {
  return (
    <motion.div
      className="panel panel-menu"
      initial={{ opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, delay: 0.15 }}
    >
      <div className="panel-header panel-header-space">
        <div>
          <h2>菜單</h2>
          <p>{menu ? `${filteredItems.length} / ${menu.items.length} 個品項` : "先選擇要加入的團購"}</p>
        </div>
        <div className="menu-filters">
          <div className="select-box">
            <span>分類</span>
            <CustomSelect
              ariaLabel="分類"
              value={selectedCategoryId}
              onChange={onCategoryChange}
              options={[
                { value: "all", label: "全部分類" },
                ...(menu?.categories.map((category) => ({
                  value: category.id,
                  label: category.name,
                })) ?? []),
              ]}
              disabled={!menu}
              placeholder="全部分類"
            />
          </div>
          <label className="search-box">
            <Search size={16} />
            <input
              placeholder="搜尋飲料"
              value={search}
              onChange={(event) => onSearchChange(event.target.value)}
              disabled={!menu}
            />
          </label>
        </div>
      </div>

      <div className="menu-grid">
        {filteredItems.map((item, index) => (
          <motion.button
            key={item.id}
            className="item-card"
            onClick={() => onSelectItem(item)}
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.22, delay: index * 0.015 }}
          >
            {item.image_url ? <img className="item-thumb" src={item.image_url} alt={item.name} loading="lazy" /> : null}
            <span className="item-price">{formatMoney(item.price)}</span>
            <div className="item-card-body">
              <div className="item-card-copy">
                <strong>{item.name}</strong>
                {item.category_name ? <span>{item.category_name}</span> : null}
              </div>
              <div className="item-order-badge">
                <Plus size={14} />
                <span>點餐</span>
              </div>
            </div>
          </motion.button>
        ))}
        {!menu ? <div className="empty-state">先從左邊選一個團購。</div> : null}
        {menu && filteredItems.length === 0 ? <div className="empty-state">沒有找到符合的品項。</div> : null}
      </div>
    </motion.div>
  );
}
