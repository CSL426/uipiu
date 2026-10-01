import type {
  ContentCandidatePayload,
  ContentToCheckMessage,
  WorkerCheckResponse,
} from "./types";

let pollTimer: ReturnType<typeof setInterval> | null = null;
let isCandidateReported = false;

function stopPolling() {
  if (pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function checkAndHarvest(): Promise<void> {
  if (isCandidateReported) {
    stopPolling();
    return;
  }

  try {
    const res: WorkerCheckResponse = await chrome.runtime.sendMessage({
      type: "CHECK_PAIRING",
    } as ContentToCheckMessage);

    if (!res || !res.shouldPoll) {
      if (!res?.waitingLease) {
        stopPolling();
      }
      return;
    }

    // 官方頁面讀取 Nidin_token 與 Nidin_userId
    const token = window.localStorage.getItem("Nidin_token");
    const userId = window.localStorage.getItem("Nidin_userId") || "";

    if (token && token.trim()) {
      isCandidateReported = true;
      stopPolling();

      await chrome.runtime.sendMessage({
        type: "CONTENT_CANDIDATE",
        token: token.trim(),
        userId: userId.trim(),
      } as ContentCandidatePayload);
    }
  } catch {
    // 若擴充功能已解除或重新載入，停止輪詢
    stopPolling();
  }
}

export function initContentScript() {
  if (typeof window === "undefined" || window.top !== window) {
    return;
  }

  // 立即檢查一次
  void checkAndHarvest();

  // 每 1000ms 檢查一次
  pollTimer = setInterval(() => {
    void checkAndHarvest();
  }, 1000);

  // 當使用者切換分頁回來到本分頁時，立即檢查
  window.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      void checkAndHarvest();
    }
  });

  window.addEventListener("focus", () => {
    void checkAndHarvest();
  });

  // 頁面卸載時停止
  window.addEventListener("beforeunload", () => {
    stopPolling();
  });
}

initContentScript();
