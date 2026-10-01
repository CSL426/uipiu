import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowLeftRight,
  CheckCircle2,
  ChevronRight,
  LoaderCircle,
  Ticket,
} from "lucide-react";
import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { CartPanel } from "../components/CartPanel";
import { ChatWidget } from "../components/ChatWidget";
import { CustomSelect } from "../components/CustomSelect";
import { ItemComposerModal } from "../components/ItemComposerModal";
import { MenuCatalog } from "../components/MenuCatalog";
import { MenuFreshness } from "../components/MenuFreshness";
import { createLineId, formatMoney, getLineTotal } from "../lib/menu";
import { navigateTo } from "../lib/navigation";
import {
  cancelLiveOrder,
  myOrderTokens,
  newSubmissionId,
  readContributorName,
  readContributorPhone,
  refreshFromNidin,
  saveContributorName,
  saveContributorPhone,
  submitLiveOrder,
  type LiveOrderLine,
} from "../lib/liveOrder";
import { stopSpeaking } from "../lib/speech";
import { selectSessionId, toSessionSummary, upsertSessionSummary } from "../lib/sessions";
import type {
  CartLine,
  CartSessionDetail,
  CartSessionSummary,
  GuestOrderReceipt,
  MenuItem,
  NormalizedMenu,
  SharedCartLine,
} from "../types";

