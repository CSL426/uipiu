import { Bot, Check, CheckCircle2, LoaderCircle, Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createLineId, formatMoney } from "../lib/menu";
import { speak, stopSpeaking, toSpeechText } from "../lib/speech";
import { contentKey, type LiveOrderLine } from "../lib/liveOrder";
import type {
  AgentOrderResponse,
  AgentParsedItemPreview,
  CartSessionDetail,
  ChatMessage,
  NormalizedMenu,
} from "../types";
import { PreviewItemRow } from "./PreviewItemRow";

interface ChatWidgetProps {
  selectedSessionId: string;
  contributorName: string;
  menu: NormalizedMenu | null;
  /** 送進 Nidin；同一張預覽卡重按帶同一個 submissionId，後端才擋得住重複下單 */
  onSubmitOrder: (submissionId: string, contributorName: string, lines: LiveOrderLine[]) => Promise<CartSessionDetail>;
  setStatusMessage: (message: string) => void;
}

function formatChatTime(): string {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function extractQuickReplies(text: string): string[] {
  const match = text.match(/（(?:例如|例如：|如：)([^）]+)）/);
  if (!match?.[1]) return [];
  return match[1].split(/[,，、]/).map((s) => s.trim()).filter(Boolean);
}

export function ChatWidget({ selectedSessionId, contributorName, menu, onSubmitOrder, setStatusMessage }: ChatWidgetProps) {
  const [chatInputText, setChatInputText] = useState("");
  const [isChatSubmitting, setIsChatSubmitting] = useState(false);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([
    {
      id: "welcome-1",
      sender: "bot",
      text: "哈囉！我是比比拉布，可以直接告訴我想喝什麼（例如「+1 四季春 微糖微冰」）！我會先幫您解析預覽，確認後再送出加入購物車。",
      createdAt: formatChatTime(),
    },
  ]);
  const messagesRef = useRef<HTMLDivElement>(null);
  const messageCount = chatMessages.length;

  // 離開頁面時閉嘴，不然聲音會跟著使用者跑到後台
  useEffect(() => stopSpeaking, []);

  // 只在「多了一則訊息」時捲，而且只捲聊天框自己。
  // 原本用 scrollIntoView 並監聽整個 chatMessages：它會連整個網頁一起捲，
  // 使用者在預覽卡上每按一下（改數量、規格都會原地更新訊息）頁面就被拉到聊天框底部。
  useEffect(() => {
    const box = messagesRef.current;
    if (box && typeof box.scrollTo === "function") {
      box.scrollTo({ top: box.scrollHeight, behavior: "smooth" });
    }
  }, [messageCount]);

  async function handleSendChatMessage(rawText?: string) {
    const textToSend = (rawText ?? chatInputText).trim();
    if (!textToSend) return;
    if (!selectedSessionId) {
      setStatusMessage("先選一組團購。");
      return;
    }

    const userMsg: ChatMessage = {
      id: createLineId(),
      sender: "user",
      text: textToSend,
      createdAt: formatChatTime(),
    };

    // 使用者又開口了就別再唸上一句：解析要等 LLM 回來，不先停會疊著講好幾秒
    stopSpeaking();
    setChatMessages((prev) => [...prev, userMsg]);
    setChatInputText("");
    setIsChatSubmitting(true);

    try {
      const response = await fetch(`/api/cart-sessions/${selectedSessionId}/agent-order`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: textToSend,
          contributor_name: contributorName,
          dry_run: true,
        }),
      });

      if (!response.ok) {
        const errorPayload = await response.json();
        throw new Error(errorPayload.detail || "AI 解析點單失敗。");
      }

      const payload = (await response.json()) as AgentOrderResponse;

      if (payload.action === "preview" && payload.parsed_items && payload.parsed_items.length > 0) {
        const botMsg: ChatMessage = {
          id: createLineId(),
          sender: "bot",
          text: payload.reply_message || "已為您解析點單，請確認品項與規格：",
          createdAt: formatChatTime(),
          preview: {
            parsed_items: payload.parsed_items,
            total_price: payload.total_price ?? 0,
            contributor_name: payload.contributor_name || contributorName,
            confirmed: false,
            rawText: textToSend,
          },
        };
        setChatMessages((prev) => [...prev, botMsg]);
        setStatusMessage("請在比比拉布中確認品項後送出。");
        // 預覽卡已經畫出來了，語音晚幾百毫秒再開口，不擋畫面
        void speak(toSpeechText(botMsg.text, payload.parsed_items.length, payload.total_price ?? 0));
      } else {
        const quickReplies = extractQuickReplies(payload.reply_message);
        const botMsg: ChatMessage = {
          id: createLineId(),
          sender: "bot",
          text: payload.reply_message || "請確認點單內容。",
          createdAt: formatChatTime(),
          quickReplies: quickReplies.length > 0 ? quickReplies : undefined,
        };
        setChatMessages((prev) => [...prev, botMsg]);
        setStatusMessage(payload.reply_message);
        void speak(toSpeechText(botMsg.text));
      }
    } catch (error) {
      const errorMsg: ChatMessage = {
        id: createLineId(),
        sender: "bot",
        text: `⚠️ ${error instanceof Error ? error.message : "AI 解析時發生錯誤。"}`,
        createdAt: formatChatTime(),
      };
      setChatMessages((prev) => [...prev, errorMsg]);
      setStatusMessage(error instanceof Error ? error.message : "AI 解析時發生錯誤。");
    } finally {
      setIsChatSubmitting(false);
    }
  }

  async function handleConfirmPreview(msgId: string, preview: NonNullable<ChatMessage["preview"]>) {
    stopSpeaking();
    // 用「現在」名字欄的名字：解析當下常還沒填名字（得到「訪客」），補填後重按要能送
    const name = contributorName.trim() || preview.contributor_name;
    const lines = preview.parsed_items.map((it) => ({
      item_id: it.item_id,
      quantity: it.quantity,
      selection_option_ids: it.selection_option_ids,
    }));
    // 同一張卡、同一團、同一份內容才沿用同一個 id；改了杯數或規格就是新的一筆
    const submissionId = `chat-${contentKey([selectedSessionId, name, lines])}-${msgId}`
      .replace(/[^A-Za-z0-9_-]/g, "")
      .slice(0, 64);
    setIsChatSubmitting(true);
    try {
      await onSubmitOrder(submissionId, name, lines);

      setChatMessages((prev) =>
        prev.map((m) =>
          m.id === msgId && m.preview ? { ...m, preview: { ...m.preview, confirmed: true } } : m,
        ),
      );

      const confirmBotMsg: ChatMessage = {
        id: createLineId(),
        sender: "bot",
        text: `🎉 已送到 Nidin 的團裡（${preview.parsed_items.length} 筆，共 ${formatMoney(preview.total_price)}）。要改的話請在「本團訂單」取消這筆再重點。`,
        createdAt: formatChatTime(),
      };
      setChatMessages((prev) => [...prev, confirmBotMsg]);
    } catch (error) {
      const message = error instanceof Error ? error.message : "送出失敗，這筆沒有送到 Nidin。";
      setStatusMessage(message);
      setChatMessages((prev) => [
        ...prev,
        { id: createLineId(), sender: "bot", text: `⚠️ ${message}`, createdAt: formatChatTime() },
      ]);
    } finally {
      setIsChatSubmitting(false);
    }
  }

  function handleUpdateParsedItem(msgId: string, itemIdx: number, updatedItem: AgentParsedItemPreview) {
    setChatMessages((prev) =>
      prev.map((m) => {
        if (m.id !== msgId || !m.preview) return m;
        const newParsed = [...m.preview.parsed_items];
        newParsed[itemIdx] = updatedItem;
        const newTotal = newParsed.reduce((sum, it) => sum + it.line_total, 0);
        return {
          ...m,
          preview: {
            ...m.preview,
            parsed_items: newParsed,
            total_price: newTotal,
          },
        };
      }),
    );
  }

  function handleCancelPreview(msgId: string) {
    stopSpeaking();
    setChatMessages((prev) =>
      prev.map((m) =>
        m.id === msgId && m.preview ? { ...m, preview: { ...m.preview, cancelled: true } } : m,
      ),
    );
    const cancelMsg: ChatMessage = {
      id: createLineId(),
      sender: "bot",
      text: "已為您取消該筆預覽，可以重新告訴我想點什麼。",
      createdAt: formatChatTime(),
    };
    setChatMessages((prev) => [...prev, cancelMsg]);
  }

  function handleQuickReplyClick(replyText: string) {
    const lastUserMsg = [...chatMessages].reverse().find((m) => m.sender === "user");
    const combined = lastUserMsg ? `${lastUserMsg.text} ${replyText}` : replyText;
    void handleSendChatMessage(combined);
  }

  function handleClearChat() {
    stopSpeaking();
    setChatMessages([
      {
        id: createLineId(),
        sender: "bot",
        text: "對話已重置。我是比比拉布，可以直接告訴我想喝什麼，我會先幫您解析預覽！",
        createdAt: formatChatTime(),
      },
    ]);
  }

  const sampleItem1 = menu?.items[0]?.name ?? "紅茶拿鐵";
  const sampleItem2 = menu?.items[1]?.name ?? "珍珠奶茶";

  return (
    <div className="chat-widget">
      <div className="chat-header">
        <div className="chat-header-title">
          <Bot size={18} style={{ color: "var(--accent)" }} />
          <span>比比拉布</span>
          <span className="chat-status-dot" title="在線中" />
        </div>
        <button
          type="button"
          className="ghost-button ghost-button-text"
          style={{ fontSize: "0.75rem", padding: "0.2rem 0.5rem" }}
          onClick={handleClearChat}
          title="重置對話"
        >
          重置對話
        </button>
      </div>

      <div className="chat-messages-container" ref={messagesRef}>
        {chatMessages.map((msg) => (
          <div
            key={msg.id}
            className={`chat-msg ${msg.sender === "user" ? "chat-msg-user" : "chat-msg-bot"}`}
          >
            <div className={`chat-bubble ${msg.sender === "user" ? "chat-bubble-user" : "chat-bubble-bot"}`}>
              {msg.text}

              {/* Dry-run 預覽確認卡片 */}
              {msg.preview ? (
                <div className="chat-preview-card">
                  <div className="preview-card-header">
                    <span>📋 點單內容預覽（{msg.preview.contributor_name}）</span>
                    <span>{formatMoney(msg.preview.total_price)}</span>
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: "0.45rem" }}>
                    {msg.preview.parsed_items.map((it, idx) => (
                      <PreviewItemRow
                        key={`${it.item_id}-${idx}`}
                        item={it}
                        menu={menu}
                        disabled={msg.preview?.confirmed || msg.preview?.cancelled || isChatSubmitting}
                        onUpdateItem={(updated) => handleUpdateParsedItem(msg.id, idx, updated)}
                      />
                    ))}
                  </div>

                  <div className="preview-card-footer">
                    {msg.preview.confirmed ? (
                      <span className="preview-confirmed-badge">
                        <CheckCircle2 size={14} /> 已送到 Nidin
                      </span>
                    ) : msg.preview.cancelled ? (
                      <span className="preview-confirmed-badge" style={{ color: "var(--muted)" }}>
                        已取消
                      </span>
                    ) : (
                      <>
                        <span style={{ fontSize: "0.78rem", color: "var(--muted)" }}>
                          共 {msg.preview.parsed_items.reduce((s, i) => s + i.quantity, 0)} 杯
                        </span>
                        <div className="preview-actions">
                          <button
                            type="button"
                            className="btn-cancel-preview"
                            onClick={() => handleCancelPreview(msg.id)}
                            disabled={isChatSubmitting}
                          >
                            取消
                          </button>
                          <button
                            type="button"
                            className="btn-confirm-order"
                            onClick={() => void handleConfirmPreview(msg.id, msg.preview!)}
                            disabled={isChatSubmitting}
                          >
                            {isChatSubmitting ? <LoaderCircle className="spin" size={14} /> : <Check size={14} />}
                            確認送出
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                </div>
              ) : null}

              {/* 澄清問題時的快速選項按鈕 */}
              {msg.quickReplies && msg.quickReplies.length > 0 ? (
                <div className="chat-quick-replies">
                  {msg.quickReplies.map((reply, ridx) => (
                    <button
                      key={ridx}
                      type="button"
                      className="quick-reply-pill"
                      onClick={() => handleQuickReplyClick(reply)}
                    >
                      + {reply}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
            <span className="chat-timestamp">{msg.createdAt}</span>
          </div>
        ))}
      </div>

      <div className="chat-input-bar">
        <div className="ai-quick-pills" style={{ margin: 0 }}>
          <span style={{ fontSize: "0.75rem", color: "var(--muted)", alignSelf: "center" }}>範例：</span>
          <button
            type="button"
            className="quick-pill"
            aria-label="快速代入範例一"
            onClick={() => void handleSendChatMessage(`${contributorName} +1 ${sampleItem1} 微糖微冰`)}
          >
            +1 {sampleItem1} 微糖微冰
          </button>
          <button
            type="button"
            className="quick-pill"
            aria-label="快速代入範例二"
            onClick={() => void handleSendChatMessage(`${contributorName} +1 ${sampleItem2} 半糖少冰`)}
          >
            +1 {sampleItem2} 半糖少冰
          </button>
        </div>
        <div className="chat-input-row">
          <input
            className="chat-input-field"
            placeholder="告訴我想點什麼，例如：小明 +2 奶茶 微糖微冰"
            value={chatInputText}
            onChange={(e) => setChatInputText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void handleSendChatMessage();
              }
            }}
            disabled={isChatSubmitting || !selectedSessionId}
          />
          <button
            type="button"
            className="chat-send-button"
            onClick={() => void handleSendChatMessage()}
            disabled={isChatSubmitting || !selectedSessionId || !chatInputText.trim()}
            title="送出點單解析"
          >
            {isChatSubmitting ? <LoaderCircle className="spin" size={16} /> : <Send size={16} />}
          </button>
        </div>
      </div>
    </div>
  );
}
