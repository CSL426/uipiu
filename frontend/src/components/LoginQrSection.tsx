import { Check, Copy, Edit2, Globe, QrCode, RefreshCw } from "lucide-react";
import { useState } from "react";

import { isLoopbackHostname } from "../lib/authHost";
import { copyText } from "../lib/clipboard";

export type QrStatus = "idle" | "loading" | "pending" | "authorized" | "expired";

export interface QrTicket {
  ticket: string;
  expires_at: number;
  auth_url: string;
  final_url: string;
  qrDataUrl?: string;
}

interface LoginQrSectionProps {
  qrTicket: QrTicket | null;
  qrStatus: QrStatus;
  customHost: string;
  hostInput: string;
  isEditingHost: boolean;
  onHostInputChange: (value: string) => void;
  onToggleEditHost: () => void;
  onSaveHost: () => void;
  onRefreshTicket: () => void;
}

export function LoginQrSection({
  qrTicket,
  qrStatus,
  customHost,
  hostInput,
  isEditingHost,
  onHostInputChange,
  onToggleEditHost,
  onSaveHost,
  onRefreshTicket,
}: LoginQrSectionProps) {
  const [copiedAuthLink, setCopiedAuthLink] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const isLocalhost = typeof window !== "undefined" && isLoopbackHostname(window.location.hostname);

  return (
    <div className="login-qr-side">
      <div style={{ display: "flex", alignItems: "center", gap: "0.4rem", marginBottom: "0.35rem" }}>
        <QrCode size={18} color="var(--primary)" />
        <strong style={{ fontSize: "1.05rem" }}>手機掃碼快速登入</strong>
      </div>
      <p style={{ fontSize: "0.82rem", color: "var(--muted)", margin: "0 0 1rem" }}>
        用手機掃碼，在手機上登入並按確認，這台電腦就會自動進入後台
      </p>

      {qrStatus === "loading" ? (
        <div style={{ padding: "2.5rem 0", color: "var(--muted)" }}>
          <RefreshCw size={28} className="spin" style={{ margin: "0 auto 0.5rem" }} />
          <p style={{ fontSize: "0.85rem" }}>產生登入 QR 碼中…</p>
        </div>
      ) : qrStatus === "expired" ? (
        <div style={{ padding: "1.5rem 0" }}>
          <p style={{ color: "var(--danger, #ef4444)", fontSize: "0.875rem", marginBottom: "0.75rem" }}>
            QR 碼已逾期失效
          </p>
          <button className="action-button" type="button" onClick={onRefreshTicket}>
            <RefreshCw size={14} style={{ marginRight: "0.35rem" }} />
            重新產生 QR 碼
          </button>
        </div>
      ) : qrTicket?.qrDataUrl ? (
        <div>
          <div
            style={{
              background: "#ffffff",
              padding: "0.6rem",
              borderRadius: "0.75rem",
              display: "inline-block",
              border: "1px solid var(--line)",
              boxShadow: "0 2px 10px rgba(0,0,0,0.06)",
            }}
          >
            <img
              src={qrTicket.qrDataUrl}
              alt="主揪登入 QR 碼"
              style={{ width: "11rem", height: "11rem", display: "block" }}
            />
          </div>

          <p style={{ fontSize: "0.8rem", color: "var(--muted)", margin: "0.6rem 0 0.4rem" }}>
            {qrStatus === "authorized" ? (
              <span style={{ color: "var(--success, #10b981)", fontWeight: 600 }}>
                ✓ 手機已授權！正在完成登入…
              </span>
            ) : (
              "支援手機相機、LINE 掃碼器直接掃描"
            )}
          </p>

          <div style={{ display: "flex", justifyContent: "center", gap: "0.5rem", marginTop: "0.75rem", flexWrap: "wrap" }}>
            <button type="button" className="btn-secondary" onClick={onRefreshTicket}>
              <RefreshCw size={13} />
              重新整理
            </button>
            {qrTicket.final_url ? (
              <button
                type="button"
                className="btn-secondary"
                onClick={() => {
                  void copyText(qrTicket.final_url).then((ok) => {
                    setCopyFailed(!ok);
                    if (!ok) return;
                    setCopiedAuthLink(true);
                    setTimeout(() => setCopiedAuthLink(false), 2000);
                  });
                }}
              >
                {copiedAuthLink ? <Check size={13} color="var(--success)" /> : <Copy size={13} />}
                {copiedAuthLink ? "已複製！" : "複製手機授權連結"}
              </button>
            ) : null}
          </div>
          {copyFailed && qrTicket.final_url ? (
            <input
              aria-label="手機授權連結"
              className="host-edit-input"
              readOnly
              value={qrTicket.final_url}
              onFocus={(e) => e.currentTarget.select()}
              style={{ width: "100%", marginTop: "0.5rem", fontSize: "0.75rem" }}
            />
          ) : null}

          {/* 電腦開 localhost 時，手機掃到的網址要換成區網 IP 才連得到 */}
          <div className="host-config-card">
            <div className="host-config-header">
              <div style={{ display: "flex", alignItems: "center", gap: "0.35rem", color: "var(--muted)" }}>
                <Globe size={13} />
                <span>掃碼位址：<strong style={{ color: "var(--text)" }}>{customHost || "localhost"}</strong></span>
              </div>
              <button
                type="button"
                className="btn-secondary"
                style={{ minHeight: "1.6rem", padding: "0.15rem 0.55rem", fontSize: "0.75rem" }}
                onClick={onToggleEditHost}
              >
                <Edit2 size={11} />
                {isEditingHost ? "收起" : "更換"}
              </button>
            </div>

            {isLocalhost && !isEditingHost ? (
              <p style={{ margin: "0.35rem 0 0", color: "var(--muted)", fontSize: "0.72rem", lineHeight: 1.4 }}>
                💡 電腦用 localhost 時，手機連不到。請點「更換」填這台電腦的區網 IP（如 192.168.x.x:6464）。
              </p>
            ) : null}

            {isEditingHost ? (
              <div className="host-edit-row">
                <input
                  className="host-edit-input"
                  value={hostInput}
                  placeholder="例如 192.168.1.100:6464 或 ngrok 網址"
                  onChange={(e) => onHostInputChange(e.target.value)}
                />
                <button
                  type="button"
                  className="btn-primary-sm"
                  style={{ minHeight: "1.75rem", padding: "0.2rem 0.65rem", fontSize: "0.75rem" }}
                  onClick={onSaveHost}
                >
                  更新
                </button>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