export function PublicPage() {
  const [sessions, setSessions] = useState<CartSessionSummary[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const [selectedSessionDetail, setSelectedSessionDetail] = useState<CartSessionDetail | null>(null);
  const [menu, setMenu] = useState<NormalizedMenu | null>(null);
  // 名字就是 Nidin 上這張跟團單的身分；記在瀏覽器，下次不用重填
  const [contributorName, setContributorName] = useState(readContributorName);
  const [contributorPhone, setContributorPhone] = useState(readContributorPhone);
  const [search, setSearch] = useState("");
  const [selectedCategoryId, setSelectedCategoryId] = useState("all");
  const [activeItemId, setActiveItemId] = useState<string | null>(null);
  const [cartLines, setCartLines] = useState<CartLine[]>([]);
  const [cartTab, setCartTab] = useState<"draft" | "orders">("draft");
  const [lastOfficialReceipt, setLastOfficialReceipt] = useState<GuestOrderReceipt | null>(null);
  const [statusMessage, setStatusMessage] = useState("正在讀取目前可點的團購。");
  const [isLoading, setIsLoading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [myOrders, setMyOrders] = useState<Record<string, string>>({});
  const [cancellingOrderId, setCancellingOrderId] = useState<string | null>(null);
  // 同一批暫存內容重按「送出」沿用同一個 submission_id：逾時後重按不會在 Nidin 下第二次
  const draftSubmission = useRef<{ lineIds: string; id: string } | null>(null);
  // 非同步結果回來時只套用到「還是同一團」的畫面；送單/取消後遞增，讓更早發出的刷新結果作廢
  const currentSessionRef = useRef("");
  const stateVersion = useRef(0);
  const deferredSearch = useDeferredValue(search);

  const activeItem = menu?.items.find((item) => item.id === activeItemId) ?? null;

  function cloneLineToDraft(line: SharedCartLine) {
    stopSpeaking();
    if (!menu) return;
    const item = menu.items.find((it) => it.id === line.item_id);
    if (!item) {
      setStatusMessage(`找不到此品項 (${line.item_name})。`);
      return;
    }

    const newLine: CartLine = {
      id: createLineId(),
      item,
      quantity: line.quantity,
      selections: line.selections.map((sel) => ({
        groupId: sel.group_id,
        groupName: sel.group_name,
        optionId: sel.option_id,
        optionName: sel.option_name,
        price: sel.price,
      })),
    };

    setCartLines((current) => [...current, newLine]);
    setCartTab("draft");
    setStatusMessage(`已將「${line.item_name}」加入您的點餐清單！`);
  }

  const filteredItems = useMemo(() => {
    if (!menu) {
      return [];
    }

    const keyword = deferredSearch.trim().toLowerCase();
    return menu.items.filter((item) => {
      const matchesCategory = selectedCategoryId === "all" || item.category_id === selectedCategoryId;
      const matchesKeyword =
        !keyword ||
        [item.name, ...item.aliases].some((candidate) => candidate.toLowerCase().includes(keyword));
      return matchesCategory && matchesKeyword;
    });
  }, [deferredSearch, menu, selectedCategoryId]);

  const draftTotal = useMemo(
    () => cartLines.reduce((sum, line) => sum + getLineTotal(line), 0),
    [cartLines],
  );

  async function loadSession(sessionId: string) {
    setIsLoading(true);
    try {
      const [detailResponse, menuResponse] = await Promise.all([
        fetch(`/api/cart-sessions/${sessionId}`),
        fetch(`/api/cart-sessions/${sessionId}/menu`),
      ]);

      if (!detailResponse.ok || !menuResponse.ok) {
        throw new Error("這個團購暫時讀不到，請稍後再試。");
      }

      const [detailPayload, menuPayload] = await Promise.all([
        detailResponse.json() as Promise<CartSessionDetail>,
        menuResponse.json() as Promise<NormalizedMenu>,
      ]);
      setSelectedSessionId(sessionId);
      setSelectedSessionDetail(detailPayload);
      setMenu(menuPayload);
      setSelectedCategoryId("all");
      setLastOfficialReceipt(null);
      setStatusMessage(`已切換到 ${detailPayload.session_name}，可以開始選餐。`);
    } catch (error) {
      setMenu(null);
      setSelectedSessionDetail(null);
      setStatusMessage(error instanceof Error ? error.message : "讀不到團購資訊。");
    } finally {
      setIsLoading(false);
    }
  }

  async function refreshSessions(preferredSessionId?: string) {
    setIsLoading(true);
    try {
      const response = await fetch("/api/cart-sessions");
      if (!response.ok) {
        throw new Error("讀不到目前揪團，請稍後再試。");
      }

      const payload = (await response.json()) as CartSessionSummary[];
      setSessions(payload);

      if (payload.length === 0) {
        setSelectedSessionId("");
        setSelectedSessionDetail(null);
        setMenu(null);
        setStatusMessage("目前還沒有開團，請先到後台新增揪團。");
        setIsLoading(false);
        return;
      }

      const nextSessionId = selectSessionId(payload, selectedSessionId, preferredSessionId);

      await loadSession(nextSessionId);
    } catch (error) {
      setSessions([]);
      setSelectedSessionId("");
      setSelectedSessionDetail(null);
      setMenu(null);
      setStatusMessage(error instanceof Error ? error.message : "讀不到目前揪團。");
      setIsLoading(false);
    }
  }

  useEffect(() => {
    void refreshSessions();
  }, []);

  function applySessionDetail(detail: CartSessionDetail) {
    // 切到別團後才回來的結果不要蓋掉現在這團的畫面
    if (detail.session_id !== currentSessionRef.current) return;
    setSelectedSessionDetail(detail);
    setSessions((current) => upsertSessionSummary(current, toSessionSummary(detail)));
  }

  async function syncFromNidin(sessionId: string) {
    const versionAtStart = stateVersion.current;
    const result = await refreshFromNidin(sessionId);
    // 刷新途中有人送單或取消：這份刷新比較舊，套上去會把剛送出的單抹掉
    if (result && stateVersion.current === versionAtStart) applySessionDetail(result.session);
  }

  // Nidin 才是真相：開著頁面時定期撈回來（後端同一團 10 秒內只真的同步一次）
  useEffect(() => {
    currentSessionRef.current = selectedSessionId;
    if (!selectedSessionId) return;
    setMyOrders(myOrderTokens(selectedSessionId));
    void syncFromNidin(selectedSessionId);
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void syncFromNidin(selectedSessionId);
    }, 20000);
    const onVisible = () => {
      if (document.visibilityState === "visible") void syncFromNidin(selectedSessionId);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [selectedSessionId]);

  async function submitToNidin(
    submissionId: string,
    name: string,
    lines: LiveOrderLine[],
  ): Promise<CartSessionDetail> {
    const result = await submitLiveOrder(selectedSessionId, submissionId, name, lines, contributorPhone);
    stateVersion.current += 1;
    applySessionDetail(result.session);
    setMyOrders(myOrderTokens(selectedSessionId));
    if (result.receipt) setLastOfficialReceipt(result.receipt);
    setCartTab("orders");
    setStatusMessage(
      result.already_submitted
        ? `這筆之前已經送到 Nidin 了（單號 ${result.official_order_id}），不會重複下單。`
        : `已送到 Nidin，單號 ${result.official_order_id}。`,
    );
    return result.session;
  }

  async function cancelOrder(officialOrderId: string) {
    if (!selectedSessionId) return;
    setCancellingOrderId(officialOrderId);
    try {
      const detail = await cancelLiveOrder(selectedSessionId, officialOrderId);
      stateVersion.current += 1;
      applySessionDetail(detail);
      setMyOrders(myOrderTokens(selectedSessionId));
      setStatusMessage(`已在 Nidin 取消單號 ${officialOrderId}。`);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "取消失敗。");
    } finally {
      setCancellingOrderId(null);
    }
  }

  function openComposer(item: MenuItem) {
    setActiveItemId(item.id);
    setStatusMessage(`${item.name} 已打開，選好口味後就能加入暫存購物車。`);
  }

  function closeComposer() {
    setActiveItemId(null);
  }

  function removeCartLine(lineId: string) {
    stopSpeaking();
    setCartLines((current) => current.filter((line) => line.id !== lineId));
  }

  async function submitOrder() {
    stopSpeaking();
    if (!selectedSessionId) {
      setStatusMessage("目前還沒有可送出的團購。");
      return;
    }
    if (!contributorName.trim() || contributorName.trim() === "訪客") {
      setStatusMessage("先填你的名字，它會是你在 Nidin 上這筆單的名字。");
      return;
    }
    if (cartLines.length === 0) {
      setStatusMessage("暫存購物車是空的，先選幾杯再送。");
      return;
    }

    const submittedIds = new Set(cartLines.map((line) => line.id));
    // 納入團與名字：同一批暫存換團或改名後再送，就是另一筆
    const lineKey = [selectedSessionId, contributorName.trim(), contributorPhone.trim(), ...[...submittedIds].sort()].join(",");
    if (draftSubmission.current?.lineIds !== lineKey) {
      draftSubmission.current = { lineIds: lineKey, id: newSubmissionId("cart") };
    }

    setIsSubmitting(true);
    try {
      await submitToNidin(
        draftSubmission.current.id,
        contributorName,
        cartLines.map((line) => ({
          item_id: line.item.id,
          quantity: line.quantity,
          selection_option_ids: line.selections.map((selection) => selection.optionId),
        })),
      );
      draftSubmission.current = null;
      setCartLines((current) => current.filter((line) => !submittedIds.has(line.id)));
      closeComposer();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "送出失敗，這筆沒有送到 Nidin。");
    } finally {
      setIsSubmitting(false);
    }
  }

  const selectedSessionSummary =
    sessions.find((session) => session.session_id === selectedSessionId) ?? selectedSessionDetail;

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
            <span className="brand-badge">共享購物車</span>
            <h1 className="header-title">{selectedSessionDetail?.session_name ?? "未開團"}</h1>
          </div>
          <div className="hero-status header-status-pill">{statusMessage}</div>
          <MenuFreshness menu={menu} />
        </div>
        <div className="header-right">
          <div className="header-metric">
            <span className="metric-label">已收單金額</span>
            <strong className="metric-value">{formatMoney(selectedSessionDetail?.total_money ?? 0)}</strong>
          </div>
          <button className="link-chip" onClick={() => navigateTo("/admin")}>
            進入後台
          </button>
        </div>
      </motion.header>

      <section className="workspace">
        <motion.div
          className="panel panel-setup"
          initial={{ opacity: 0, x: -20 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.45, delay: 0.1 }}
        >
          <div className="panel-header">
            <Ticket size={18} />
            <h2>目前揪團</h2>
          </div>

          <label className="field">
            <span>選擇團購</span>
            <CustomSelect
              ariaLabel="目前揪團"
              value={selectedSessionId}
              onChange={(val) => {
                void loadSession(val);
              }}
              options={
                sessions.length === 0
                  ? [{ value: "", label: "目前沒有開團" }]
                  : sessions.map((session) => ({
                      value: session.session_id,
                      label: session.session_name,
                      sublabel: `${formatMoney(session.total_money)} · ${session.line_count} 筆`,
                    }))
              }
              disabled={sessions.length === 0 || isLoading}
              placeholder="請選擇團購"
            />
          </label>

          <label className="field">
            <span>名字</span>
            <input
              value={contributorName}
              placeholder="你的名字（會顯示在 Nidin 上）"
              maxLength={20}
              onChange={(event) => {
                setContributorName(event.target.value);
                saveContributorName(event.target.value);
              }}
            />
          </label>

          <label className="field">
            <span>電話（選填）</span>
            <input
              value={contributorPhone}
              inputMode="tel"
              placeholder="團長聯絡用，不填也可以"
              maxLength={20}
              onChange={(event) => {
                setContributorPhone(event.target.value);
                saveContributorPhone(event.target.value);
              }}
            />
          </label>

          <button
            className="action-button"
            onClick={() => {
              void refreshSessions(selectedSessionId).then(() => {
                if (selectedSessionId) void syncFromNidin(selectedSessionId);
              });
            }}
            disabled={isLoading}
          >
            {isLoading ? <LoaderCircle className="spin" size={18} /> : <ArrowLeftRight size={18} />}
            重新整理
          </button>

          {selectedSessionSummary ? (
            <div className="delivery-card">
              <div className="panel-header panel-header-space">
                <div>
                  <h3>外送門檻</h3>
                  <p>{selectedSessionSummary.session_name}</p>
                </div>
                <strong>
                  {selectedSessionSummary.delivery_minimum != null
                    ? formatMoney(selectedSessionSummary.delivery_minimum)
                    : "未提供"}
                </strong>
              </div>
              <p>{selectedSessionSummary.order_status_reason || selectedSessionSummary.delivery_description}</p>
            </div>
          ) : null}

          {lastOfficialReceipt ? (
            <div className="receipt-card">
              <p className="eyebrow receipt-badge">已送出</p>
              <div className="panel-header">
                <CheckCircle2 size={18} />
                <h3>剛剛送出的訂單</h3>
              </div>
              <div className="receipt-lines">
                <p>訂單編號：{lastOfficialReceipt.order_id}</p>
                <p>內容：{lastOfficialReceipt.item_name}</p>
                <p>總金額：{formatMoney(lastOfficialReceipt.total_money)}</p>
              </div>
              <button
                type="button"
                className="link-text-btn"
                onClick={() => setCartTab("orders")}
              >
                查看本團所有訂單 <ChevronRight size={14} />
              </button>
            </div>
          ) : null}

          <ChatWidget
            selectedSessionId={selectedSessionId}
            contributorName={contributorName}
            menu={menu}
            setStatusMessage={setStatusMessage}
            onSubmitOrder={submitToNidin}
          />
        </motion.div>

        <MenuCatalog
          menu={menu}
          filteredItems={filteredItems}
          search={search}
          selectedCategoryId={selectedCategoryId}
          onSearchChange={setSearch}
          onCategoryChange={setSelectedCategoryId}
          onSelectItem={openComposer}
        />

        <CartPanel
          cartTab={cartTab}
          cartLines={cartLines}
          contributorName={contributorName}
          draftTotal={draftTotal}
          isSubmitting={isSubmitting}
          selectedSessionId={selectedSessionId}
          selectedSessionDetail={selectedSessionDetail}
          selectedSessionSummary={selectedSessionSummary ?? null}
          onTabChange={setCartTab}
          onRemoveLine={removeCartLine}
          onSubmitOrder={() => void submitOrder()}
          onCloneLine={cloneLineToDraft}
          myOrderIds={new Set(Object.keys(myOrders))}
          cancellingOrderId={cancellingOrderId}
          onCancelOrder={(orderId) => void cancelOrder(orderId)}
        />
      </section>

      <AnimatePresence>
        {activeItem && menu ? (
          <ItemComposerModal
            key={activeItem.id}
            activeItem={activeItem}
            menu={menu}
            closeComposer={closeComposer}
            onAddLine={(line) => setCartLines((current) => [...current, line])}
            setStatusMessage={setStatusMessage}
          />
        ) : null}
      </AnimatePresence>
    </main>
  );
}
