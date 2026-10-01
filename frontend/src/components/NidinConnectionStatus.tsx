import { AlertCircle, CheckCircle2, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

import type { LeaderAuthCheck } from "../types";

type CheckState =
  | { kind: "checking" }
  | { kind: "ok"; result: LeaderAuthCheck }
  | { kind: "failed"; message: string };

/** 憑證到期日（本地時間），例如 2026/10/24 16:14 */
export function formatExpiry(expiresAt: number): string {
  const d = new Date(expiresAt * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/**
 * 實際向 Nidin 讀一次會員資料來證明連線可用。
 * 資料庫裡有憑證不代表 Nidin 還認它，所以一打開就檢查，不要只顯示「已綁定」。
 */
export function NidinConnectionStatus({ onRelogged }: { onRelogged?: () => void }) {
  const [state, setState] = useState<CheckState>({ kind: "checking" });

  async function check() {
    setState({ kind: "checking" });
    try {
      const res = await fetch("/api/leader/auth/check", { method: "POST" });
      const body = (await res.json().catch(() => ({}))) as LeaderAuthCheck & { detail?: unknown };
      if (!res.ok) {
        setState({
          kind: "failed",
          message: typeof body.detail === "string" ? body.detail : "檢查失敗，請稍後再試。",
        });
        return;
      }
      setState({ kind: "ok", result: body });
      if (body.relogged_in) onRelogged?.();
    } catch {
      setState({ kind: "failed", message: "連不上本站伺服器，請稍後再試。" });
    }
  }

  useEffect(() => {
    void check();
    // 只在打開時自動檢查一次，之後由使用者按按鈕重測
  }, []);

  const box = {
    border: "1px solid var(--line)",
    borderRadius: "0.5rem",
    padding: "0.75rem 0.9rem",
    marginBottom: "1rem",
    fontSize: "0.85rem",
    lineHeight: 1.6,
  } as const;

  return (
    <div role="status" aria-live="polite" style={box}>
      {state.kind === "checking" ? (
        <span style={{ display: "flex", alignItems: "center", gap: "0.4rem", color: "var(--muted)" }}>
          <RefreshCw size={14} className="spin" />
          正在向 Nidin 確認連線…
        </span>
      ) : state.kind === "ok" ? (
        <>
          <strong style={{ display: "flex", alignItems: "center", gap: "0.4rem", color: "var(--success, #10b981)" }}>
            <CheckCircle2 size={15} />
            Nidin 連線正常
          </strong>
          <div>
            會員：{state.result.display_name || "（未設定名稱）"}（ID {state.result.nidin_user_id}）
          </div>
          {state.result.expires_at ? <div>憑證有效到：{formatExpiry(state.result.expires_at)}</div> : null}
          <div>
            {state.result.auto_relogin
              ? "到期或失效時會自動重新登入，不用管它。"
              : "沒有記住密碼：到期後要回來重新登入一次。"}
          </div>
          {state.result.relogged_in ? <div>剛才舊憑證已失效，已自動重新登入。</div> : null}
        </>
      ) : (
        <strong style={{ display: "flex", alignItems: "center", gap: "0.4rem", color: "var(--danger, #ef4444)" }}>
          <AlertCircle size={15} />
          {state.message}
        </strong>
      )}
      {state.kind !== "checking" ? (
        <button
          type="button"
          className="btn-secondary"
          style={{ marginTop: "0.5rem", minHeight: "1.75rem", fontSize: "0.8rem" }}
          onClick={() => void check()}
        >
          <RefreshCw size={12} />
          重新檢查
        </button>
      ) : null}
    </div>
  );
}
