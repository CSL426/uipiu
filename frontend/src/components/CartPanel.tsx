import { motion } from "framer-motion";
import { ArrowRight, LoaderCircle, ReceiptText, ShoppingBag, Trash2, Plus, X } from "lucide-react";

import { formatMoney, getLineTotal } from "../lib/menu";
import type { CartLine, CartSessionDetail, CartSessionSummary, SharedCartLine } from "../types";

interface CartPanelProps {
  cartTab: "draft" | "orders";
  cartLines: CartLine[];
  contributorName: string;
  draftTotal: number;
  isSubmitting: boolean;
  selectedSessionId: string;
  selectedSessionDetail: CartSessionDetail | null;
  selectedSessionSummary: CartSessionSummary | null;
  onTabChange: (tab: "draft" | "orders") => void;
  onRemoveLine: (lineId: string) => void;
  onSubmitOrder: () => void;
  onCloneLine: (line: SharedCartLine) => void;
  /** 這台瀏覽器送出過的 Nidin 單號（有取消憑證） */
  myOrderIds?: ReadonlySet<string>;
  cancellingOrderId?: string | null;
  onCancelOrder?: (officialOrderId: string) => void;
}

export function CartPanel({
  cartTab,
  cartLines,
  contributorName,
  draftTotal,
  isSubmitting,
  selectedSessionId,
  selectedSessionDetail,
  selectedSessionSummary,
  onTabChange,
  onRemoveLine,
  onSubmitOrder,
  onCloneLine,
  myOrderIds = new Set<string>(),
  cancellingOrderId = null,
  onCancelOrder,
}: CartPanelProps) {
  // 一張 Nidin 單可能有好幾杯；取消鈕只放在該單第一杯，免得看起來能單杯取消
  const firstLineOfOrder = new Set<string>();
  const seenOrders = new Set<string>();
  for (const line of selectedSessionDetail?.lines ?? []) {
    if (line.official_order_id && !seenOrders.has(line.official_order_id)) {
      seenOrders.add(line.official_order_id);
      firstLineOfOrder.add(line.line_id);
    }
  }

  return (
    <motion.div
      className="panel panel-cart"
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.45, delay: 0.2 }}
    >
      <div className="cart-tab-bar">
        <button
          type="button"
          className={cartTab === "draft" ? "cart-tab cart-tab-active" : "cart-tab"}
          onClick={() => onTabChange("draft")}
        >
          <ShoppingBag size={16} />
          <span>待送出 ({cartLines.length})</span>
        </button>
        <button
          type="button"
          className={cartTab === "orders" ? "cart-tab cart-tab-active" : "cart-tab"}
          onClick={() => onTabChange("orders")}
        >
          <ReceiptText size={16} />
          <span>本團訂單 ({selectedSessionDetail?.lines.length ?? 0})</span>
        </button>
      </div>

      {cartTab === "draft" ? (
        <>
          <div className="panel-header panel-header-space">
            <div className="panel-header">
              <ShoppingBag size={18} />
              <h2>這次要加的</h2>
            </div>
            <strong>{formatMoney(draftTotal)}</strong>
          </div>

          <div className="cart-list">
            {cartLines.map((line) => (
              <div key={line.id} className="cart-line">
                <div>
                  <p className="cart-title">
                    {line.quantity} x {line.item.name}
                  </p>
                  <p className="cart-options">
                    {line.selections.length > 0
                      ? line.selections.map((selection) => selection.optionName).join(" / ")
                      : "標準規格"}
                  </p>
                </div>
                <div className="cart-line-actions">
                  <strong>{formatMoney(getLineTotal(line))}</strong>
                  <button className="ghost-button" onClick={() => onRemoveLine(line.id)} aria-label="移除品項">
                    <Trash2 size={16} />
                  </button>
                </div>
              </div>
            ))}
            {cartLines.length === 0 ? <div className="empty-state">還沒有加入任何品項。</div> : null}
          </div>

          <div className="submit-box">
            {cartLines.length > 0 ? (
              <div className="submit-summary-line">
                <span>點餐人：<strong>{contributorName}</strong></span>
                <span>共 <strong>{cartLines.reduce((s, l) => s + l.quantity, 0)}</strong> 杯 · <strong>{formatMoney(draftTotal)}</strong></span>
              </div>
            ) : null}
            <button
              className="submit-button"
              onClick={onSubmitOrder}
              disabled={isSubmitting || cartLines.length === 0 || !selectedSessionId}
            >
              {isSubmitting ? <LoaderCircle className="spin" size={18} /> : <ArrowRight size={18} />}
              送出訂單
            </button>
          </div>
        </>
      ) : (
        <div className="group-orders-view">
          {selectedSessionSummary?.delivery_minimum != null ? (
            <div className="delivery-progress-card">
              <div className="delivery-progress-header">
                <span>外送門檻進度</span>
                <span
                  className={
                    (selectedSessionDetail?.total_money ?? 0) >= selectedSessionSummary.delivery_minimum
                      ? "delivery-progress-status met"
                      : "delivery-progress-status unmet"
                  }
                >
                  {(selectedSessionDetail?.total_money ?? 0) >= selectedSessionSummary.delivery_minimum
                    ? "🎉 已達外送門檻！"
                    : `差 ${formatMoney(
                        selectedSessionSummary.delivery_minimum - (selectedSessionDetail?.total_money ?? 0),
                      )} 免運`}
                </span>
              </div>
              <div className="progress-track">
                <div
                  className={
                    (selectedSessionDetail?.total_money ?? 0) >= selectedSessionSummary.delivery_minimum
                      ? "progress-fill met"
                      : "progress-fill"
                  }
                  style={{
                    width: `${Math.min(
                      100,
                      Math.round(
                        ((selectedSessionDetail?.total_money ?? 0) /
                          (selectedSessionSummary.delivery_minimum || 1)) *
                          100,
                      ),
                    )}%`,
                  }}
                />
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.78rem", color: "var(--muted)" }}>
                <span>目前 {formatMoney(selectedSessionDetail?.total_money ?? 0)}</span>
                <span>門檻 {formatMoney(selectedSessionSummary.delivery_minimum)}</span>
              </div>
            </div>
          ) : null}

          <div className="summary-grid" style={{ marginBottom: "0.75rem" }}>
            <div className="summary-card" style={{ padding: "0.75rem" }}>
              <span style={{ fontSize: "0.78rem" }}>本團總額</span>
              <strong style={{ fontSize: "1.1rem" }}>{formatMoney(selectedSessionDetail?.total_money ?? 0)}</strong>
            </div>
            <div className="summary-card" style={{ padding: "0.75rem" }}>
              <span style={{ fontSize: "0.78rem" }}>總杯數</span>
              <strong style={{ fontSize: "1.1rem" }}>
                {selectedSessionDetail?.lines.reduce((s, l) => s + l.quantity, 0) ?? 0} 杯
              </strong>
            </div>
          </div>

          <div className="group-order-list">
            {selectedSessionDetail?.lines && selectedSessionDetail.lines.length > 0 ? (
              selectedSessionDetail.lines.map((line) => {
                const ownsOrder = Boolean(line.official_order_id && myOrderIds.has(line.official_order_id));
                const isMine = ownsOrder || (contributorName.trim() && line.contributor_name === contributorName.trim());
                const canCancel = ownsOrder && onCancelOrder && firstLineOfOrder.has(line.line_id);
                return (
                  <div
                    key={line.line_id}
                    className={isMine ? "group-order-card group-order-card-mine" : "group-order-card"}
                  >
                    <div className="group-order-top">
                      <span className={isMine ? "contributor-tag mine" : "contributor-tag"}>
                        {line.contributor_name} {isMine ? " (你)" : ""}
                      </span>
                      <strong style={{ color: "var(--accent-deep)" }}>{formatMoney(line.line_total)}</strong>
                    </div>

                    <div className="group-order-item-title">
                      {line.item_name} × {line.quantity}
                    </div>

                    {line.selections.length > 0 ? (
                      <div className="group-order-specs">
                        {line.selections.map((sel, idx) => (
                          <span key={idx} className="spec-pill">
                            {sel.option_name}
                            {sel.price > 0 ? ` +$${sel.price}` : ""}
                          </span>
                        ))}
                      </div>
                    ) : null}

                    <div className="group-order-footer">
                      <span style={{ fontSize: "0.75rem", color: "var(--muted)" }}>
                        {new Date(line.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                      </span>
                      <div style={{ display: "flex", gap: "0.35rem" }}>
                        {canCancel ? (
                          <button
                            type="button"
                            className="clone-order-btn"
                            disabled={cancellingOrderId !== null}
                            onClick={() => onCancelOrder?.(line.official_order_id as string)}
                            title="請團長在 Nidin 刪掉你這筆（送出後無法自己改單）"
                          >
                            {cancellingOrderId === line.official_order_id ? <LoaderCircle size={12} className="spin" /> : <X size={12} />}
                            <span>取消這筆</span>
                          </button>
                        ) : null}
                        <button
                          type="button"
                          className="clone-order-btn"
                          onClick={() => onCloneLine(line)}
                          title="加一杯跟這筆一樣的飲料到自己的暫存清單"
                        >
                          <Plus size={12} />
                          <span>+1 跟著點</span>
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })
            ) : (
              <div className="empty-state">
                目前這組團購還沒有人點餐，趕快在左邊點餐當第一個吧！
              </div>
            )}
          </div>
        </div>
      )}
    </motion.div>
  );
}
