import { KeyRound, Smartphone } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

import { CreateAccountForm } from "./components/CreateAccountForm";
import { LoginQrSection, type QrStatus, type QrTicket } from "./components/LoginQrSection";
import { QuickLoginModal } from "./components/QuickLoginModal";
import { readInitialHost, resolveTicketUrl, saveHost } from "./lib/authHost";
import { generateQrDataUrl } from "./lib/qr";

interface Account {
  id: number;
  username: string;
  is_admin: boolean;
}

async function loginErrorMessage(response: Response, mode: "nidin" | "site"): Promise<string> {
  if (response.status === 429) return "嘗試次數太多，請稍候再試。";
  if (mode === "site") {
    // 422 是帳密格式不合（例如密碼不足 12 碼），對使用者來說就是帳密不對，不是伺服器壞掉
    return response.status === 401 || response.status === 422
      ? "帳號或密碼不正確。要用 Nidin 手機號碼登入請切到「Nidin 帳號」。"
      : "登入失敗，請稍後再試。";
  }
  if (response.status === 422) return "手機號碼格式應為 09 開頭的 10 碼數字。";
  const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
  if (typeof body.detail === "string" && (response.status === 401 || response.status === 403)) {
    return body.detail;
  }
  return "登入失敗，請稍後再試。";
}

