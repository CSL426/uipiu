import { AnimatePresence, motion } from "framer-motion";
import {
  AlertCircle,
  Bookmark,
  Camera,
  Check,
  CheckCircle2,
  Copy,
  ExternalLink,
  KeyRound,
  QrCode,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { copyText } from "../lib/clipboard";
import {
  CandidateCredential,
  detectExtension,
  getExtensionId,
  NidinPairingManager,
  PairingClientStatus,
} from "../lib/nidin-extension";
import {
  generateQrDataUrl,
  getNidinBookmarkletCode,
  parseQrCredentials,
  parseQrFromImageFile,
} from "../lib/qr";
import type { LeaderAuthStatus } from "../types";
import { NidinPasswordLogin } from "./NidinPasswordLogin";

interface LeaderAuthModalProps {
  showAuthModal: boolean;
  leaderAuth: LeaderAuthStatus | null;
  setLeaderAuth: (auth: LeaderAuthStatus) => void;
  setShowAuthModal: (open: boolean) => void;
  setStatusMessage: (message: string) => void;
}

function maskToken(token: string): string {
  if (token.length > 10) {
    return `${token.slice(0, 6)}...${token.slice(-4)}`;
  }
  return "***";
}

export function LeaderAuthModal({
  showAuthModal,
  leaderAuth,
  setLeaderAuth,
  setShowAuthModal,
  setStatusMessage,
}: LeaderAuthModalProps) {
  // 手動輸入狀態
  const [tokenInput, setTokenInput] = useState("");
  const [userIdInput, setUserIdInput] = useState("");

  // password 不必安裝任何東西，放第一個；auto 是擴充功能，給只用 LINE／Google 登入 Nidin 的人
  const [authModalTab, setAuthModalTab] = useState<"password" | "auto" | "manual" | "qr" | "bookmarklet">("password");

  // 自動連結配對狀態
  const [pairingStatus, setPairingStatus] = useState<PairingClientStatus>("ready");
  const [pairingMsg, setPairingMsg] = useState("");
  const [extensionVersion, setExtensionVersion] = useState<string | null>(null);
  const [customExtensionId, setCustomExtensionId] = useState("");
  const [candidate, setCandidate] = useState<CandidateCredential | null>(null);
  const [isDetecting, setIsDetecting] = useState(false);

  const pairingManagerRef = useRef<NidinPairingManager | null>(null);

  // QR / Bookmarklet 狀態
  const [qrScanFeedback, setQrScanFeedback] = useState("");
  const [qrScanError, setQrScanError] = useState("");
  const [isProcessingQr, setIsProcessingQr] = useState(false);
  const [shareQrDataUrl, setShareQrDataUrl] = useState<string | null>(null);
  const [copiedBookmarklet, setCopiedBookmarklet] = useState(false);
  const qrFileInputRef = useRef<HTMLInputElement | null>(null);

  // 初始化擴充功能偵測
  async function checkExtension(targetId?: string) {
    setIsDetecting(true);
    const idToTest = targetId || customExtensionId || getExtensionId();
    const result = await detectExtension(idToTest);
    setIsDetecting(false);

    if (result.installed) {
      setExtensionVersion(result.version || "0.1.0");
      setPairingStatus("ready");
      setPairingMsg("");
    } else {
      setExtensionVersion(null);
      setPairingStatus("extension_missing");
      setPairingMsg(result.error || "未偵測到已安裝的擴充功能");
    }
  }

  useEffect(() => {
    if (showAuthModal && authModalTab === "auto") {
      void checkExtension();
    }
  }, [showAuthModal, authModalTab]);

  // 配對管理器初始化
  useEffect(() => {
    const manager = new NidinPairingManager(customExtensionId || getExtensionId());
    pairingManagerRef.current = manager;

    manager.onStatusChange = (status, msg) => {
      setPairingStatus(status);
      setPairingMsg(msg);
    };

    manager.onCandidateReceived = (cand) => {
      setCandidate(cand);
    };

    return () => {
      manager.destroy();
      pairingManagerRef.current = null;
    };
  }, [customExtensionId]);

  // 關閉視窗時清理未完成配對
  function handleCloseModal() {
    if (pairingStatus === "opening" || pairingStatus === "waiting_login" || pairingStatus === "awaiting_confirmation") {
      void pairingManagerRef.current?.cancelPairing();
    }
    setCandidate(null);
    setShowAuthModal(false);
  }

  // 啟動自動配對
  async function handleStartAutoPairing() {
    setCandidate(null);
    await pairingManagerRef.current?.startPairing();
  }

  // 確認綁定
  async function handleConfirmBinding() {
    if (!pairingManagerRef.current) return;
    const res = await pairingManagerRef.current.confirmBinding();
    if (res.success) {
      // 重新讀取目前憑證狀態
      try {
        const authRes = await fetch("/api/leader/auth");
        if (authRes.ok) {
          const authData = (await authRes.json()) as LeaderAuthStatus;
          setLeaderAuth(authData);
        }
      } catch {}
      setStatusMessage("官方憑證已自動連結並更新成功！");
    }
  }

  // 取消配對
  async function handleCancelPairing() {
    await pairingManagerRef.current?.cancelPairing();
    setCandidate(null);
  }

  // 關閉官方分頁
  async function handleCloseOfficialTab() {
    await pairingManagerRef.current?.closeOfficialTab();
    handleCloseModal();
  }

  // 手動輸入與 QR 處理
  async function handleQrImageUpload(file: File) {
    setIsProcessingQr(true);
    setQrScanError("");
    setQrScanFeedback("");
    try {
      const qrText = await parseQrFromImageFile(file);
      if (!qrText) {
        setQrScanError("未能在圖片中辨識出 QR 碼，請確保圖片清晰無反光。");
        return;
      }
      const creds = parseQrCredentials(qrText);
      if (!creds) {
        setQrScanError("辨識到的 QR 碼內容並非有效的你訂憑證格式。");
        return;
      }
      setTokenInput(creds.token);
      if (creds.userId) setUserIdInput(creds.userId);
      setQrScanFeedback("✓ 成功從 QR 碼讀取憑證！已為您自動填入。");
      setAuthModalTab("manual");
    } catch {
      setQrScanError("解析圖片 QR 碼時發生異常，請重試或使用手動填寫。");
    } finally {
      setIsProcessingQr(false);
    }
  }

  async function handleGenerateCredentialQr() {
    const token = tokenInput.trim();
    if (!token) return;
    try {
      const payload = JSON.stringify({ token, userId: userIdInput.trim() });
      const url = await generateQrDataUrl(payload);
      setShareQrDataUrl(url);
    } catch {
      setQrScanError("產生憑證 QR 碼失敗。");
    }
  }

  async function saveLeaderAuth() {
    if (!tokenInput.trim()) return;
    try {
      const res = await fetch("/api/leader/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: tokenInput, user_id: userIdInput }),
      });
      if (res.ok) {
        const data = (await res.json()) as LeaderAuthStatus;
        setLeaderAuth(data);
        setShowAuthModal(false);
        setTokenInput("");
        setUserIdInput("");
        setStatusMessage("團長憑證已更新成功！");
      }
    } catch {
      setStatusMessage("儲存團長憑證失敗。");
    }
  }

  async function refreshLeaderAuth() {
    try {
      const res = await fetch("/api/leader/auth");
      if (res.ok) setLeaderAuth((await res.json()) as LeaderAuthStatus);
    } catch {
      // 只是更新顯示用的到期日，失敗就維持原樣
    }
  }

  async function clearLeaderAuth() {
    try {
      const res = await fetch("/api/leader/auth", { method: "DELETE" });
      if (res.ok) {
        setLeaderAuth({ is_authenticated: false, user_id: "", masked_token: "" });
        setStatusMessage("已清除團長憑證。");
      }
    } catch {}
  }

  return (
    <AnimatePresence>
      {showAuthModal ? (
        <motion.div
          className="modal-overlay"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={handleCloseModal}
        >
          <motion.div
            className="modal-content"
            initial={{ scale: 0.95, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.95, opacity: 0 }}
            onClick={(e) => e.stopPropagation()}
            style={{ maxWidth: "38rem" }}
          >
            <div className="panel-header" style={{ marginBottom: "0.75rem", flexWrap: "wrap", gap: "0.5rem" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                <KeyRound size={20} />
                <h3>你訂 (Nidin) 官方憑證設定</h3>
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "0.25rem", background: "var(--surface)", padding: "0.25rem", borderRadius: "0.5rem" }}>
                <button
                  type="button"
                  className="btn-secondary"
                  style={{
                    minHeight: "1.85rem",
                    padding: "0.25rem 0.65rem",
                    fontSize: "0.8rem",
                    background: authModalTab === "password" ? "var(--panel-strong)" : "transparent",
                    borderColor: authModalTab === "password" ? "var(--line)" : "transparent",
                    fontWeight: authModalTab === "password" ? 600 : 400,
                  }}
                  onClick={() => setAuthModalTab("password")}
                >
                  手機＋密碼
                </button>
                <button
                  type="button"
                  className="btn-secondary"
                  style={{
                    minHeight: "1.85rem",
                    padding: "0.25rem 0.65rem",
                    fontSize: "0.8rem",
                    display: "flex",
                    alignItems: "center",
                    gap: "0.25rem",
                    background: authModalTab === "auto" ? "var(--panel-strong)" : "transparent",
                    borderColor: authModalTab === "auto" ? "var(--line)" : "transparent",
                    fontWeight: authModalTab === "auto" ? 600 : 400,
                  }}
                  onClick={() => setAuthModalTab("auto")}
                >
                  <Sparkles size={13} style={{ color: "var(--accent, #f59e0b)" }} />
                  LINE／Google
                </button>
                <button
                  type="button"
                  className="btn-secondary"
                  style={{
                    minHeight: "1.85rem",
                    padding: "0.25rem 0.65rem",
                    fontSize: "0.8rem",
                    background: authModalTab === "manual" ? "var(--panel-strong)" : "transparent",
                    borderColor: authModalTab === "manual" ? "var(--line)" : "transparent",
                    fontWeight: authModalTab === "manual" ? 600 : 400,
                  }}
                  onClick={() => setAuthModalTab("manual")}
                >
                  手動輸入
                </button>
                <button
                  type="button"
                  className="btn-secondary"
                  style={{
                    minHeight: "1.85rem",
                    padding: "0.25rem 0.65rem",
                    fontSize: "0.8rem",
                    display: "flex",
                    alignItems: "center",
                    gap: "0.25rem",
                    background: authModalTab === "qr" ? "var(--panel-strong)" : "transparent",
                    borderColor: authModalTab === "qr" ? "var(--line)" : "transparent",
                    fontWeight: authModalTab === "qr" ? 600 : 400,
                  }}
                  onClick={() => setAuthModalTab("qr")}
                >
                  <Camera size={13} />
                  掃碼/匯入
                </button>
                <button
                  type="button"
                  className="btn-secondary"
                  style={{
                    minHeight: "1.85rem",
                    padding: "0.25rem 0.65rem",
                    fontSize: "0.8rem",
                    display: "flex",
                    alignItems: "center",
                    gap: "0.25rem",
                    background: authModalTab === "bookmarklet" ? "var(--panel-strong)" : "transparent",
                    borderColor: authModalTab === "bookmarklet" ? "var(--line)" : "transparent",
                    fontWeight: authModalTab === "bookmarklet" ? 600 : 400,
                  }}
                  onClick={() => setAuthModalTab("bookmarklet")}
                >
                  <Bookmark size={13} />
                  書籤提取
                </button>
              </div>
            </div>

            {authModalTab === "password" ? (
              <NidinPasswordLogin
                leaderAuth={leaderAuth}
                onBound={(auth) => {
                  setLeaderAuth(auth);
                  setShowAuthModal(false);
                  setStatusMessage(
                    [
                      "已登入 Nidin 並綁定憑證。",
                      auth.auto_relogin ? "憑證失效時會自動重新登入。" : "",
                      auth.nidin_login_linked ? "之後可以直接用 Nidin 帳號登入本站。" : "",
                    ].join(""),
                  );
                }}
                onClear={() => void clearLeaderAuth()}
                onCancel={handleCloseModal}
                onStatusChanged={() => void refreshLeaderAuth()}
              />
            ) : authModalTab === "auto" ? (
              /* TAB: LINE／Google（擴充功能） */
              <div>
                {/* 目前憑證狀態摘要 */}
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "0.6rem 0.85rem",
                    background: "var(--surface)",
                    borderRadius: "0.5rem",
                    border: "1px solid var(--line)",
                    marginBottom: "1rem",
                    fontSize: "0.85rem",
                  }}
                >
                  <div>
                    <span style={{ color: "var(--muted)" }}>目前綁定狀態：</span>
                    {leaderAuth?.is_authenticated ? (
                      <strong style={{ color: "var(--success, #10b981)", marginLeft: "0.25rem" }}>
                        已綁定 (Token: {leaderAuth.masked_token})
                      </strong>
                    ) : (
                      <span style={{ color: "var(--muted)", marginLeft: "0.25rem" }}>尚未綁定</span>
                    )}
                  </div>
                  {leaderAuth?.is_authenticated ? (
                    <button
                      className="btn-secondary"
                      style={{ fontSize: "0.75rem", padding: "0.2rem 0.5rem", color: "var(--danger, #ef4444)" }}
                      onClick={() => void clearLeaderAuth()}
                    >
                      解除綁定
                    </button>
                  ) : null}
                </div>

                {/* 狀態 1: 尚未偵測到擴充功能 */}
                {pairingStatus === "extension_missing" ? (
                  <div
                    style={{
                      background: "var(--surface)",
                      border: "1px dashed var(--line)",
                      borderRadius: "0.75rem",
                      padding: "1.25rem",
                      marginBottom: "1rem",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "flex-start", gap: "0.6rem", marginBottom: "0.75rem" }}>
                      <AlertCircle size={20} style={{ color: "var(--accent, #f59e0b)", flexShrink: 0, marginTop: "0.15rem" }} />
                      <div>
                        <strong style={{ display: "block", fontSize: "0.9rem", color: "var(--text)" }}>
                          需要安裝「Nidin 團購點餐站 憑證連結（非官方）」擴充功能
                        </strong>
                        <p style={{ margin: "0.25rem 0 0", fontSize: "0.8rem", color: "var(--muted)", lineHeight: 1.5 }}>
                          首版採用 Chrome / Edge 專用的 Manifest V3 擴充功能，可在官方頁面安全讀取登入工作階段，免去手動複製貼上。
                        </p>
                      </div>
                    </div>

                    <div style={{ background: "var(--panel-strong)", padding: "0.75rem", borderRadius: "0.5rem", marginBottom: "0.85rem" }}>
                      <strong style={{ fontSize: "0.8rem", color: "var(--text)", display: "block", marginBottom: "0.35rem" }}>
                        本機安裝步驟：
                      </strong>
                      <ol style={{ margin: 0, paddingLeft: "1.2rem", fontSize: "0.78rem", color: "var(--muted)", lineHeight: 1.6 }}>
                        <li>打開瀏覽器擴充功能頁面（<code>chrome://extensions</code> 或 <code>edge://extensions</code>）。</li>
                        <li>開啟右上角「開發人員模式」，點擊「載入未封裝項目」。</li>
                        <li>選擇本專案目錄中的 <code>extension/</code> 資料夾。</li>
                        <li>載入後，系統將自動辨識擴充功能 ID。</li>
                      </ol>
                    </div>

                    <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginBottom: "0.5rem" }}>
                      <input
                        placeholder="選填：指定 Extension ID"
                        value={customExtensionId}
                        onChange={(e) => setCustomExtensionId(e.target.value.trim())}
                        style={{ fontSize: "0.8rem", padding: "0.35rem 0.6rem", flex: 1 }}
                      />
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={isDetecting}
                        onClick={() => void checkExtension(customExtensionId)}
                        style={{ fontSize: "0.8rem", display: "flex", alignItems: "center", gap: "0.35rem" }}
                      >
                        <RefreshCw size={13} className={isDetecting ? "spin" : ""} />
                        重新偵測
                      </button>
                    </div>
                  </div>
                ) : null}

                {/* 狀態 2: 擴充功能準備就緒 (ready) */}
                {pairingStatus === "ready" ? (
                  <div style={{ textAlign: "center", padding: "1rem 0" }}>
                    <p style={{ fontSize: "0.875rem", color: "var(--muted)", marginBottom: "1.25rem", lineHeight: 1.6 }}>
                      點擊下方按鈕，系統將自動開啟你訂官方專用登入分頁。
                      <br />
                      若尚未登入，請依官方流程使用 LINE 或 Google 完成驗證；完成後憑證將自動帶回本站。
                    </p>
                    <button
                      type="button"
                      className="action-button"
                      style={{
                        padding: "0.75rem 1.5rem",
                        fontSize: "0.95rem",
                        display: "inline-flex",
                        alignItems: "center",
                        gap: "0.5rem",
                        fontWeight: 600,
                      }}
                      onClick={() => void handleStartAutoPairing()}
                    >
                      <ExternalLink size={18} />
                      連結 Nidin 帳號 (自動配對)
                    </button>
                  </div>
                ) : null}

                {/* 狀態 3: 等待官方登入與採集 (opening / waiting_login) */}
                {pairingStatus === "opening" || pairingStatus === "waiting_login" ? (
                  <div style={{ textAlign: "center", padding: "1.5rem 0" }}>
                    <RefreshCw size={32} className="spin" style={{ margin: "0 auto 1rem", color: "var(--primary)" }} />
                    <strong style={{ display: "block", fontSize: "1rem", marginBottom: "0.5rem" }}>
                      請在開啟的你訂官方分頁完成登入
                    </strong>
                    <p style={{ fontSize: "0.85rem", color: "var(--muted)", maxWidth: "24rem", margin: "0 auto 1.5rem", lineHeight: 1.5 }}>
                      已為您建立專屬配對分頁。請在該分頁使用 LINE 或 Google 完成驗證。
                      驗證成功後，憑證將自動回傳本站，請勿手動關閉此對話視窗。
                    </p>
                    <button
                      type="button"
                      className="btn-secondary"
                      style={{ fontSize: "0.85rem" }}
                      onClick={() => void handleCancelPairing()}
                    >
                      取消本次配對
                    </button>
                  </div>
                ) : null}

                {/* 狀態 4: 已取得候選憑證，等待確認 (awaiting_confirmation) */}
                {pairingStatus === "awaiting_confirmation" && candidate ? (
                  <div
                    style={{
                      background: "var(--surface)",
                      border: "1px solid var(--line)",
                      borderRadius: "0.75rem",
                      padding: "1.25rem",
                      marginBottom: "1rem",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "1rem" }}>
                      <CheckCircle2 size={20} style={{ color: "var(--success, #10b981)" }} />
                      <strong style={{ fontSize: "0.95rem" }}>已成功讀取官方憑證，請確認綁定</strong>
                    </div>

                    <div style={{ display: "grid", gap: "0.6rem", fontSize: "0.85rem", marginBottom: "1rem" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", borderBottom: "1px solid var(--line)", paddingBottom: "0.4rem" }}>
                        <span style={{ color: "var(--muted)" }}>接收本站帳號：</span>
                        <strong>{pairingManagerRef.current?.getFlowData()?.user_summary || "目前登入帳號"}</strong>
                      </div>
                      <div style={{ display: "flex", justifyContent: "space-between", borderBottom: "1px solid var(--line)", paddingBottom: "0.4rem" }}>
                        <span style={{ color: "var(--muted)" }}>官方 User ID：</span>
                        <span>{candidate.userId ? candidate.userId : "（官方未提供）"}</span>
                      </div>
                      <div style={{ display: "flex", justifyContent: "space-between", borderBottom: "1px solid var(--line)", paddingBottom: "0.4rem" }}>
                        <span style={{ color: "var(--muted)" }}>官方 Token 遮罩：</span>
                        <code style={{ background: "var(--panel-strong)", padding: "0.15rem 0.4rem", borderRadius: "0.25rem" }}>
                          {maskToken(candidate.token)}
                        </code>
                      </div>
                    </div>

                    {leaderAuth?.is_authenticated ? (
                      <p style={{ margin: "0 0 1rem", fontSize: "0.8rem", color: "var(--accent, #f59e0b)", lineHeight: 1.4 }}>
                        ⚠️ 確認後將會取代既有的團長憑證設定。
                      </p>
                    ) : null}

                    <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.5rem" }}>
                      <button
                        type="button"
                        className="btn-secondary"
                        onClick={() => void handleCancelPairing()}
                      >
                        放棄並取消
                      </button>
                      <button
                        type="button"
                        className="action-button"
                        style={{ marginTop: 0 }}
                        onClick={() => void handleConfirmBinding()}
                      >
                        確認綁定憑證
                      </button>
                    </div>
                  </div>
                ) : null}

                {/* 狀態 5: 儲存中 (saving) */}
                {pairingStatus === "saving" ? (
                  <div style={{ textAlign: "center", padding: "1.5rem 0" }}>
                    <RefreshCw size={28} className="spin" style={{ margin: "0 auto 0.75rem", color: "var(--primary)" }} />
                    <p style={{ margin: 0, fontWeight: 500 }}>正在驗證並更新官方憑證…</p>
                  </div>
                ) : null}

                {/* 狀態 6: 完成 (saved) */}
                {pairingStatus === "saved" ? (
                  <div style={{ textAlign: "center", padding: "1.25rem 0" }}>
                    <CheckCircle2 size={36} style={{ margin: "0 auto 0.75rem", color: "var(--success, #10b981)" }} />
                    <strong style={{ display: "block", fontSize: "1rem", marginBottom: "0.35rem" }}>
                      已成功儲存 Nidin 官方憑證！
                    </strong>
                    <p style={{ fontSize: "0.85rem", color: "var(--muted)", marginBottom: "1.25rem" }}>
                      您現在可以直接同步跟團同事的品項明細與進行排版對帳。
                    </p>
                    <div style={{ display: "flex", justifyContent: "center", gap: "0.75rem" }}>
                      <button
                        type="button"
                        className="btn-secondary"
                        onClick={() => void handleCloseOfficialTab()}
                      >
                        關閉官方登入分頁
                      </button>
                      <button
                        type="button"
                        className="action-button"
                        style={{ marginTop: 0 }}
                        onClick={handleCloseModal}
                      >
                        完成
                      </button>
                    </div>
                  </div>
                ) : null}

                {/* 狀態 7: 錯誤 / 取消 / 逾期 */}
                {pairingStatus === "error" || pairingStatus === "cancelled" || pairingStatus === "expired" ? (
                  <div
                    style={{
                      background: "var(--surface)",
                      border: "1px solid var(--line)",
                      borderRadius: "0.75rem",
                      padding: "1rem",
                      marginBottom: "1rem",
                      textAlign: "center",
                    }}
                  >
                    <p style={{ color: "var(--danger, #ef4444)", fontSize: "0.875rem", margin: "0 0 0.85rem" }}>
                      {pairingMsg || "配對未完成或已逾期"}
                    </p>
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={() => {
                        setPairingStatus("ready");
                        setPairingMsg("");
                      }}
                    >
                      重新發起配對
                    </button>
                  </div>
                ) : null}

                <div style={{ marginTop: "1.25rem", borderTop: "1px solid var(--line)", paddingTop: "0.75rem", display: "flex", justifyContent: "flex-end" }}>
                  <button className="btn-secondary" onClick={handleCloseModal}>
                    關閉
                  </button>
                </div>
              </div>
            ) : authModalTab === "manual" ? (
              /* TAB: 手動輸入 */
              <div>
                <p style={{ fontSize: "0.875rem", color: "var(--muted)", marginBottom: "1rem", lineHeight: 1.5 }}>
                  可在已登入你訂的瀏覽器中按 F12 開發者工具 Application &gt; LocalStorage 複製 <code>Nidin_token</code> 與 <code>Nidin_userId</code>。
                </p>
                {qrScanFeedback ? (
                  <p style={{ fontSize: "0.85rem", color: "var(--success, #10b981)", fontWeight: 500, marginBottom: "0.75rem" }}>
                    {qrScanFeedback}
                  </p>
                ) : null}
                <label className="field" style={{ marginBottom: "0.75rem" }}>
                  <span>MC-API-Token (必填)</span>
                  <input
                    placeholder="例如：eyJhbGciOiJIUzI1NiIsIn..."
                    value={tokenInput}
                    onChange={(e) => setTokenInput(e.target.value)}
                  />
                </label>
                <label className="field" style={{ marginBottom: "1rem" }}>
                  <span>MC-API-User (選填)</span>
                  <input
                    placeholder="例如：12345678"
                    value={userIdInput}
                    onChange={(e) => setUserIdInput(e.target.value)}
                  />
                </label>

                {tokenInput.trim() ? (
                  <div style={{ marginBottom: "1rem" }}>
                    {shareQrDataUrl ? (
                      <div style={{ textAlign: "center", padding: "0.75rem", background: "var(--surface)", borderRadius: "0.5rem" }}>
                        <img
                          src={shareQrDataUrl}
                          alt="憑證分享 QR 碼"
                          style={{ width: "10rem", height: "10rem", display: "inline-block", background: "#fff", padding: "0.5rem", borderRadius: "0.5rem" }}
                        />
                        <p style={{ fontSize: "0.8rem", color: "var(--muted)", marginTop: "0.5rem", marginBottom: 0 }}>
                          其他裝置可掃描此 QR 碼快速匯入此憑證
                        </p>
                      </div>
                    ) : (
                      <button
                        type="button"
                        className="btn-secondary"
                        style={{ fontSize: "0.8rem", display: "flex", alignItems: "center", gap: "0.35rem" }}
                        onClick={() => void handleGenerateCredentialQr()}
                      >
                        <QrCode size={14} />
                        產生此憑證的分享 QR 碼
                      </button>
                    )}
                  </div>
                ) : null}

                <div style={{ display: "flex", justifyContent: "space-between", gap: "0.5rem", alignItems: "center" }}>
                  <div>
                    {leaderAuth?.is_authenticated ? (
                      <button
                        className="btn-secondary"
                        style={{ color: "var(--danger, #ef4444)" }}
                        onClick={() => void clearLeaderAuth()}
                      >
                        解除綁定
                      </button>
                    ) : null}
                  </div>
                  <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                    <button className="btn-secondary" onClick={handleCloseModal}>
                      取消
                    </button>
                    <button className="action-button" style={{ marginTop: 0 }} onClick={() => void saveLeaderAuth()}>
                      儲存憑證
                    </button>
                  </div>
                </div>
              </div>
            ) : authModalTab === "qr" ? (
              /* TAB: QR 掃碼/匯入 */
              <div style={{ textAlign: "center", padding: "0.5rem 0" }}>
                <input
                  type="file"
                  ref={qrFileInputRef}
                  accept="image/*"
                  style={{ display: "none" }}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) void handleQrImageUpload(file);
                  }}
                />
                <div
                  style={{
                    border: "2px dashed var(--line)",
                    borderRadius: "0.75rem",
                    padding: "2rem 1rem",
                    cursor: "pointer",
                    background: "var(--surface)",
                    transition: "background 0.2s",
                  }}
                  onClick={() => qrFileInputRef.current?.click()}
                >
                  {isProcessingQr ? (
                    <div>
                      <RefreshCw size={28} className="spin" style={{ margin: "0 auto 0.75rem" }} />
                      <p style={{ margin: 0, fontWeight: 500 }}>正在解析圖片中的 QR 碼…</p>
                    </div>
                  ) : (
                    <div>
                      <Camera size={32} style={{ margin: "0 auto 0.75rem", color: "var(--primary)" }} />
                      <p style={{ margin: "0 0 0.5rem", fontWeight: 600 }}>點擊拍照或上傳 QR 碼截圖</p>
                      <p style={{ margin: 0, fontSize: "0.85rem", color: "var(--muted)" }}>
                        支援手機相機拍照、電腦截圖上傳，自動識別並填入憑證
                      </p>
                    </div>
                  )}
                </div>
                {qrScanError ? (
                  <p role="alert" style={{ color: "var(--danger, #ef4444)", fontSize: "0.85rem", marginTop: "0.75rem" }}>
                    {qrScanError}
                  </p>
                ) : null}
                <div style={{ marginTop: "1rem", display: "flex", justifyContent: "flex-end" }}>
                  <button className="btn-secondary" onClick={() => setAuthModalTab("manual")}>
                    返回手動輸入
                  </button>
                </div>
              </div>
            ) : (
              /* TAB: 書籤提取 */
              <div>
                <p style={{ fontSize: "0.875rem", color: "var(--muted)", marginBottom: "0.75rem", lineHeight: 1.5 }}>
                  使用此書籤小工具，在你訂官方網站上一鍵即可彈出憑證 QR 碼。
                </p>
                <ol style={{ fontSize: "0.85rem", color: "var(--text)", paddingLeft: "1.25rem", margin: "0 0 1rem", lineHeight: 1.6 }}>
                  <li>點擊下方按鈕複製書籤工具代碼。</li>
                  <li>在瀏覽器書籤列新增書籤，將網址（URL）貼上剛複製的代碼。</li>
                  <li>在 <strong>order.nidin.shop</strong> 透過 LINE 或 Google 帳號登入後，點擊該書籤。</li>
                  <li>網頁將自動彈出憑證 QR 碼，使用本系統掃碼或手機相機一掃即可完成！</li>
                </ol>
                <button
                  type="button"
                  className="action-button"
                  style={{ width: "100%", display: "flex", alignItems: "center", justifyContent: "center", gap: "0.5rem" }}
                  onClick={() => {
                    void copyText(getNidinBookmarkletCode()).then((ok) => {
                      if (!ok) return;
                      setCopiedBookmarklet(true);
                      setTimeout(() => setCopiedBookmarklet(false), 2500);
                    });
                  }}
                >
                  {copiedBookmarklet ? <Check size={16} /> : <Copy size={16} />}
                  {copiedBookmarklet ? "已複製書籤代碼至剪貼簿！" : "複製你訂一鍵提取書籤代碼"}
                </button>
                <div style={{ marginTop: "1rem", display: "flex", justifyContent: "flex-end" }}>
                  <button className="btn-secondary" onClick={() => setAuthModalTab("manual")}>
                    返回手動輸入
                  </button>
                </div>
              </div>
            )}
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
