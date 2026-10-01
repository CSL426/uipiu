/**
 * Nidin 擴充功能連線與一次性配對用戶端
 */

export const PROTOCOL_VERSION = 1;

export interface ExtensionDetectionResult {
  installed: boolean;
  version?: string;
  error?: string;
}

export interface PairingInitData {
  flow_id: string;
  state: string;
  expires_at: number;
  user_summary: string;
  credential_revision: number;
}

export interface CandidateCredential {
  flowId: string;
  token: string;
  userId: string;
}

export type PairingClientStatus =
  | "extension_missing"
  | "ready"
  | "opening"
  | "waiting_login"
  | "awaiting_confirmation"
  | "saving"
  | "saved"
  | "cancelled"
  | "expired"
  | "error";

export interface ChromePort {
  postMessage: (msg: any) => void;
  disconnect: () => void;
  onMessage: {
    addListener: (cb: (msg: any) => void) => void;
  };
  onDisconnect: {
    addListener: (cb: () => void) => void;
  };
}

/**
 * extension/manifest.json 的 `key` 把 unpacked 載入的 ID 固定成這個值，
 * 任何電腦載入 extension/ 都一樣，所以不必每台機器各自設定。
 * 上架商店後商店版會有自己的 ID，屆時用 VITE_NIDIN_EXTENSION_ID 覆寫。
 */
export const PINNED_EXTENSION_ID = "pmmppocaacmfhoodfibonhfoibdheign";

export function getExtensionId(): string {
  const envId = (import.meta.env.VITE_NIDIN_EXTENSION_ID as string | undefined)?.trim();
  return envId || PINNED_EXTENSION_ID;
}

export function hasExtensionApi(): boolean {
  return (
    typeof window !== "undefined" &&
    Boolean(window.chrome && window.chrome.runtime && window.chrome.runtime.connect)
  );
}

export async function detectExtension(targetExtensionId?: string): Promise<ExtensionDetectionResult> {
  const extId = targetExtensionId || getExtensionId();
  if (!extId || !hasExtensionApi()) {
    return { installed: false, error: "未設定擴充功能 ID 或瀏覽器不支援" };
  }

  return new Promise((resolve) => {
    try {
      window.chrome.runtime.sendMessage(
        extId,
        { type: "HELLO", protocolVersion: PROTOCOL_VERSION },
        (response: any) => {
          if (window.chrome.runtime.lastError || !response) {
            resolve({
              installed: false,
              error: window.chrome.runtime.lastError?.message || "無法與擴充功能連線",
            });
            return;
          }
          if (response.type === "HELLO_ACK" && response.status === "ok") {
            resolve({
              installed: true,
              version: response.version,
            });
          } else {
            resolve({
              installed: false,
              error: "擴充功能協定版本不符",
            });
          }
        }
      );
    } catch (err) {
      resolve({
        installed: false,
        error: (err as Error).message || "連線發生錯誤",
      });
    }
  });
}

export class NidinPairingManager {
  private port: ChromePort | null = null;
  private leaseInterval: ReturnType<typeof setInterval> | null = null;
  private flowData: PairingInitData | null = null;
  private candidate: CandidateCredential | null = null;
  private broadcastChannel: BroadcastChannel | null = null;

  public status: PairingClientStatus = "ready";
  public statusMessage: string = "";
  public onStatusChange?: (status: PairingClientStatus, message: string) => void;
  public onCandidateReceived?: (candidate: CandidateCredential) => void;

  constructor(private extensionId?: string) {
    if (typeof window !== "undefined" && "BroadcastChannel" in window) {
      this.broadcastChannel = new BroadcastChannel("nidin_pairing_events");
      this.broadcastChannel.onmessage = (e) => {
        if (e.data?.type === "PAIRING_CANCELLED" && e.data?.flowId === this.flowData?.flow_id) {
          this.handleCancelled("配對流程已在其他分頁取消");
        }
      };
    }
  }

  private setStatus(status: PairingClientStatus, message: string = "") {
    this.status = status;
    this.statusMessage = message;
    this.onStatusChange?.(status, message);
  }

  public getCandidate(): CandidateCredential | null {
    return this.candidate;
  }

  public getFlowData(): PairingInitData | null {
    return this.flowData;
  }

