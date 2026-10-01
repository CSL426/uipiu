import { Check, Copy, RefreshCw, Smartphone } from "lucide-react";
import { useState } from "react";

import { copyText } from "../lib/clipboard";
import type { QrTicket } from "./LoginQrSection";

interface QuickLoginModalProps {
  username: string;
  ticket: QrTicket | null;
  onRegenerate: () => void;
  onClose: () => void;
}

export function QuickLoginModal({ username, ticket, onRegenerate, onClose }: QuickLoginModalProps) {
  const [copiedQuickLink, setCopiedQuickLink] = useState(false);
  const shareUrl = ticket ? ticket.final_url || ticket.auth_url : "";

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" style={{ textAlign: "center" }} onClick={(e) => e.stopPropagation()}>
        <div className="panel-header" style={{ marginBottom: "0.75rem", justifyContent: "center" }}>
          <Smartphone size={20} />
          <h3>手機快速登入 QR 碼</h3>
        </div>
        <p style={{ fontSize: "0.875rem", color: "var(--muted)", marginBottom: "1rem", lineHeight: 1.5 }}>
          拿起手機相機或 LINE 掃描下方 QR 碼，即可直接在手機上以 <strong>{username}</strong> 身分免密碼登入後台。
        </p>

        {ticket?.qrDataUrl ? (
          <div>
            <div
              style={{
                background: "#ffffff",
                padding: "0.75rem",
                borderRadius: "0.75rem",
                display: "inline-block",
                border: "1px solid var(--line)",
                boxShadow: "0 2px 8px rgba(0,0,0,0.06)",
                marginBottom: "1rem",
              }}
            >
              <img
                src={ticket.qrDataUrl}
                alt="手機快速登入 QR 碼"
                style={{ width: "13rem", height: "13rem", display: "block" }}
              />
            </div>
            <div style={{ display: "flex", justifyContent: "center", gap: "0.5rem", marginBottom: "0.75rem", flexWrap: "wrap" }}>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => {
                  void copyText(shareUrl).then((ok) => {
                    if (!ok) return;
                    setCopiedQuickLink(true);
                    setTimeout(() => setCopiedQuickLink(false), 2000);
                  });
                }}
              >
                {copiedQuickLink ? <Check size={14} color="var(--success)" /> : <Copy size={14} />}
                {copiedQuickLink ? "已複製連結！" : "複製快速登入連結"}
              </button>
              <button type="button" className="btn-secondary" onClick={onRegenerate}>
                <RefreshCw size={14} />
                重新產生
              </button>
            </div>
            <p style={{ fontSize: "0.8rem", color: "var(--muted)", margin: "0 0 0.5rem" }}>
              連線網址：<span style={{ userSelect: "all", wordBreak: "break-all" }}>{shareUrl}</span>
            </p>
            <p style={{ fontSize: "0.78rem", color: "var(--muted)", margin: 0 }}>
              有效期限：5 分鐘（單次使用）
            </p>
          </div>
        ) : (
          <div style={{ padding: "2rem 0", color: "var(--muted)" }}>
            <RefreshCw size={24} className="spin" style={{ marginBottom: "0.5rem" }} />
            <p>產生中…</p>
          </div>
        )}

        <div style={{ marginTop: "1.25rem" }}>
          <button type="button" className="action-button" style={{ width: "100%" }} onClick={onClose}>
            關閉
          </button>
        </div>
      </div>
    </div>
  );
}
