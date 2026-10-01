import {
  EXTENSION_VERSION,
  PROTOCOL_VERSION,
  type ContentCandidatePayload,
  type ContentToCheckMessage,
  type PairingSessionData,
  type WebToExtensionMessage,
  type WorkerCheckResponse,
} from "./types";

export const ALLOWED_WEB_ORIGINS = [
  "http://localhost:6464",
  "http://127.0.0.1:6464",
  "http://localhost:5173",
  "http://127.0.0.1:5173",
  "http://localhost:8000",
  "http://127.0.0.1:8000",
];

const activePorts = new Map<string, chrome.runtime.Port>();

export function isOriginAllowed(origin: string): boolean {
  try {
    // 比對完整 origin（scheme + host + port）。manifest 的 externally_connectable
    // 無法限制 port，所以這層是唯一能擋掉本機其他服務的地方。
    return ALLOWED_WEB_ORIGINS.includes(new URL(origin).origin);
  } catch {
    return false;
  }
}

export function isSenderAllowed(sender: chrome.runtime.MessageSender): boolean {
  if (!sender.origin || !isOriginAllowed(sender.origin)) {
    return false;
  }
  if (sender.frameId !== 0 && sender.frameId !== undefined) {
    return false;
  }
  if (sender.url) {
    try {
      const url = new URL(sender.url);
      if (!url.pathname.includes("/admin")) {
        return false;
      }
    } catch {
      return false;
    }
  }
  return true;
}

export async function getPairingSession(flowId: string): Promise<PairingSessionData | null> {
  const key = `pairing_${flowId}`;
  const res = await chrome.storage.session.get(key);
  return (res[key] as PairingSessionData) || null;
}

export async function savePairingSession(data: PairingSessionData): Promise<void> {
  const key = `pairing_${data.flowId}`;
  await chrome.storage.session.set({ [key]: data });
  if (data.nidinTabId) {
    await chrome.storage.session.set({ [`tab_flow_${data.nidinTabId}`]: data.flowId });
  }
}

export async function removePairingSession(flowId: string): Promise<void> {
  const session = await getPairingSession(flowId);
  const key = `pairing_${flowId}`;
  await chrome.storage.session.remove(key);
  if (session?.nidinTabId) {
    await chrome.storage.session.remove(`tab_flow_${session.nidinTabId}`);
  }
  activePorts.delete(flowId);
}

export async function getFlowIdForTab(tabId: number): Promise<string | null> {
  const key = `tab_flow_${tabId}`;
  const res = await chrome.storage.session.get(key);
  return (res[key] as string) || null;
}

