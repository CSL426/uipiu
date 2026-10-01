# Nidin 憑證連結（非官方）：瀏覽器擴充功能與一次性配對

狀態：Draft

日期：2026-09-21

專案：本 repo

前置分析：[憑證取得問題整理](2026-09-21-nidin-credential-acquisition.md)

## 1. 目標與範圍

讓已登入本站後台的主揪按「連結 Nidin 帳號」，在官方頁面完成登入後，自動取得官方憑證並帶回本站；主揪確認後才儲存。第一次需安裝並授權擴充功能，之後不必使用書籤、F12、QR 或複製貼上。

本功能只取得使用者本人在 Nidin 瀏覽器工作階段中的憑證，不代理 LINE／Google 登入、不收集官方帳密、不自動送出訂單、不自動同步官方訂單。

首版支援桌面 Chrome／Edge 的一般瀏覽模式與 Manifest V3。手機、無痕、跨瀏覽器 profile、跨裝置自動配對不在首版範圍；保留現有手動匯入，QR 在修復並實測前不得承諾可用。

這是待實作的設計，不表示擴充功能、API 或真實登入驗收已完成。文件只有使用者確認後才能改為 Done。

## 2. 兩套認證與已確認事實

- 本站登入：`nidin_session` HttpOnly cookie，由 `app/auth.py` 驗證本站使用者。
- 官方憑證：Nidin 頁面的 localStorage `Nidin_token`、`Nidin_userId`，用於官方 API。
- 一般本站網頁不能讀取 Nidin localStorage；擴充功能以明確的站點權限在官方頁面執行 content script，取得使用者授權讀取的資料。
- 本專案尚未串接 Nidin OAuth；不能由本地沒有 OAuth 程式碼，推論官方完全沒有第三方授權服務。
- `app/routers/leader_auth.py` 已提供按本站 user.id 隔離的官方憑證 CRUD。目前儲存只檢查 token 非空，不代表官方已驗證有效。
- 後端整合已拆為 `app/nidin/{client,normalizer,builder}.py`，舊文件中的 `app/nidin.py` 路徑已過期。
- 原分析的 query 範例 `nidin_token/nidin_user` 不受現有解析器支援。本方案傳結構化訊息，不依賴 URL／QR 解析器。

## 3. 使用者流程

1. 使用者先登入本站後台，開啟「Nidin 官方帳號」設定。
2. 本站檢查擴充功能是否已安裝、版本是否相容；未安裝時顯示安裝說明與「重新偵測」。
3. 按「連結 Nidin 帳號」，本站建立有效期 5 分鐘的一次性配對，並與擴充功能握手。
4. 擴充功能建立專用的 `https://order.nidin.shop/` 分頁，記錄其 tab ID，不接管既有的其他官方分頁。
5. 官方頁面若未登入，使用者照常選 LINE／Google 並完成驗證。本站顯示「請在你訂頁面完成登入」。
6. 指定官方分頁讀到非空 token 後，擴充功能將候選憑證送回原本的本站分頁；本站顯示「已取得憑證，請確認綁定」。
7. 顯示本站接收帳號、官方 user ID（缺少時明示未提供）、憑證遮罩，以及是否會取代舊綁定。不得把 user ID 說成已驗證的姓名，也不得把「已讀到 token」說成「官方驗證成功」。
8. 使用者按「確認綁定」後，後端在同一交易內驗證配對與更新憑證。成功後顯示「已儲存官方憑證」，清除暫存，提供「關閉官方分頁」按鈕。

已登入官方時可直接使用目前的官方帳號，不強制重新登入。若想換帳號，使用者先在官方登出再登入，本站按「重新取得」，取消舊 flow、清除舊候選並建立新 flow；不可在等待確認時悄悄替換候選 token。

安裝一次不等於永久授權自動採集；每次讀取都必須由未逾期、綁定指定分頁且持有短期採集許可的配對流程觸發。伺服器撤銷到瀏覽器停止之間存在有限傳播時間，依下文許可期限控制；後端確認不接受已撤銷流程。

## 4. 架構與信任邊界

```text
本站已登入後台 ── 同源 HTTPS＋cookie ── 本站配對 API／憑證儲存
       │
       │ chrome.runtime.connect(extensionId)
       │ 僅允許部署白名單網站
       ▼
擴充功能 service worker ── runtime 訊息 ── Nidin content script
       │                                      │
       └─ 綁定本站 tab、官方 tab、flow ID       └─ 只讀兩個 localStorage key
```

