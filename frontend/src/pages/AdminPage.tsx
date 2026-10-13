import { motion } from "framer-motion";
import {
  ArrowRight,
  CheckSquare,
  KeyRound,
  LoaderCircle,
  Plus,
  ReceiptText,
  RefreshCw,
  Square,
  Trash2,
  Users,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiKeysPanel } from "../components/ApiKeysPanel";
import { LeaderAuthModal } from "../components/LeaderAuthModal";
import { NidinGroupBuyImporter } from "../components/NidinGroupBuyImporter";
import { NidinStoreSearch } from "../components/NidinStoreSearch";
import { formatExpiry } from "../components/NidinConnectionStatus";
import { copyText } from "../lib/clipboard";
import { describeSharedLine, formatMoney } from "../lib/menu";
import { navigateTo } from "../lib/navigation";
import { platformLabel } from "../lib/platform";
import { selectSessionId, toSessionSummary, upsertSessionSummary } from "../lib/sessions";
import type { CartSessionDetail, CartSessionSummary, LeaderAuthStatus, SharedCartLine } from "../types";

function nidinButtonLabel(auth: LeaderAuthStatus | null): string {
  if (!auth?.is_authenticated) return "設定 Nidin 連線";
  // 到期日只看 JWT 內容，真正能不能用要打開視窗實際檢查
  return auth.expires_at ? `✓ Nidin 已連結（到 ${formatExpiry(auth.expires_at).slice(5, 10)}）` : "✓ Nidin 已連結";
}

