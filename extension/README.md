# Nidin 團購點餐站 憑證連結擴充功能（非官方）

本擴充功能專為點餐系統後台設計，讓主揪在點擊「連結 Nidin 帳號」後，安全自動地將官方登入工作階段中的 `Nidin_token` 帶回本站後台，免去手動按 F12、複製貼上或書籤提取等繁瑣步驟。

---

## 1. 架構與安全性

- **Manifest V3**：採用最新 Manifest V3 標準，僅使用宣告式最小權限。
- **最小權限原則**：
  - 僅要求 `storage` 權限（使用 `chrome.storage.session` 暫存配對中繼資料，不保存明文 token）。
  - 僅在 `https://order.nidin.shop/*` 注入 content script，只在頂層 frame 執行；不注入 LINE / Google 等第三方驗證頁面。
  - 不使用 `scripting`、`cookies`、`webRequest`、`debugger` 或 `<all_urls>`。
- **嚴格來源限制 (`externally_connectable`)**：
  - 擴充功能只接受部署白名單網站的連線，且**白名單含 port**（預設 `http://localhost:6464/*`、`http://127.0.0.1:6464/*`，另含 5173/8000 供開發）。
  - manifest 的 `externally_connectable` 在部分瀏覽器版本不保證能限制 port，因此 service worker 另以 `ALLOWED_WEB_ORIGINS` 比對完整 origin（scheme+host+port）作為實際防線。
  - Service Worker 額外檢查網頁 sender 的完整 origin、頂層 frame 以及 `/admin` 路徑，防止惡意網站竊取憑證。
- **短期採集許可 (Lease)**：
  - 每次採集必須由本站後台建立的 5 分鐘有效一次性配對觸發。
  - 前端每 5 秒向後端續約短期採集許可（最長 10 秒）。一旦逾期、取消或本站分頁關閉，採集立即停止。
  - 一旦讀到憑證，擴充功能即凍結候選，不重複採集。

---

## 2. 本機安裝與載入指引 (Chrome / Edge)

### 步驟 A：建置擴充功能
在專案根目錄或 `extension/` 目錄執行建置：
```bash
cd extension
pnpm install
pnpm build
```
建置產物將輸出至 `extension/dist/`。

### 步驟 B：在瀏覽器中載入 Unpacked 擴充功能
1. 打開 Google Chrome 或 Microsoft Edge 瀏覽器。
2. 進入擴充功能管理頁面：
   - Chrome：網址列輸入 `chrome://extensions/`
   - Edge：網址列輸入 `edge://extensions/`
3. 開啟右上角（或左下角）的 **「開發人員模式」(Developer mode)**。
4. 點擊 **「載入未封裝項目」(Load unpacked)**。
5. 選擇本專案中的 `extension/` 目錄（該目錄包含 `manifest.json` 與已建置的 `dist/`）。
6. 載入成功後，擴充功能 ID 應為 `pmmppocaacmfhoodfibonhfoibdheign`。
   `manifest.json` 的 `key` 把 ID 固定下來，任何電腦載入都一樣，前端已內建這個 ID，**不需要另外設定**。

### 步驟 C（僅限商店版）：覆寫擴充功能 ID
上架 Chrome 線上應用程式商店後，商店版會有自己的 ID。屆時在建置前端時設定：
```env
VITE_NIDIN_EXTENSION_ID=商店版識別碼
```
`tests/manifest-id.test.ts` 會檢查 manifest 的 key 與前端內建 ID 是否一致。

---

## 3. 訊息協定 (Protocol Version 1)

| 訊息類型 | 方向 | 說明 |
|---|---|---|
| `HELLO` | 本站 → 擴充功能 | 探測擴充功能是否已安裝與通訊協定版本 |
| `HELLO_ACK` | 擴充功能 → 本站 | 回應版本資訊 |
| `START_PAIRING` | 本站 → 擴充功能 | 建立專用官方分頁並開始配對流程 |
| `PAIRING_LEASE` | 本站 → 擴充功能 | 傳遞後端核發的短期採集許可（10秒） |
| `PAIRING_STATUS` | 擴充功能 → 本站 | 回報當前配對分頁與狀態 |
| `CREDENTIAL_CANDIDATE` | 擴充功能 → 本站 | 回傳官方讀取的候選憑證供本站確認 |
| `FINISH_PAIRING` | 本站 → 擴充功能 | 確認儲存完成，結束流程並清除暫存 |
| `CANCEL_PAIRING` | 本站 → 擴充功能 | 主動取消流程並清除暫存 |
| `CLOSE_NIDIN_TAB` | 本站 → 擴充功能 | 關閉專用官方分頁 |