擴充功能不直接呼叫後端保存 API、不讀本站 cookie。候選 token 經擴充功能訊息通道交給原本站頁面，再由該頁面以同源 POST 提交；不新增跨來源可寫入的憑證 API，不放寬 CORS。

不使用 window.opener 作為配對依據，因此官方登入過程的 noopener／COOP 不應決定回傳能否成功。真實登入若另開官方子分頁，首版只監看配對的原分頁；待它回到官方站或重新取得時讀取共享 localStorage。不掃描所有使用者分頁來找 token。

信任範圍為本機瀏覽器、已安裝擴充功能、本站程式及 Nidin 網站。本站 XSS 或惡意擴充功能可讀取頁面記憶體，並非一次性 state 能解決的問題。

## 5. 擴充功能權限與部署設定

擴充功能放在專案 `extension/`，使用 Manifest V3，不依賴外部 CDN 或遠端可執行程式碼。

- 宣告 `storage`；使用 `chrome.storage.session` 保存非憑證的配對中繼資料，維持預設僅 trusted contexts 可讀。
- content script 僅匹配 `https://order.nidin.shop/*`、只在頂層 frame 執行；不注入 LINE／Google 網站。
- 首版採宣告式 content script，無必要不加入 `scripting`、`cookies`、`webRequest`、`debugger`、`<all_urls>` 或廣泛 `tabs` 權限。建立／移除分頁與使用 sender.tab.id 不代表必須申請讀取所有分頁的權限。
- `externally_connectable.matches` 只列明確的本站部署主機；不得使用任意網站萬用匹配。
- service worker 再檢查 sender URL 的完整 origin（scheme、host、port）、頂層分頁與本站 `/admin` 路徑。manifest 匹配不能取代精確 port 驗證。
- 預設開發 origin 為 `http://localhost:6464`、`http://127.0.0.1:6464`；正式設定使用確定的 HTTPS origin，不接受 LAN IP 的明文 HTTP。
- localhost 的 manifest 匹配相容性列為第一個可行性驗收；若目標瀏覽器不支援，開發環境改用固定 HTTPS 開發網域，不擴大至所有網站。
- 新增部署 origin 必須更新擴充功能設定並重新載入／發布；不能由任意網頁訊息要求把白名單改成其他 host。
- unpacked 版以 `manifest.json` 的 `key` 固定 extension ID（`pmmppocaacmfhoodfibonhfoibdheign`），前端內建為 `PINNED_EXTENSION_ID`，換機載入 ID 相同；兩者一致性由 `extension/tests/manifest-id.test.ts` 檢查。商店版 ID 不同，以建置設定 `VITE_NIDIN_EXTENSION_ID` 覆寫。（2026-09-24 修訂：原規定「不得假設換機後 ID 自動相同」在未設 `key` 時成立，已改為固定 key。）

## 6. 訊息協定與生命週期

協定版本從 `1` 開始。本站與擴充功能使用長連線 Port，但正確性不得依賴 Port 永遠保持 service worker 存活。

| 方向 | 訊息 | 必要欄位／行為 |
|---|---|---|
| 本站 → 擴充功能 | `HELLO` | protocolVersion；回報版本與功能，不回傳憑證 |
| 本站 → 擴充功能 | `START_PAIRING` | flowId、state、expiresAt；綁定實際 sender origin/tab ID |
| 本站 → 擴充功能 | `PAIRING_LEASE` | flowId、state、leaseExpiresAt；只接受原配對 Port 更新的短期採集許可 |
| 擴充功能 → 本站 | `PAIRING_STATUS` | flowId、status、可呈現的錯誤代碼 |
| 官方 content script → worker | `CREDENTIAL_CANDIDATE` | token、userId；worker 由 sender 推導官方 tab/frame/origin，不信任 payload 自述來源 |
| 擴充功能 → 本站 | `CREDENTIAL_CANDIDATE` | flowId、state、token、userId；只送到原本站配對 Port |
| 本站 → 擴充功能 | `FINISH_PAIRING`／`CANCEL_PAIRING` | flowId、state；停止讀取並清除中繼資料 |

所有訊息驗證型別、版本、長度與允許欄位。token 首版上限 16 KiB、userId 上限 128 字元；只讀實際值，不要求一定是 JWT，不把缺少 userId 當成空 token。