export function registerListeners() {
  if (typeof chrome === "undefined" || !chrome.runtime) return;

  // 1. 外部網頁長連線（本站 Web Port）
  chrome.runtime.onConnectExternal.addListener((port) => {
    if (!isSenderAllowed(port.sender || {})) {
      port.disconnect();
      return;
    }

    let connectedFlowId: string | null = null;

    port.onMessage.addListener(async (rawMsg: WebToExtensionMessage) => {
      if (!rawMsg || typeof rawMsg !== "object") return;

      if (rawMsg.type === "HELLO") {
        port.postMessage({
          type: "HELLO_ACK",
          protocolVersion: PROTOCOL_VERSION,
          version: EXTENSION_VERSION,
          status: "ok",
        });
        return;
      }

      if (rawMsg.type === "START_PAIRING") {
        if (!rawMsg.flowId || !rawMsg.state || !rawMsg.expiresAt) {
          port.postMessage({
            type: "PAIRING_STATUS",
            flowId: rawMsg.flowId || "",
            status: "error",
            error: "無效的配對參數",
          });
          return;
        }

        connectedFlowId = rawMsg.flowId;
        activePorts.set(connectedFlowId, port);

        // 建立專用官方分頁
        const tab = await chrome.tabs.create({
          url: "https://order.nidin.shop/",
          active: true,
        });

        const sessionData: PairingSessionData = {
          flowId: rawMsg.flowId,
          state: rawMsg.state,
          expiresAt: rawMsg.expiresAt,
          webOrigin: port.sender?.origin || "",
          webTabId: port.sender?.tab?.id,
          nidinTabId: tab.id,
          status: "waiting_login",
          leaseExpiresAt: 0,
          candidateReceived: false,
        };

        await savePairingSession(sessionData);

        port.postMessage({
          type: "PAIRING_STATUS",
          flowId: rawMsg.flowId,
          status: "waiting_login",
          nidinTabId: tab.id,
        });
        return;
      }

      if (rawMsg.type === "PAIRING_LEASE") {
        const session = await getPairingSession(rawMsg.flowId);
        if (!session || session.state !== rawMsg.state) return;
        session.leaseExpiresAt = rawMsg.leaseExpiresAt;
        await savePairingSession(session);
        return;
      }

      if (rawMsg.type === "FINISH_PAIRING" || rawMsg.type === "CANCEL_PAIRING") {
        if (rawMsg.flowId) {
          await removePairingSession(rawMsg.flowId);
        }
        return;
      }

      if (rawMsg.type === "CLOSE_NIDIN_TAB") {
        const session = await getPairingSession(rawMsg.flowId);
        if (session && session.nidinTabId) {
          try {
            await chrome.tabs.remove(session.nidinTabId);
          } catch {}
          await removePairingSession(rawMsg.flowId);
        }
      }
    });

    port.onDisconnect.addListener(() => {
      if (connectedFlowId) {
        activePorts.delete(connectedFlowId);
      }
    });
  });

  // 2. 內部訊息（來自 Nidin Content Script）
  chrome.runtime.onMessage.addListener((message: ContentToCheckMessage | ContentCandidatePayload, sender, sendResponse) => {
    if (sender.id !== chrome.runtime.id) return;
    if (sender.frameId !== 0 && sender.frameId !== undefined) return;
    if (!sender.origin?.startsWith("https://order.nidin.shop")) return;

    const tabId = sender.tab?.id;
    if (!tabId) return;

    if (message.type === "CHECK_PAIRING") {
      void (async () => {
        const flowId = await getFlowIdForTab(tabId);
        if (!flowId) {
          sendResponse({ shouldPoll: false } as WorkerCheckResponse);
          return;
        }

        const session = await getPairingSession(flowId);
        if (!session || session.candidateReceived) {
          sendResponse({ shouldPoll: false } as WorkerCheckResponse);
          return;
        }

        const now = Date.now() / 1000;
        if (now > session.expiresAt || now > session.leaseExpiresAt) {
          sendResponse({ shouldPoll: false, waitingLease: true } as WorkerCheckResponse);
          return;
        }

        sendResponse({ shouldPoll: true, flowId: session.flowId } as WorkerCheckResponse);
      })();
      return true; // async sendResponse
    }

    if (message.type === "CONTENT_CANDIDATE") {
      void (async () => {
        const flowId = await getFlowIdForTab(tabId);
        if (!flowId) return;

        const session = await getPairingSession(flowId);
        if (!session || session.candidateReceived) return;

        const now = Date.now() / 1000;
        if (now > session.expiresAt || now > session.leaseExpiresAt) return;

        const token = (message.token || "").trim();
        const userId = (message.userId || "").trim();
        if (!token || token.length > 16384 || userId.length > 128) return;

        // 凍結候選
        session.candidateReceived = true;
        session.status = "awaiting_confirmation";
        await savePairingSession(session);

        const port = activePorts.get(flowId);
        if (port) {
          port.postMessage({
            type: "CREDENTIAL_CANDIDATE",
            flowId: session.flowId,
            state: session.state,
            token,
            userId,
          });
          port.postMessage({
            type: "PAIRING_STATUS",
            flowId: session.flowId,
            status: "awaiting_confirmation",
          });
        }
      })();
      return false;
    }
  });

  // 3. 監聽分頁關閉
  chrome.tabs.onRemoved.addListener(async (tabId) => {
    const flowId = await getFlowIdForTab(tabId);
    if (!flowId) return;

    const session = await getPairingSession(flowId);
    if (session && session.nidinTabId === tabId) {
      const port = activePorts.get(flowId);
      if (port) {
        port.postMessage({
          type: "PAIRING_STATUS",
          flowId,
          status: "tab_closed",
          error: "官方分頁已被關閉",
        });
      }
      await removePairingSession(flowId);
    }
  });
}

registerListeners();