  public async startPairing(): Promise<void> {
    const extId = this.extensionId || getExtensionId();
    if (!extId || !hasExtensionApi()) {
      this.setStatus("extension_missing", "尚未安裝或未設定擴充功能");
      return;
    }

    this.cleanup();
    this.setStatus("opening", "正在向後端建立配對金鑰…");

    try {
      // 1. 後端建立一次性配對
      const res = await fetch("/api/leader/auth/pairings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });

      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || "無法建立配對流程");
      }

      this.flowData = (await res.json()) as PairingInitData;

      // 2. 與擴充功能建立長連線
      const port: ChromePort = window.chrome.runtime.connect(extId);
      this.port = port;

      port.onDisconnect.addListener(() => {
        if (this.status !== "saved" && this.status !== "cancelled") {
          const err = window.chrome.runtime.lastError?.message;
          this.setStatus("error", err || "擴充功能連線中斷");
        }
      });

      port.onMessage.addListener((msg: any) => {
        if (!msg || typeof msg !== "object") return;

        if (msg.type === "PAIRING_STATUS") {
          if (msg.status === "tab_closed") {
            this.handleCancelled(msg.error || "官方登入分頁已被關閉");
          } else if (msg.status === "waiting_login") {
            this.setStatus("waiting_login", "請在開啟的你訂官方分頁中完成登入");
          } else if (msg.status === "error") {
            this.setStatus("error", msg.error || "擴充功能回報錯誤");
          }
        }

        if (msg.type === "CREDENTIAL_CANDIDATE") {
          // 驗證 flowId 與 state
          if (
            msg.flowId === this.flowData?.flow_id &&
            msg.state === this.flowData?.state &&
            msg.token
          ) {
            this.stopLeaseLoop();
            this.candidate = {
              flowId: msg.flowId,
              token: msg.token,
              userId: msg.userId || "",
            };
            this.setStatus("awaiting_confirmation", "已成功取得候選憑證，請確認綁定");
            this.onCandidateReceived?.(this.candidate);
          }
        }
      });

      // 3. 發送 START_PAIRING 給擴充功能
      port.postMessage({
        type: "START_PAIRING",
        protocolVersion: PROTOCOL_VERSION,
        flowId: this.flowData.flow_id,
        state: this.flowData.state,
        expiresAt: this.flowData.expires_at,
      });

      // 4. 開始定期 5 秒向後端請求 lease
      this.startLeaseLoop();
      this.setStatus("waiting_login", "請在開啟的你訂官方分頁中完成登入");
    } catch (err) {
      this.setStatus("error", (err as Error).message || "啟動配對失敗");
    }
  }

  private startLeaseLoop() {
    this.stopLeaseLoop();
    // 立即取一次
    void this.renewLease();
    this.leaseInterval = setInterval(() => {
      void this.renewLease();
    }, 5000);
  }

  private stopLeaseLoop() {
    if (this.leaseInterval !== null) {
      clearInterval(this.leaseInterval);
      this.leaseInterval = null;
    }
  }

  private async renewLease(): Promise<void> {
    if (!this.flowData || !this.port || this.status === "awaiting_confirmation") return;

    try {
      const res = await fetch(`/api/leader/auth/pairings/${this.flowData.flow_id}/lease`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state: this.flowData.state }),
      });

      if (!res.ok) {
        if (res.status === 410) {
          this.setStatus("expired", "配對已逾期，請重新發起連結");
          this.cleanup();
          return;
        }
        if (res.status === 409) {
          this.setStatus("cancelled", "配對流程已被取消");
          this.cleanup();
          return;
        }
        return;
      }

      const leaseData = (await res.json()) as { status: string; lease_expires_at: number };
      if (this.port) {
        this.port.postMessage({
          type: "PAIRING_LEASE",
          protocolVersion: PROTOCOL_VERSION,
          flowId: this.flowData.flow_id,
          state: this.flowData.state,
          leaseExpiresAt: leaseData.lease_expires_at,
        });
      }
    } catch {}
  }

  public async confirmBinding(): Promise<{ success: boolean; error?: string }> {
    if (!this.flowData || !this.candidate) {
      return { success: false, error: "缺少配對資訊或尚未取得憑證" };
    }

    this.setStatus("saving", "正在更新後台憑證…");

    try {
      const res = await fetch(`/api/leader/auth/pairings/${this.flowData.flow_id}/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          state: this.flowData.state,
          token: this.candidate.token,
          user_id: this.candidate.userId,
        }),
      });

      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        const detail = errJson.detail || "確認配對失敗";
        this.setStatus("error", detail);
        return { success: false, error: detail };
      }

      // 通知擴充功能結束
      if (this.port) {
        this.port.postMessage({
          type: "FINISH_PAIRING",
          flowId: this.flowData.flow_id,
          state: this.flowData.state,
        });
      }

      this.setStatus("saved", "已成功儲存官方憑證！");
      return { success: true };
    } catch (err) {
      const msg = (err as Error).message || "確認配對異常";
      this.setStatus("error", msg);
      return { success: false, error: msg };
    }
  }

  public async closeOfficialTab(): Promise<void> {
    if (this.port && this.flowData) {
      this.port.postMessage({
        type: "CLOSE_NIDIN_TAB",
        flowId: this.flowData.flow_id,
        state: this.flowData.state,
      });
    }
  }

  public handleCancelled(reason: string = "已取消配對") {
    this.setStatus("cancelled", reason);
    this.cleanup();
  }

  public async cancelPairing(): Promise<void> {
    if (this.flowData) {
      const flowId = this.flowData.flow_id;
      const state = this.flowData.state;

      if (this.port) {
        try {
          this.port.postMessage({
            type: "CANCEL_PAIRING",
            flowId,
            state,
          });
        } catch {}
      }

      this.broadcastChannel?.postMessage({
        type: "PAIRING_CANCELLED",
        flowId,
      });

      try {
        await fetch(`/api/leader/auth/pairings/${flowId}/cancel`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ state }),
        });
      } catch {}
    }

    this.handleCancelled("使用者已取消配對");
  }

  public cleanup() {
    this.stopLeaseLoop();
    if (this.port) {
      try {
        this.port.disconnect();
      } catch {}
      this.port = null;
    }
    this.flowData = null;
    this.candidate = null;
  }

  public destroy() {
    this.cleanup();
    if (this.broadcastChannel) {
      this.broadcastChannel.close();
      this.broadcastChannel = null;
    }
  }
}