官方 script 載入時先向 worker 查詢是否有對應 tab 的有效配對，收到指示後才讀取。配對期間每秒檢查一次，加上頁面重新顯示／導航後重連；不只靠 `storage` event，因為寫入 localStorage 的同一文件不一定收到該事件。取得候選或到期即停止輪詢。進入 awaiting_confirmation 後凍結候選，忽略相同 flow 的遲到候選；重新取得一定建立新 flow。

worker 保存 flowId、state、expiresAt、本站 origin/tab ID、官方 tab ID、狀態；不持久化 token。service worker 重啟後從 session storage 恢復對照，重新校驗來源與期限，必要時重新讀官方頁面。瀏覽器重啟、擴充功能更新或狀態遺失時安全失敗，提示重新連結。

Port 斷線後停止傳送候選；同一本站 tab 可以在期限內以相同 flowId/state 重新握手，恢復時由本站回報是否仍持有待確認候選：有候選只恢復狀態，不重新讀取；尚未取得候選才在新採集許可內恢復讀取。不能把候選廣播給其他分頁。本站重新整理會遺失記憶體中的 state，必須開始新配對；舊配對由伺服器撤銷或到期。

## 7. 後端配對 API 與資料模型

瀏覽器使用 `/api/...`；FastAPI 路徑不含 `/api`。新增路由集中在 `app/routers/nidin_pairing.py`，由 `app/main.py` 註冊，沿用現有驗證依賴。

| API | 請求／回應 | 行為 |
|---|---|---|
| `POST /leader/auth/pairings` | 回 flowId、state、expiresAt、本站帳號摘要、credentialRevision | 須本站登入與同源檢查；建立 5 分鐘配對 |
| `POST /leader/auth/pairings/{flowId}/lease` | state；回 status、leaseExpiresAt | 驗證建立時的 session／origin／state，pending 才核發最長 10 秒採集許可，不延長 flow 到期時間 |
| `POST /leader/auth/pairings/{flowId}/confirm` | state、token、user_id | 驗證所有綁定條件，原子更新憑證並消耗配對；回現有 LeaderAuthStatus |
| `POST /leader/auth/pairings/{flowId}/cancel` | state | 撤銷配對；不刪除既有官方憑證 |

SQLite 新增 `nidin_credential_pairings` 表，使用現有資料庫與遷移方式，不另開服務：

- 隨機 flow ID；至少 256-bit CSPRNG state，資料庫只存 state hash。
- 本站 user ID、建立配對時的 login session token hash、完整 origin。
- 建立／到期時間、pending／consumed／cancelled 狀態。
- 建立時的憑證 revision；確認成功的非敏感結果供重試辨識。
- 不在配對表儲存 Nidin token。

同一 login session 建立新配對時，撤銷其舊 pending 配對。不同 session 可同時建立，但更新憑證時必須比較 revision；任何手動儲存、清除或配對儲存都增加同一 revision，避免覆蓋別處剛更新的憑證。revision 必須在清除憑證後仍保留且單調增加，可放在獨立的使用者憑證版本紀錄；不能隨 `leader_credentials` 刪除而歸零。

確認必須綁定建立時的同一 user、同一有效 login session、同一 origin。登出再登入，即使是同一 user，也不能使用舊 state。不得將 HttpOnly cookie 或其 hash 交給前端或擴充功能。

驗證配對、檢查 revision、更新既有 `leader_credentials` 儲存、增加 revision、標記 consumed 必須在同一 SQLite transaction。不可先呼叫會自行 commit 的憑證 setter 再消耗 state。

成功但回應遺失時，同一 session 對 consumed flow 的重試只能回「此配對已完成」與遮罩結果，不再次寫入、不接受替換 token。若憑證 revision 已再變更，回「配對已完成，憑證另有更新」，請前端重新讀狀態，不宣稱舊憑證仍有效。

錯誤分類：401 登入失效；403 同源或配對身分不符；404 無權存取或未知 flow（避免洩漏他人流程）；409 revision 衝突／已取消；410 已到期；422 輸入格式不符。錯誤不得包含原始 token、state 或官方敏感回應。

本站在等待官方登入期間每 5 秒請求 lease，僅經原 Port 傳給 worker；worker 的讀取指令與 content script 輪詢都受 lease 到期時間限制，worker 或 Port 消失不能讓 content script 無限讀取。第一次取得 lease 前不得採集。頁面背景節流、網路中斷或 lease 失效時暫停，重新驗證後才恢復。

