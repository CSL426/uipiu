import { AlertCircle, Smartphone } from "lucide-react";
import { useState, type FormEvent } from "react";

import type { LeaderAuthStatus } from "../types";
import { NidinConnectionStatus } from "./NidinConnectionStatus";

interface NidinPasswordLoginProps {
  leaderAuth: LeaderAuthStatus | null;
  onBound: (auth: LeaderAuthStatus) => void;
  onClear: () => void;
  onCancel: () => void;
  /** 檢查連線時自動重登過，憑證與到期日已變，請上層重新讀取狀態 */
  onStatusChanged?: () => void;
}

export function NidinPasswordLogin({ leaderAuth, onBound, onClear, onCancel, onStatusChanged }: NidinPasswordLoginProps) {
  const [account, setAccount] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const res = await fetch("/api/leader/auth/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account: account.trim(), password, remember }),
      });
      if (res.ok) {
        setPassword("");
        onBound((await res.json()) as LeaderAuthStatus);
        return;
      }
      const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
      setError(
        res.status === 422
          ? "手機號碼格式應為 09 開頭的 10 碼數字。"
          : typeof body.detail === "string"
            ? body.detail
            : "登入 Nidin 失敗，請稍後再試。",
      );
    } catch {
      setError("連線失敗，請稍後再試。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={(event) => void submit(event)}>
      <p style={{ fontSize: "0.875rem", color: "var(--muted)", margin: "0 0 1rem", lineHeight: 1.5 }}>
        用你在 Nidin 官網設定的手機號碼與密碼登入，不需要安裝任何東西。平常用 LINE／Google 登入
        Nidin、沒設過密碼的話，可以到官網用「忘記密碼」設一組，或改用「LINE／Google」分頁。
      </p>

      {leaderAuth?.is_authenticated ? <NidinConnectionStatus onRelogged={onStatusChanged} /> : null}

      <label className="field" style={{ marginBottom: "0.75rem" }}>
        <span>Nidin 手機號碼</span>
        <input
          autoComplete="username"
          inputMode="numeric"
          placeholder="09xxxxxxxx"
          value={account}
          onChange={(e) => setAccount(e.target.value)}
          required
        />
      </label>
      <label className="field" style={{ marginBottom: "0.75rem" }}>
        <span>Nidin 密碼</span>
        <input
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
      </label>
      <label style={{ display: "flex", alignItems: "flex-start", gap: "0.5rem", fontSize: "0.85rem", marginBottom: "1rem" }}>
        <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
        <span>
          記住密碼（加密儲存），憑證失效時自動重新登入
          <br />
          <span style={{ color: "var(--muted)", fontSize: "0.78rem" }}>不勾的話只存憑證，失效時要再輸入一次</span>
        </span>
      </label>

      {error ? (
        <p role="alert" style={{ display: "flex", alignItems: "center", gap: "0.35rem", color: "var(--danger, #ef4444)", fontSize: "0.85rem" }}>
          <AlertCircle size={14} />
          {error}
        </p>
      ) : null}

      <div style={{ display: "flex", justifyContent: "space-between", gap: "0.5rem", alignItems: "center" }}>
        <div>
          {leaderAuth?.is_authenticated ? (
            <button type="button" className="btn-secondary" style={{ color: "var(--danger, #ef4444)" }} onClick={onClear}>
              解除綁定
            </button>
          ) : null}
        </div>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
          <button type="button" className="btn-secondary" onClick={onCancel}>
            取消
          </button>
          <button className="action-button" style={{ marginTop: 0 }} disabled={busy} type="submit">
            <Smartphone size={14} style={{ marginRight: "0.35rem" }} />
            {busy ? "登入中…" : "登入並綁定"}
          </button>
        </div>
      </div>
    </form>
  );
}