export function AdminAccess({ children }: { children: ReactNode }) {
  const [account, setAccount] = useState<Account | null>(null);
  const [checking, setChecking] = useState(true);
  const [busy, setBusy] = useState(false);

  // Form states：預設用 Nidin 帳號，本站帳號留給還沒綁定或沒有 Nidin 的人
  const [loginMode, setLoginMode] = useState<"nidin" | "site">("nidin");
  const [rememberNidin, setRememberNidin] = useState(true);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [newUsername, setNewUsername] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  // LAN Host configuration for mobile scanning
  const [customHost, setCustomHost] = useState<string>(readInitialHost);
  const [isEditingHost, setIsEditingHost] = useState(false);
  const [hostInput, setHostInput] = useState(customHost);

  // QR Login States
  const [qrTicket, setQrTicket] = useState<QrTicket | null>(null);
  const [qrStatus, setQrStatus] = useState<QrStatus>("idle");
  const pollingRef = useRef<number | null>(null);

  // Cross-device authorization states from URL
  const [pendingAuthTicket, setPendingAuthTicket] = useState<string | null>(null);
  const [authorizing, setAuthorizing] = useState(false);
  const [approvedDevice, setApprovedDevice] = useState(false);

  // Quick Login Modal (for logged-in users to share with mobile)
  const [showQuickLoginModal, setShowQuickLoginModal] = useState(false);
  const [quickLoginTicket, setQuickLoginTicket] = useState<QrTicket | null>(null);

  // Stop QR polling helper
  const stopPolling = () => {
    if (pollingRef.current) {
      window.clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  };

  // Check current session on mount and handle query params
  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const params = new URLSearchParams(window.location.search);
        const claimTicket = params.get("claim_ticket");
        const authTicket = params.get("auth_ticket");

        // 1. If claim_ticket is in URL, attempt immediate claim
        if (claimTicket) {
          try {
            const claimRes = await fetch("/api/auth/qr/claim", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ ticket: claimTicket }),
            });
            if (claimRes.ok) {
              const user = (await claimRes.json()) as Account;
              if (active) {
                setAccount(user);
                setNotice("已透過 QR 碼快速登入！");
                window.history.replaceState(null, "", window.location.pathname);
              }
              return;
            } else {
              if (active) setError("快速登入 QR 碼已失效或過期，請重新掃碼。");
            }
          } catch {
            if (active) setError("快速登入連線失敗，請稍後再試。");
          }
        }

        // 2. Check /api/auth/me
        const response = await fetch("/api/auth/me");
        if (response.status === 401) {
          if (authTicket && active) {
            setPendingAuthTicket(authTicket);
          }
          return;
        }
        if (!response.ok) throw new Error();
        const user = (await response.json()) as Account;
        if (active) {
          setAccount(user);
          if (authTicket) {
            setPendingAuthTicket(authTicket);
          }
        }
      } catch {
        if (active) setError("無法確認登入狀態，請稍後重新登入。");
      } finally {
        if (active) setChecking(false);
      }
    })();
    return () => {
      active = false;
      stopPolling();
    };
  }, []);

  // Fetch QR ticket on load if not logged in
  useEffect(() => {
    // 掃碼進來的手機是來替電腦授權的，再產生一張自己的 QR 只會讓畫面跟電腦一模一樣
    if (!checking && !account && !pendingAuthTicket) {
      void fetchQrTicket();
    }
    return () => {
      stopPolling();
    };
  }, [checking, account, pendingAuthTicket]);

  // Fetch a new QR ticket for login
  async function fetchQrTicket(overrideHost?: string) {
    stopPolling();
    setQrStatus("loading");
    setError("");
    const effectiveHost = overrideHost !== undefined ? overrideHost : customHost;
    try {
      const res = await fetch("/api/auth/qr/ticket", { method: "POST" });
      if (!res.ok) throw new Error();
      const data = (await res.json()) as { ticket: string; expires_at: number; auth_url: string };
      const finalUrl = resolveTicketUrl(data.ticket, data.auth_url, effectiveHost, "auth_ticket");
      const qrDataUrl = await generateQrDataUrl(finalUrl);
      setQrTicket({ ...data, final_url: finalUrl, qrDataUrl });
      setQrStatus("pending");

      // Start polling
      pollingRef.current = window.setInterval(() => {
        void (async () => {
          try {
            const statusRes = await fetch(`/api/auth/qr/status?ticket=${data.ticket}`);
            if (!statusRes.ok) return;
            const statusData = (await statusRes.json()) as { status: string; username?: string };
            if (statusData.status === "authorized") {
              stopPolling();
              setQrStatus("authorized");
              // Claim the authorized ticket
              const claimRes = await fetch("/api/auth/qr/claim", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ ticket: data.ticket }),
              });
              if (claimRes.ok) {
                const user = (await claimRes.json()) as Account;
                setAccount(user);
                setNotice(`歡迎回來，${user.username}！已透過 QR 碼完成登入。`);
              }
            } else if (statusData.status === "expired") {
              stopPolling();
              setQrStatus("expired");
            }
          } catch {
            // Ignore temporary network errors during polling
          }
        })();
      }, 2000);
    } catch {
      setQrStatus("idle");
      setError("無法產生登入 QR 碼，請稍後再試。");
    }
  }

  // Update custom host
  function handleSaveCustomHost() {
    const trimmed = hostInput.trim();
    if (!trimmed) return;
    setCustomHost(trimmed);
    saveHost(trimmed);
    setIsEditingHost(false);
    void fetchQrTicket(trimmed);
  }

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response =
        loginMode === "nidin"
          ? await fetch("/api/auth/nidin-login", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ account: username.trim(), password, remember: rememberNidin }),
            })
          : await fetch("/api/auth/login", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ username: username.trim(), password }),
            });
      if (!response.ok) {
        setError(await loginErrorMessage(response, loginMode));
        return;
      }
      setAccount((await response.json()) as Account);
      setPassword("");
      setUsername("");
      stopPolling();
    } catch {
      setError("無法連線，請稍後再試。");
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/auth/logout", { method: "POST" });
      if (!response.ok) throw new Error();
      setAccount(null);
      setNewUsername("");
      setNewPassword("");
      setNotice("");
      stopPolling();
    } catch {
      setError("登出失敗，請稍後再試。");
    } finally {
      setBusy(false);
    }
  }

  async function createAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setNotice("");
    if (newPassword.length < 12) {
      setError("新主揪密碼至少需要 12 個字元。");
      return;
    }
    setBusy(true);
    try {
      const response = await fetch("/api/auth/accounts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: newUsername.trim(), password: newPassword }),
      });
      if (!response.ok) {
        setError(response.status === 409 ? "這個主揪帳號已存在。" : "新增主揪失敗，請確認帳號與權限後再試。");
        return;
      }
      const created = (await response.json()) as Account;
      setNotice("已新增主揪 " + created.username + "。");
      setNewUsername("");
      setNewPassword("");
    } catch {
      setError("無法連線，請稍後再試。");
    } finally {
      setBusy(false);
    }
  }

  // Authorize another device that scanned our QR code
  async function authorizePendingTicket() {
    if (!pendingAuthTicket) return;
    setAuthorizing(true);
    setError("");
    try {
      const res = await fetch("/api/auth/qr/authorize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticket: pendingAuthTicket }),
      });
      if (res.ok) {
        setApprovedDevice(true);
        setPendingAuthTicket(null);
        window.history.replaceState(null, "", window.location.pathname);
      } else {
        setError("授權失敗或 QR 碼已失效，請重新整理電腦端 QR 碼。");
      }
    } catch {
      setError("無法連線至伺服器進行授權。");
    } finally {
      setAuthorizing(false);
    }
  }

  // Open Quick Login Modal for mobile
  async function openQuickLoginModal() {
    setShowQuickLoginModal(true);
    try {
      const res = await fetch("/api/auth/qr/quick-ticket", { method: "POST" });
      if (!res.ok) throw new Error();
      const data = (await res.json()) as { ticket: string; expires_at: number; auth_url: string };
      const finalUrl = resolveTicketUrl(data.ticket, data.auth_url, customHost, "claim_ticket");
      const qrDataUrl = await generateQrDataUrl(finalUrl);
      setQuickLoginTicket({ ...data, final_url: finalUrl, qrDataUrl });
    } catch {
      setError("無法產生快速登入 QR 碼。");
    }
  }

  if (checking) return <main className="shell"><p role="status">正在確認登入狀態…</p></main>;

  if (!account) {
    return (
      <main className="shell">
        <section className="login-card">
          {/* Header */}
          <div style={{ textAlign: "center", marginBottom: "1.5rem" }}>
            <h1 style={{ fontSize: "1.6rem", fontWeight: 700, margin: "0 0 0.35rem" }}>
              {pendingAuthTicket ? "📱 授權電腦登入" : "主揪登入"}
            </h1>
            <p style={{ color: "var(--muted)", fontSize: "0.9rem", margin: 0 }}>
              {pendingAuthTicket
                ? "有一台電腦正在等你登入。先在這支手機登入主揪帳號，下一步按確認，電腦就會自動進入後台。"
                : "輸入帳號密碼，或用已登入的手機掃右側 QR 碼授權這台電腦"}
            </p>
          </div>

          {/* Side-by-side layout: Password Form & QR Code together */}
          <div className={pendingAuthTicket ? undefined : "login-split-grid"}>
            {/* Left: Password Form */}
            <div className="login-form-side">
              <div>
                <div style={{ display: "flex", alignItems: "center", gap: "0.4rem", marginBottom: "0.75rem" }}>
                  <KeyRound size={18} color="var(--primary)" />
                  <strong style={{ fontSize: "1.05rem" }}>帳號密碼登入</strong>
                </div>
                <div style={{ display: "flex", gap: "0.25rem", background: "var(--surface)", padding: "0.25rem", borderRadius: "0.5rem", marginBottom: "1rem" }}>
                  {(["nidin", "site"] as const).map((mode) => (
                    <button
                      key={mode}
                      type="button"
                      className="btn-secondary"
                      aria-pressed={loginMode === mode}
                      style={{
                        flex: 1,
                        minHeight: "1.85rem",
                        fontSize: "0.85rem",
                        background: loginMode === mode ? "var(--panel-strong)" : "transparent",
                        borderColor: loginMode === mode ? "var(--line)" : "transparent",
                        fontWeight: loginMode === mode ? 600 : 400,
                      }}
                      onClick={() => {
                        setLoginMode(mode);
                        setError("");
                      }}
                    >
                      {mode === "nidin" ? "Nidin 帳號" : "本站帳號"}
                    </button>
                  ))}
                </div>

                <form onSubmit={(event) => void login(event)}>
                  <label className="field">
                    <span>{loginMode === "nidin" ? "Nidin 手機號碼" : "主揪帳號"}</span>
                    <input
                      autoComplete="username"
                      inputMode={loginMode === "nidin" ? "numeric" : undefined}
                      placeholder={loginMode === "nidin" ? "09xxxxxxxx" : undefined}
                      value={username}
                      onChange={(event) => setUsername(event.target.value)}
                      required
                    />
                  </label>
                  <label className="field" style={{ marginTop: "0.85rem" }}>
                    <span>{loginMode === "nidin" ? "Nidin 密碼" : "密碼"}</span>
                    <input
                      type="password"
                      autoComplete="current-password"
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                      required
                    />
                  </label>
                  {loginMode === "nidin" ? (
                    <>
                      <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", fontSize: "0.8rem", marginTop: "0.75rem" }}>
                        <input type="checkbox" checked={rememberNidin} onChange={(e) => setRememberNidin(e.target.checked)} />
                        記住 Nidin 密碼（加密），憑證失效時自動重新登入
                      </label>
                      <p style={{ color: "var(--muted)", fontSize: "0.75rem", margin: "0.5rem 0 0", lineHeight: 1.5 }}>
                        站長直接用 Nidin 帳號登入。其他主揪第一次要先用本站帳號登入，在「設定 Nidin 連線」用手機＋密碼綁定一次。
                      </p>
                    </>
                  ) : null}

                  {error ? <p role="alert" style={{ color: "var(--danger, #ef4444)", fontSize: "0.85rem", marginTop: "0.75rem" }}>{error}</p> : null}
                  {notice ? <p role="status" style={{ color: "var(--success, #10b981)", fontSize: "0.85rem", marginTop: "0.75rem" }}>{notice}</p> : null}

                  <button className="action-button" disabled={busy} type="submit" style={{ marginTop: "1.25rem", width: "100%" }}>
                    {busy ? "登入中…" : "登入"}
                  </button>
                </form>
              </div>

              <div style={{ marginTop: "1.5rem" }}>
                <a className="link-chip" href="/">← 回到點餐頁</a>
              </div>
            </div>

            {pendingAuthTicket ? null : (
              <LoginQrSection
                qrTicket={qrTicket}
                qrStatus={qrStatus}
                customHost={customHost}
                hostInput={hostInput}
                isEditingHost={isEditingHost}
                onHostInputChange={setHostInput}
                onToggleEditHost={() => setIsEditingHost(!isEditingHost)}
                onSaveHost={handleSaveCustomHost}
                onRefreshTicket={() => void fetchQrTicket()}
              />
            )}
          </div>
        </section>
      </main>
    );
  }

  if (pendingAuthTicket || approvedDevice) {
    return (
      <main className="shell">
        <section className="login-card" style={{ textAlign: "center" }}>
          {approvedDevice ? (
            <>
              <h1 style={{ fontSize: "1.6rem", fontWeight: 700, margin: "0 0 0.5rem" }}>✓ 電腦已登入</h1>
              <p style={{ color: "var(--muted)", fontSize: "0.9rem", margin: "0 0 1.5rem" }}>
                電腦端會自動進入後台。這支手機也會保持登入，下次掃碼只要按確認。
              </p>
              <button className="action-button" type="button" onClick={() => setApprovedDevice(false)}>
                在手機上進入後台
              </button>
            </>
          ) : (
            <>
              <h1 style={{ fontSize: "1.6rem", fontWeight: 700, margin: "0 0 0.5rem" }}>📱 要讓電腦登入嗎？</h1>
              <p style={{ color: "var(--muted)", fontSize: "0.9rem", margin: "0 0 1.5rem" }}>
                電腦將以 <strong>{account.username}</strong> 的身分登入。不是你本人剛剛在電腦上產生的 QR 碼，請按拒絕。
              </p>
              {error ? <p role="alert" style={{ color: "var(--danger, #ef4444)", fontSize: "0.85rem" }}>{error}</p> : null}
              <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
                <button
                  className="action-button"
                  disabled={authorizing}
                  type="button"
                  onClick={() => void authorizePendingTicket()}
                >
                  {authorizing ? "授權中…" : "確認，讓電腦登入"}
                </button>
                <button
                  className="btn-secondary"
                  type="button"
                  onClick={() => {
                    setPendingAuthTicket(null);
                    window.history.replaceState(null, "", window.location.pathname);
                  }}
                >
                  拒絕
                </button>
              </div>
            </>
          )}
        </section>
      </main>
    );
  }

  return (
    <>
      <section className="shell" aria-label="主揪帳號管理" style={{ paddingBottom: 0 }}>
        <div className="panel">
          <div className="panel-header panel-header-space" style={{ flexWrap: "wrap", gap: "0.5rem" }}>
            <span>已登入：{account.username}</span>
            <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => void openQuickLoginModal()}
              >
                <Smartphone size={14} />
                手機快速登入
              </button>
              <button className="btn-secondary" disabled={busy} onClick={() => void logout()}>
                登出
              </button>
            </div>
          </div>

          {error ? <p role="alert">{error}</p> : null}
          {notice ? <p role="status">{notice}</p> : null}

          {account.is_admin ? (
            <CreateAccountForm
              username={newUsername}
              password={newPassword}
              busy={busy}
              onUsernameChange={setNewUsername}
              onPasswordChange={setNewPassword}
              onSubmit={(event) => void createAccount(event)}
            />
          ) : null}
        </div>
      </section>

      {showQuickLoginModal ? (
        <QuickLoginModal
          username={account.username}
          ticket={quickLoginTicket}
          onRegenerate={() => void openQuickLoginModal()}
          onClose={() => setShowQuickLoginModal(false)}
        />
      ) : null}

      {children}
    </>
  );
}
