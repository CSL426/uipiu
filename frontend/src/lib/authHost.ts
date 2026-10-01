/**
 * 掃碼位址處理。
 *
 * 電腦端開 localhost 時，手機掃到的 QR 會指向手機自己的 localhost，當然連不上，
 * 所以要把網址換成同一個區網的 IP。使用者可在介面上改，記在 localStorage。
 * 沒有內建預設 IP：每個部署的區網都不一樣，猜錯的位址會把登入票送到別台機器。
 */

const HOST_STORAGE_KEY = "nidin_qr_custom_host";

export function isLoopbackHostname(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1";
}

/** 讀出目前要寫進 QR 的位址：使用者設定優先，否則用目前網址（localhost 時請使用者在介面上改成區網 IP）。 */
export function readInitialHost(): string {
  if (typeof window === "undefined") return "";
  const saved = localStorage.getItem(HOST_STORAGE_KEY);
  if (saved) return saved;
  return window.location.host;
}

export function saveHost(host: string): void {
  localStorage.setItem(HOST_STORAGE_KEY, host);
}

/**
 * 把後端給的 auth_url 換成手機連得到的位址。
 * 只有原網址指向 loopback 時才換——已經是對外網址就別動它。
 */
export function resolveTicketUrl(
  ticket: string,
  originalUrl: string,
  hostToUse: string,
  param: "auth_ticket" | "claim_ticket",
): string {
  if (!hostToUse) return originalUrl;
  try {
    const url = new URL(originalUrl);
    if (isLoopbackHostname(url.hostname)) {
      return `${url.protocol}//${hostToUse}/admin?${param}=${ticket}`;
    }
    return originalUrl;
  } catch {
    return `${window.location.protocol}//${hostToUse}/admin?${param}=${ticket}`;
  }
}