本站登出、取消及建立新 flow 時，以同源 BroadcastChannel 傳遞不含憑證的撤銷事件，讓相關分頁通知 worker 停止並清除候選；所有頁面再次聚焦或準備顯示候選時也檢查配對狀態。伺服器端撤銷由下一次 lease 檢查發現，最長採集停止延遲為 10 秒；不宣稱分散式通知能瞬間停止。此期間即使讀到候選，後端也必須拒絕確認。接收事件不得重新授權已取消 flow。

所有配對回應 `Cache-Control: no-store`。沿用既有 cookie 與同源防護；清理過期配對，對建立流程與錯誤確認加使用者／session 層級節流。

## 8. 前端狀態與失敗處理

主要狀態：`extension_missing → ready → opening → waiting_login → awaiting_confirmation → saving → saved`；另有 `cancelled`、`expired`、`error`。

- 未安裝／版本不符：顯示安裝或更新指引，不能停在無限 loading。
- 官方頁已登入：自動帶回目前候選，仍要求本站確認。
- 官方分頁關閉、權限撤銷：停止流程，保留原憑證，提供重試。
- 逾期或本站登出：清除 token/state，重新連結；不自動延長期限。
- 使用者取消：關閉 Port、停止觀察，呼叫 cancel；網路失敗也先清空前端候選，伺服器依期限失效。
- 取得候選時官方 user ID 缺少：顯示「官方使用者 ID 未提供」，允許依既有 API 契約儲存，不捏造身分。
- token 過期／官方拒絕：後續真正呼叫官方 API 時提示重新連結。首版未選定並驗證無副作用的官方身分 API 前，不新增猜測端點來宣稱驗證成功。
- 重複訊息／連點：相同 flow 不重開多個官方分頁，不重複更新憑證。
- revision 衝突：重新讀取目前遮罩狀態，要求重新發起綁定，不靜默覆蓋。

候選 token 與 state 只放本站記憶體，不寫 localStorage、sessionStorage、URL、分析事件或 console。模態關閉、登出、取消與成功後都清空。必要的重連 state 僅保存在擴充功能 trusted session storage，關閉流程後清除。

## 9. 安全與資料限制

- token 不進 query、fragment、頁面標題、通知文字、QR、剪貼簿或日誌；此自動流程不使用上述搬運方式。
- 原始 token 只經受限 extension messaging 與本站同源 POST body 傳遞；production 必須 HTTPS。
- 官方 content script 不執行本站提供的任意 JS、不接受任意 localStorage key、不接受外來目的 URL。
- 來源驗證分兩條路徑：官方 content script 的內部訊息驗證 sender.id 為本擴充功能、官方頂層 frame、精確 origin 與配對 tab；本站 external Port 驗證 sender URL／origin、本站 tab／頂層 frame 與白名單，不要求網頁具有 extension ID。本站回傳處理另驗證原 Port／flow/state，拒絕跨分頁重放。
- 後端寫入只依已驗證的登入身分，不接受請求自行指定 user ID 來選擇存放帳號。
- 既有後端憑證持久化機制保留；本規格沒有額外宣稱資料庫已加密。實作前核對權限、備份與 log redaction，不把原始憑證加進測試 fixture。
- 自動連結不授權任何官方下單或帳號修改。

## 10. 預期檔案與交付面

以下為預定落點，實作時先核對當時 checkout：

- `extension/manifest.json`、`extension/src/background.ts`、`extension/src/nidin-content.ts`、`extension/README.md`：擴充功能、固定部署白名單、安裝與版本指引。
- `extension/package.json`、lockfile、build/test 設定：使用 pnpm，產物放 `extension/dist/`，不把產物或憑證提交。
- `frontend/src/lib/nidin-extension.ts`：版本握手、Port、訊息驗證與清理。
- `frontend/src/components/LeaderAuthModal.tsx`：連結入口、狀態、候選確認與既有手動備援。
- `frontend/src/styles/`：在擁有該元件的樣式模組加入規則，維持現有視覺、彈性尺寸與 rem/em。
- `app/routers/nidin_pairing.py`、`app/nidin_pairing.py`：路由與 SQLite 原子配對邏輯。
- `app/auth.py`：必要的登入 session 身分存取、憑證 revision／transaction 整合；不得重寫本站登入流程。
- `app/models.py`、`app/main.py`：資料契約與路由註冊。
- `tests/`、前端元件測試與 `extension/tests/`：下節測試。
- `compose.yaml`／Dockerfile：增加 extension builder/test profile，沿用 builder→runner 慣例；不另開 host port，所有檔案操作與測試在容器內，建置依序執行。
- `.env.example`、前端 Docker build args／Compose wiring：傳入非秘密的 extension ID 與部署 origin；Vite 建置設定不能只放 runtime environment。
- `README.md` 與原分析文件：連回此規格並修正過度推論；原分析保留為背景，不作協定來源。