export function AdminPage() {
  const [sessions, setSessions] = useState<CartSessionSummary[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const [selectedSessionDetail, setSelectedSessionDetail] = useState<CartSessionDetail | null>(null);
  const [sessionName, setSessionName] = useState("");
  const [groupBuyUrl, setGroupBuyUrl] = useState("");
  const [statusMessage, setStatusMessage] = useState("可以新增團購，也可以查看大家目前加了哪些品項。");
  const [isLoading, setIsLoading] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [leaderAuth, setLeaderAuth] = useState<LeaderAuthStatus | null>(null);
  const [showAuthModal, setShowAuthModal] = useState(false);
  const [isSyncingOfficial, setIsSyncingOfficial] = useState(false);
  const [adminTab, setAdminTab] = useState<"lines" | "split">("lines");
  const [paidContributors, setPaidContributors] = useState<Record<string, boolean>>({});
  // 刪 Nidin 跟團單無法復原：第一次按只進入確認狀態，第二次才真的刪
  const [confirmingOrderId, setConfirmingOrderId] = useState<string | null>(null);
  // 從本站移除團也是第一次按只進入確認
  const [confirmingRemoveId, setConfirmingRemoveId] = useState<string | null>(null);
  const [deletingOrderId, setDeletingOrderId] = useState<string | null>(null);
  const [isSavingPayment, setIsSavingPayment] = useState(false);
  const detailRequest = useRef(0);

  async function togglePaid(name: string) {
    if (!selectedSessionDetail || isSavingPayment) return;
    const request = detailRequest.current;
    setIsSavingPayment(true);
    try {
      const response = await fetch(`/api/cart-sessions/${selectedSessionDetail.session_id}/payments`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ contributor_name: name, paid: !paidContributors[name] }),
      });
      if (!response.ok) throw new Error();
      const payments = (await response.json()) as Record<string, boolean>;
      if (request === detailRequest.current) {
        setPaidContributors(payments);
        setStatusMessage("收款狀態已儲存。");
      }
    } catch {
      if (request === detailRequest.current) setStatusMessage("儲存收款狀態失敗，請重新整理後再試。");
    } finally {
      if (request === detailRequest.current) setIsSavingPayment(false);
    }
  }

  async function fetchLeaderAuth() {
    try {
      const res = await fetch("/api/leader/auth");
      if (res.ok) {
        const data = (await res.json()) as LeaderAuthStatus;
        setLeaderAuth(data);
      }
    } catch {}
  }

  async function syncOfficialOrders() {
    if (!selectedSessionId) return;
    setIsSyncingOfficial(true);
    try {
      const res = await fetch(`/api/cart-sessions/${selectedSessionId}/sync-official`, {
        method: "POST",
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "同步官方訂單失敗。");
      }
      const updated = (await res.json()) as CartSessionDetail;
      setSelectedSessionDetail(updated);
      setStatusMessage(`成功自官方同步！目前共 ${updated.line_count} 筆點單。`);
    } catch (err) {
      setStatusMessage(err instanceof Error ? err.message : "同步官方失敗。");
    } finally {
      setIsSyncingOfficial(false);
    }
  }

  const contributorGroups = useMemo(() => {
    if (!selectedSessionDetail) return [];
    const groups: Record<string, { lines: SharedCartLine[]; total: number }> = {};
    for (const line of selectedSessionDetail.lines) {
      const name = line.contributor_name || "訪客";
      if (!groups[name]) {
        groups[name] = { lines: [], total: 0 };
      }
      groups[name].lines.push(line);
      groups[name].total += line.line_total;
    }
    return Object.entries(groups).map(([name, data]) => ({
      name,
      lines: data.lines,
      total: data.total,
      cupCount: data.lines.reduce((s, l) => s + l.quantity, 0),
    }));
  }, [selectedSessionDetail]);

  const itemSummary = useMemo(() => {
    if (!selectedSessionDetail) return [];
    const summary: Record<string, number> = {};
    for (const line of selectedSessionDetail.lines) {
      summary[line.item_name] = (summary[line.item_name] || 0) + line.quantity;
    }
    return Object.entries(summary).map(([name, count]) => ({ name, count }));
  }, [selectedSessionDetail]);

  useEffect(() => {
    void fetchLeaderAuth();
  }, []);

  async function deleteFollowerOrder(sessionId: string, orderId: string) {
    setDeletingOrderId(orderId);
    try {
      const res = await fetch(`/api/cart-sessions/${sessionId}/orders/${orderId}`, { method: "DELETE" });
      const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
      if (!res.ok) {
        setStatusMessage(typeof body.detail === "string" ? body.detail : "刪單失敗，請稍後再試。");
        return;
      }
      setStatusMessage(`已在 ${platformLabel(selectedSessionDetail?.provider)} 刪除單號 ${orderId}。`);
      // 走 loadDetail 一併重讀收款狀態
      await loadDetail(sessionId);
    } catch {
      setStatusMessage("連不上本站伺服器，這筆還沒有刪除。");
    } finally {
      setDeletingOrderId(null);
      setConfirmingOrderId(null);
    }
  }

  async function loadDetail(sessionId: string) {
    const request = ++detailRequest.current;
    setSelectedSessionId(sessionId);
    setSelectedSessionDetail(null);
    setPaidContributors({});
    setIsSavingPayment(false);
    setIsLoading(true);
    try {
      const [response, paymentResponse] = await Promise.all([
        fetch(`/api/cart-sessions/${sessionId}`),
        fetch(`/api/cart-sessions/${sessionId}/payments`),
      ]);
      if (!response.ok) throw new Error("讀不到這個共享購物車。");
      if (!paymentResponse.ok) throw new Error("讀不到收款狀態，請重新整理後再試。");
      const [payload, payments] = await Promise.all([
        response.json() as Promise<CartSessionDetail>,
        paymentResponse.json() as Promise<Record<string, boolean>>,
      ]);
      if (request !== detailRequest.current) return;
      setSelectedSessionDetail(payload);
      setPaidContributors(payments);
      setStatusMessage(`目前正在查看 ${payload.session_name}。`);
    } catch (error) {
      if (request !== detailRequest.current) return;
      setSelectedSessionDetail(null);
      setPaidContributors({});
      setStatusMessage(error instanceof Error ? error.message : "讀不到共享購物車。");
    } finally {
      if (request === detailRequest.current) setIsLoading(false);
    }
  }

  async function refreshSessions(preferredSessionId?: string) {
    ++detailRequest.current;
    setPaidContributors({});
    setSelectedSessionDetail(null);
    setIsLoading(true);
    try {
      const response = await fetch("/api/admin/cart-sessions");
      if (!response.ok) {
        throw new Error("讀不到共享購物車清單。");
      }

      const payload = (await response.json()) as CartSessionSummary[];
      setSessions(payload);

      if (payload.length === 0) {
        setSelectedSessionId("");
        setSelectedSessionDetail(null);
        setStatusMessage("目前還沒有任何團購。");
        setIsLoading(false);
        return;
      }

      const nextSessionId = selectSessionId(payload, selectedSessionId, preferredSessionId);

      await loadDetail(nextSessionId);
    } catch (error) {
      setSessions([]);
      setSelectedSessionId("");
      setSelectedSessionDetail(null);
      setStatusMessage(error instanceof Error ? error.message : "讀不到共享購物車清單。");
      setIsLoading(false);
    }
  }

  useEffect(() => {
    void refreshSessions();
  }, []);

  async function createSession() {
    if (!groupBuyUrl.trim()) {
      setStatusMessage("先貼上揪團連結。");
      return;
    }

    setIsCreating(true);
    try {
      const response = await fetch("/api/cart-sessions", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          session_name: sessionName,
          group_buy_url: groupBuyUrl,
        }),
      });

      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "建立共享購物車失敗。");
      }

      const createdSession = payload as CartSessionSummary;
      setSessionName("");
      setGroupBuyUrl("");
      setSessions((current) => upsertSessionSummary(current, createdSession));
      await loadDetail(createdSession.session_id);
      setStatusMessage(`已新增 ${createdSession.session_name}。`);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "建立共享購物車失敗。");
    } finally {
      setIsCreating(false);
    }
  }

  async function clearSelectedSessionLines() {
    if (!selectedSessionDetail) {
      return;
    }

    setIsLoading(true);
    try {
      const response = await fetch(`/api/cart-sessions/${selectedSessionDetail.session_id}/lines`, {
        method: "DELETE",
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.detail ?? "清空共享購物車失敗。");
      }

      const clearedSession = payload as CartSessionDetail;
      setSelectedSessionDetail(clearedSession);
      setSessions((current) => upsertSessionSummary(current, toSessionSummary(clearedSession)));
      setStatusMessage(`已清空 ${clearedSession.session_name}。`);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "清空共享購物車失敗。");
    } finally {
      setIsLoading(false);
    }
  }

  async function removeSelectedSession() {
    if (!selectedSessionDetail) return;
    const { session_id: sessionId, session_name: sessionName } = selectedSessionDetail;
    if (confirmingRemoveId !== sessionId) {
      setConfirmingRemoveId(sessionId);
      return;
    }
    setIsLoading(true);
    try {
      const response = await fetch(`/api/cart-sessions/${sessionId}`, { method: "DELETE" });
      const payload = (await response.json().catch(() => ({}))) as { detail?: unknown };
      if (!response.ok) {
        throw new Error(typeof payload.detail === "string" ? payload.detail : "移除失敗。");
      }
      setSelectedSessionDetail(null);
      setSessions((current) => current.filter((session) => session.session_id !== sessionId));
      setStatusMessage(`已從本站移除 ${sessionName}（平台上的團不受影響）。`);
      await refreshSessions();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "移除失敗。");
    } finally {
      setConfirmingRemoveId(null);
      setIsLoading(false);
    }
  }

  function copyOrderSummary() {
    if (!selectedSessionDetail || selectedSessionDetail.lines.length === 0) return;
    const linesText = selectedSessionDetail.lines.map((l) => describeSharedLine(l)).join("\n");
    const text = `【${selectedSessionDetail.session_name} 訂單匯總】\n${linesText}\n---\n總計：${selectedSessionDetail.lines.reduce((s, l) => s + l.quantity, 0)} 杯，共 ${formatMoney(selectedSessionDetail.total_money)}`;
    void copyText(text).then((ok) =>
      setStatusMessage(ok ? "已複製訂單匯總文字到剪貼簿！" : "瀏覽器不允許複製，請改用 localhost 或 HTTPS 開啟後台。"),
    );
  }

  return (
    <main className="shell">
      <motion.header
        className="app-header"
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35 }}
      >
        <div className="header-left">
          <div className="header-brand">
            <span className="brand-badge">後台管理</span>
            <h1 className="header-title">團購管理後台</h1>
          </div>
          <div className="hero-status header-status-pill">{statusMessage}</div>
        </div>
        <div className="header-right">
          <div className="header-metric">
            <span className="metric-label">已收單金額</span>
            <strong className="metric-value">{formatMoney(selectedSessionDetail?.total_money ?? 0)}</strong>
          </div>
          <button className="link-chip" onClick={() => navigateTo("/")}>
            回到點餐頁
          </button>
        </div>
      </motion.header>

      <section className="workspace workspace-admin">
        <motion.div
          className="panel"
          initial={{ opacity: 0, x: -20 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.45, delay: 0.1 }}
        >
          <div className="panel-header">
            <Plus size={18} />
            <h2>新增團購</h2>
          </div>
          <label className="field">
            <span>購物車名稱</span>
            <input
              placeholder="不填就用店名"
              value={sessionName}
              onChange={(event) => setSessionName(event.target.value)}
            />
          </label>
          <label className="field">
            <span>揪團連結</span>
            <input
              placeholder="貼上 Nidin 或 Ocard 的揪團連結"
              value={groupBuyUrl}
              onChange={(event) => setGroupBuyUrl(event.target.value)}
            />
          </label>
          <button className="action-button" onClick={() => void createSession()} disabled={isCreating}>
            {isCreating ? <LoaderCircle className="spin" size={18} /> : <ArrowRight size={18} />}
            建立購物車
          </button>
          {leaderAuth?.is_authenticated ? (
            <NidinStoreSearch
              onOpened={(session) => {
                setSessions((current) => upsertSessionSummary(current, toSessionSummary(session)));
                void loadDetail(session.session_id);
                setStatusMessage(`已在 Nidin 開團並加進本站：${session.session_name}。`);
              }}
            />
          ) : null}
          {leaderAuth?.is_authenticated ? (
            <NidinGroupBuyImporter
              onImported={(session, syncError) => {
                setSessions((current) => upsertSessionSummary(current, toSessionSummary(session)));
                // 走 loadDetail 才會一併讀付款狀態，跟點選既有團購一致
                void loadDetail(session.session_id);
                setStatusMessage(
                  syncError
                    ? `已匯入 ${session.session_name}，但同步失敗：${syncError}`
                    : `已匯入 ${session.session_name}，同步到 ${session.line_count} 筆。`,
                );
              }}
              onOpen={(sessionId) => void loadDetail(sessionId)}
            />
          ) : null}
          <ApiKeysPanel />
        </motion.div>

        <motion.div
          className="panel"
          initial={{ opacity: 0, y: 24 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.45, delay: 0.15 }}
        >
          <div className="panel-header panel-header-space">
            <div>
              <h2>團購列表</h2>
              <p>{sessions.length} 組</p>
            </div>
            <button className="ghost-button ghost-button-text" onClick={() => void refreshSessions(selectedSessionId)}>
              重新整理
            </button>
          </div>

          <div className="session-stack">
            {sessions.map((session) => (
              <button
                key={session.session_id}
                className={session.session_id === selectedSessionId ? "session-card session-card-active" : "session-card"}
                onClick={() => void loadDetail(session.session_id)}
              >
                <strong>{session.session_name}</strong>
                <span>{formatMoney(session.total_money)} · {session.line_count} 筆</span>
              </button>
            ))}
            {sessions.length === 0 ? <div className="empty-state">目前還沒有任何團購。</div> : null}
          </div>
        </motion.div>

        <motion.div
          className="panel"
          initial={{ opacity: 0, x: 20 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.45, delay: 0.2 }}
        >
          <div className="panel-header panel-header-space">
            <div>
              <h2>{selectedSessionDetail?.session_name ?? "團購管理"}</h2>
              <p>{selectedSessionDetail?.store_name ?? "選一組團購後查看"}</p>
            </div>
            <div className="inline-actions" style={{ flexWrap: "wrap", gap: "0.5rem" }}>
              <button
                className="ghost-button ghost-button-text"
                onClick={() => setShowAuthModal(true)}
                title={leaderAuth?.is_authenticated ? "打開後會實際向 Nidin 檢查連線" : "登入 Nidin 帳號以同步官方點單"}
              >
                <KeyRound size={16} />
                <span>{nidinButtonLabel(leaderAuth)}</span>
              </button>

              {selectedSessionDetail ? (
                <button
                  className="ghost-button ghost-button-text"
                  onClick={() => void syncOfficialOrders()}
                  disabled={isSyncingOfficial || !leaderAuth?.is_authenticated}
                  title="從你訂官方抓取跟團者訂單"
                >
                  <RefreshCw className={isSyncingOfficial ? "spin" : ""} size={16} />
                  <span>{isSyncingOfficial ? "同步官方中..." : "同步官方點單"}</span>
                </button>
              ) : null}

              {selectedSessionDetail && selectedSessionDetail.lines.length > 0 ? (
                <button
                  className="ghost-button ghost-button-text"
                  onClick={copyOrderSummary}
                  title="複製訂單匯總文字到剪貼簿"
                >
                  <ReceiptText size={16} />
                  <span>複製匯總</span>
                </button>
              ) : null}

              {selectedSessionDetail ? (
                <button
                  className="ghost-button ghost-button-text"
                  onClick={() => void clearSelectedSessionLines()}
                  disabled={isLoading || selectedSessionDetail.line_count === 0}
                  aria-label="清空已收單"
                >
                  <Trash2 size={16} />
                  <span>清空已收單</span>
                </button>
              ) : null}

              {selectedSessionDetail ? (
                <button
                  className="ghost-button ghost-button-text"
                  onClick={() => void removeSelectedSession()}
                  disabled={isLoading}
                  title="只從本站移除，平台上的團和已送出的單不受影響"
                >
                  <X size={16} />
                  <span>{confirmingRemoveId === selectedSessionDetail.session_id ? "確定移除？" : "從本站移除"}</span>
                </button>
              ) : null}
            </div>
          </div>

          {selectedSessionDetail ? (
            <div className="summary-grid">
              <div className="summary-card">
                <span>目前已收單金額</span>
                <strong>{formatMoney(selectedSessionDetail.total_money)}</strong>
              </div>
              <div className="summary-card">
                <span>目前已收單筆數</span>
                <strong>{selectedSessionDetail.line_count} 筆</strong>
              </div>
            </div>
          ) : null}

          {selectedSessionDetail ? (
            <div className="delivery-card">
              <p>{selectedSessionDetail.order_status_reason || selectedSessionDetail.delivery_description}</p>
            </div>
          ) : null}

          {selectedSessionDetail ? (
            <div className="admin-tab-bar" style={{ marginTop: "1rem" }}>
              <button
                className={adminTab === "lines" ? "admin-tab admin-tab-active" : "admin-tab"}
                onClick={() => setAdminTab("lines")}
              >
                <ReceiptText size={16} style={{ display: "inline", verticalAlign: "middle", marginRight: "0.25rem" }} />
                購物車明細 ({selectedSessionDetail.line_count})
              </button>
              <button
                className={adminTab === "split" ? "admin-tab admin-tab-active" : "admin-tab"}
                onClick={() => setAdminTab("split")}
              >
                <Users size={16} style={{ display: "inline", verticalAlign: "middle", marginRight: "0.25rem" }} />
                分飲料與對帳表 ({contributorGroups.length} 人)
              </button>
            </div>
          ) : null}

          {adminTab === "lines" ? (
            <div className="cart-list">
              {selectedSessionDetail?.lines.map((line, index, all) => {
                const orderId = line.official_order_id;
                // 一張跟團單可能有好幾杯，刪除鈕只放在第一杯；Nidin 團長自己的飲料要到 Nidin 改。
                // Ocard 只能撤回本站送出的單（後端對其他單會說明原因）
                const deletable =
                  orderId && !orderId.startsWith("head:") && all.findIndex((l) => l.official_order_id === orderId) === index;
                return (
                  <div key={line.line_id} className="cart-line">
                    <div>
                      <p className="cart-title">{line.item_name}</p>
                      <p className="cart-options">{describeSharedLine(line)}</p>
                    </div>
                    <div className="cart-line-actions">
                      <strong>{formatMoney(line.line_total)}</strong>
                      {deletable && selectedSessionDetail ? (
                        <button
                          type="button"
                          className="ghost-button ghost-button-text"
                          disabled={deletingOrderId !== null}
                          title={`在 ${platformLabel(selectedSessionDetail.provider)} 刪掉 ${line.contributor_name} 這張跟團單`}
                          onClick={() =>
                            confirmingOrderId === orderId
                              ? void deleteFollowerOrder(selectedSessionDetail.session_id, orderId)
                              : setConfirmingOrderId(orderId)
                          }
                        >
                          {deletingOrderId === orderId ? <LoaderCircle className="spin" size={14} /> : <X size={14} />}
                          <span>{confirmingOrderId === orderId ? "確定刪除？" : "刪除這筆"}</span>
                        </button>
                      ) : null}
                    </div>
                  </div>
                );
              })}
              {selectedSessionDetail && selectedSessionDetail.lines.length === 0 ? (
                <div className="empty-state">目前還沒有加入任何品項。</div>
              ) : null}
              {!selectedSessionDetail ? <div className="empty-state">先從左邊選一組團購。</div> : null}
            </div>
          ) : (
            <div className="split-sheet">
              {/* Item Breakdown for quick counting */}
              {itemSummary.length > 0 ? (
                <div className="split-card" style={{ background: "var(--accent-soft)" }}>
                  <div className="panel-header" style={{ marginBottom: "0.5rem" }}>
                    <strong>🥤 外送總杯數盤點清單</strong>
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
                    {itemSummary.map((it) => (
                      <span
                        key={it.name}
                        style={{
                          padding: "0.25rem 0.625rem",
                          borderRadius: "999px",
                          background: "#fff",
                          fontSize: "0.875rem",
                          border: "1px solid var(--line)",
                        }}
                      >
                        {it.name}: <strong>{it.count} 杯</strong>
                      </span>
                    ))}
                  </div>
                </div>
              ) : null}

              {/* Grouped by Contributor */}
              {contributorGroups.map((group) => {
                const isPaid = Boolean(paidContributors[group.name]);
                return (
                  <div key={group.name} className="split-card">
                    <div className="split-card-header">
                      <div>
                        <strong>{group.name}</strong>
                        <span style={{ marginLeft: "0.5rem", color: "var(--muted)", fontSize: "0.85rem" }}>
                          ({group.cupCount} 杯 · 應收 {formatMoney(group.total)})
                        </span>
                      </div>
                      <button
                        type="button"
                        aria-pressed={isPaid}
                        className={`split-paid-label ${isPaid ? "split-paid-done" : ""}`}
                        disabled={isLoading || isSavingPayment}
                        onClick={() => void togglePaid(group.name)}
                      >
                        {isPaid ? <CheckSquare size={16} /> : <Square size={16} />}
                        <span>{isPaid ? "已收清" : "待收款"}</span>
                      </button>
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: "0.375rem" }}>
                      {group.lines.map((line) => (
                        <div
                          key={line.line_id}
                          style={{
                            display: "flex",
                            justifyContent: "space-between",
                            fontSize: "0.875rem",
                            color: "var(--text)",
                          }}
                        >
                          <span>
                            {line.quantity > 1 ? `${line.quantity}x ` : ""}
                            {line.item_name}
                            <span style={{ color: "var(--muted)", marginLeft: "0.375rem" }}>
                              ({line.selections.map((s) => s.option_name).join(" / ") || "固定規格"})
                            </span>
                          </span>
                          <strong>{formatMoney(line.line_total)}</strong>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}

              {contributorGroups.length === 0 ? (
                <div className="empty-state">目前沒有點單資料可對帳。</div>
              ) : null}
            </div>
          )}
        </motion.div>
      </section>

      <LeaderAuthModal
        showAuthModal={showAuthModal}
        leaderAuth={leaderAuth}
        setLeaderAuth={setLeaderAuth}
        setShowAuthModal={setShowAuthModal}
        setStatusMessage={setStatusMessage}
      />
    </main>
  );
}