本 repo 沒有 ai-config CLI、guide、桌面 bridge 或 Claude plugin；這些交付面不適用。此功能需同時交付本站 UI、後端、擴充功能、部署／安裝說明與測試。

## 11. 驗收條件

### 自動化

- 後端：同一 session 正常綁定；其他 user/session/origin、失效登入、錯誤 state、到期、取消均拒絕；拒絕後既有憑證不變。
- 後端：併發確認只寫入一次；回應遺失重試不重寫；手動更新／清除與配對競爭由 revision 擋住；transaction 失敗不出現半寫入。
- 擴充功能：錯誤官方 tab/frame/origin、未授權本站、偽造欄位、未知協定與過長輸入被拒絕；沒有 active pairing 時不讀 token。
- 擴充功能：跨分頁登出／取消、lease 到期停止、待確認候選凍結、重新取得建立新 flow；冷啟動、worker 終止後恢復、Port 斷線、分頁導航、官方分頁關閉、瀏覽器重啟均有明確結果，不依賴永久背景計時器。
- 前端：未安裝、登入等待、候選確認、錯誤、取消、重試、重複點擊、舊 flow 回應、登入切換與元件卸載的清理。
- 敏感資料：以假 token/state 掃描 URL、console、storage.local/sync、後端／nginx 日誌與錯誤回應，確認無外洩；正常 POST body 與既有必要憑證儲存除外。
- 既有前後端全套回歸測試通過；不以文件舊測試數量取代實際收集結果。

### 本機真實瀏覽器

- 必須在本機 Chrome 實際安裝 unpacked 擴充功能，Edge 另驗證；只跑 mock 或只產出 dist 不算安裝完成。
- 驗證從本站按鈕開始、官方已登入／未登入兩種情境，LINE／Google 官方登入返回後都能取得候選。
- 驗證已有多個 Nidin 分頁、本站多帳號／多分頁、localhost 與正式 HTTPS origin、權限撤銷、擴充功能更新。
- 由使用者完成真實官方登入並確認綁定；agent 不取得帳密、不自動確認覆蓋真實憑證、不為驗收送出訂單。
- 本站帳號名稱、官方 user ID、遮罩與儲存結果一致；若需要確認官方 token 有效性，只用已授權的唯讀官方查詢。
- 保留未能驗證的項目，不將「讀到 localStorage」當成全流程已成功。

## 12. 實作順序與待實測事項

1. 先做不含真實 token 的最小 extension PoC：本站連線、精確 origin/tab 驗證、官方 content script、worker 恢復及 Chrome／Edge 安裝。
2. 實作後端一次性配對與 revision transaction，先通過跨帳號／重放／併發測試。
3. 串上 UI 與候選傳遞，再用假憑證完成瀏覽器端到端測試。
4. 依序建置、在本機安裝並跑真實官方登入；更新安裝文件及驗收紀錄。

待實測：部署的正式 HTTPS origin／extension ID、LINE／Google 的實際返回分頁行為、token/user ID 寫入時序、localhost 外部訊息匹配、受管理瀏覽器的安裝限制。這些是驗收門檻，不以目前推論宣稱已支援。Chrome Web Store／Edge Add-ons 發布另行授權，不含在首版本機安裝中。

## 13. 官方參考資料

- [Chrome：content script 存取所在頁面的 Web Storage](https://developer.chrome.com/docs/extensions/develop/concepts/storage-and-cookies)
- [Chrome：網頁與 extension 訊息傳遞](https://developer.chrome.com/docs/extensions/develop/concepts/messaging)
- [Chrome：externally_connectable 白名單](https://developer.chrome.com/docs/extensions/reference/manifest/externally-connectable)
- [Chrome：service worker 生命週期](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle)
- [Chrome：storage.session 與存取層級](https://developer.chrome.com/docs/extensions/reference/api/storage)

上述資料支持瀏覽器能力與限制；本專案的 5 分鐘期限、配對資料表、API、權限範圍與檔案結構是本 Draft 的設計決策。
