# Nidin 團長憑證取得流程 — 問題整理

狀態:Draft
日期:2026-09-21
專案:本 repo(nidin-menu-parser)

後續設計：[瀏覽器擴充功能與一次性配對（Draft）](2026-09-21-nidin-extension-pairing.md)。本文件保留原始問題分析；實作範圍、已修正的推論與協定以後續規格為準。

---

## 這份文件要解決什麼

本系統需要 Nidin 官方的 `Nidin_token`,才能呼叫官方 API 抓取跟團者的訂單明細做對帳。
目前取得 token 的流程需要使用者手動操作,想知道**能否自動化**。

核心問題:**能不能開個小窗讓使用者在 Nidin 登入,然後由我們的程式把 token 撈回來?**

---

## 背景:兩套不相干的認證,不要搞混

| | 本站後台登入 | Nidin 官方憑證 |
|---|---|---|
| 目的 | 登入自己的管理後台 | 取得官方 API 存取權 |
| 網域 | 自有網域 | `order.nidin.shop` |
| 機制 | 自家帳密 / QR 票券 | 撈對方 localStorage |
| 程式位置 | `app/auth.py` | `frontend/src/lib/qr.ts` |

兩者都有「QR 掃碼」,但完全無關。本文只討論**右邊那欄**。

**Nidin 沒有提供 OAuth。** 已確認整個 codebase 無任何 OAuth 實作;
介面上的「LINE 帳號登入」「Google 帳號登入」兩顆按鈕,程式碼就是
`<a href="https://order.nidin.shop" target="_blank">`,純外連,沒有 client_id、
沒有 redirect_uri、沒有 callback route。LINE/Google 是 **Nidin 官方站自己的**
帳號系統,不是我們串的。

---

## 目前的流程(三種,都要手動)

入口:後台 →「設定團長憑證」Modal,三個 tab。

1. **書籤提取**(`getNidinBookmarkletCode()` in `frontend/src/lib/qr.ts:110`)
   - 使用者把一段 `javascript:` 書籤拖到書籤列
   - 在 `order.nidin.shop` 登入後,點該書籤
   - 書籤讀 `localStorage.getItem('Nidin_token')`,畫成 QR 碼彈在畫面上
   - 使用者再用本站掃碼匯入
2. **掃碼/匯入** — 上傳上述 QR 截圖,`parseQrFromImageFile()` 解析
3. **手動輸入** — F12 → Application → LocalStorage 複製貼上

`parseQrCredentials()` 已支援三種格式:JSON、query string(`token=...&userId=...`)、純 token 字串。

---

## 為什麼「開小窗自動撈」不可行

### 同源政策擋的是「讀取」,不分誰發起

```js
const win = window.open('https://order.nidin.shop/login/');
// 使用者在小窗裡登入完成後…
win.localStorage.getItem('Nidin_token');
// ❌ SecurityError: Blocked a frame with origin "<我方網域>"
//    from accessing a cross-origin frame.
```

其他變體同樣被擋:

```js
iframe.contentWindow.localStorage          // ❌ 同樣 SecurityError
fetch(url, { credentials: 'include' })     // ❌ CORS 擋;且 localStorage 不隨請求送出
win.document / win.location.href           // ❌ 讀取即拋錯
```

`window.open()` 回傳的 `win` 只是個不透明把手:可以 `.close()`、可以
`.postMessage()` **送出去**,但碰 `.localStorage` / `.document` 就拋
SecurityError。**沒有任何我方可單邊開啟的設定、flag 或 header 能繞過。**
這道牆的存在理由正是防止任意網站開小窗偷讀他站 token。

### 唯一合法途徑需要對方配合

跨網域主動送資料只有 `postMessage`,但那行必須**由 Nidin 的頁面執行**:

```js
// 這段必須跑在 order.nidin.shop 上,由他們寫
window.opener.postMessage({ token: localStorage.Nidin_token }, '<我方網域>');
```

他們不會寫 —— 那等同主動把 token 交給第三方,本來就該由 OAuth 承擔。

### 結論

**撈取動作必須發生在對方網域內**,而把程式碼送進對方網域的唯一合法方式,
就是**使用者自己手動觸發**(書籤 / F12)。

現行書籤方案不是土砲,是無 OAuth 情況下的標準繞法:
書籤點下時,該段 JS 就是 Nidin 網域的一份子,讀自家 localStorage 完全合法。
QR 碼則扮演「跨越網域邊界的搬運工」——圖片靠眼睛/相機傳遞,不受同源政策約束。

**那個「使用者手動點一下」的步驟省不掉。**

---

## 可以改善的方向(待討論)

### A. 書籤直接導回本站(單機情境,價值最高)

書籤最後不彈 QR,改成直接把 token 帶回我方網址:

```js
window.open('https://<我方網域>/admin?nidin_token=' + encodeURIComponent(t)
            + '&nidin_user=' + encodeURIComponent(u));
```

- **合法**:這是從對方網域「推」出來、且由使用者觸發的導航,不是跨域讀取
- **效果**:單機情境從「彈 QR → 拿手機掃 → 匯入」三步變一步
- 手機情境仍保留 QR 路徑
- 前端需新增處理 `?nidin_token=` 的邏輯;`parseQrCredentials()` 已支援 query string 格式
- **待確認**:token 出現在 URL 的風險(瀏覽器歷史、referrer、server log)。
  建議收到後立刻 `history.replaceState` 清掉,且該頁不得再對外發請求

### B. 小窗 + 步驟指引

目前點「LINE 帳號登入」是開新分頁,使用者就此斷線,要自己記得回來點書籤。
改成小窗並在主視窗同步顯示「第 2 步:請點書籤列的提取憑證」,小窗關閉時
自動聚焦到掃碼輸入框。不減步驟,但讓使用者知道自己在第幾步。

### C. 三個 tab 改成有順序的步驟

現在「手動輸入 / 掃碼匯入 / 書籤提取」攤平成三個平行 tab,看起來像三選一,
實際上是「書籤是設定、掃碼是執行」的先後關係。應改為步驟式引導。

---

## 給接手者的問題

1. A 方案把 token 放 URL,在這個自用系統的威脅模型下可接受嗎?有沒有更好的推回方式?
2. 有沒有我漏掉的、能在**不需要對方配合**的前提下跨網域取得 token 的合法途徑?
3. 書籤安裝(拖曳到書籤列)對一般使用者是門檻。有沒有更低摩擦的替代?
   (註:Chrome 擴充功能可以,但那是另一個安裝門檻)

---

## 相關檔案

- `frontend/src/lib/qr.ts` — 書籤程式碼產生、QR 解析、憑證格式解析
- `frontend/src/components/LeaderAuthModal.tsx` — 憑證設定 Modal(三個 tab)
- `app/nidin.py` — 呼叫官方 API 時帶上 token 的地方
- `app/auth.py` — 本站自己的認證(與本文無關,勿混淆)

## 2026-09-24 補充：手機＋密碼代登入（Draft）

- Nidin 官網有原生手機＋密碼登入：`POST /shopper/v2/user/loginWeb`，body
  `{type:1, account, password, device_id, identity:"nidinphone"}`，回應帶 `token`、`user_id`。
  來源是 order.nidin.shop 公開 JS bundle（`nidinLogin()`），**尚未用真實帳號打過**。
- 後台綁定視窗第一個選項改為「手機＋密碼」（`POST /leader/auth/password`）；擴充功能改標為
  「LINE／Google」，給沒設密碼的帳號用。
- 勾選「記住密碼」時，密碼以 Fernet 加密存在 `leader_nidin_logins`。金鑰優先讀
  `NIDIN_CREDENTIAL_KEY`，否則在資料庫旁產生 0600 的 `nidin-credential.key`（只擋得住單獨外流資料庫）。
- 同步官方點單遇到憑證失效（HTTP 401/403，或 body `status` 為 1303/1305/1306/1307）時，
  有記住密碼就重新登入並重試一次；重登被拒就刪掉記住的密碼並要求重新綁定。
  失效回應的實際格式只從官網前端程式碼推得，**未實測**。
- 用手動貼上或擴充功能綁定別的憑證時，會刪掉記住的密碼，避免自動重登把帳號換回去。
- 待辦：實測 token 壽命與 `replaceToken` 輪替後舊 token 是否作廢。
- 用 Nidin 帳號登入本站（`POST /auth/nidin-login`）：只接受已連結的 Nidin 會員。連結只在
  「手機＋密碼」綁定成功時建立（user_id 由 Nidin 伺服器回傳），手動貼上的 user_id 不算。
- `NIDIN_OWNER_PHONE`：站長手機。用它以 Nidin 登入成功時，自動連結到第一位管理員；
  站上還沒有管理員就建立一個（本站密碼為隨機值）。移除了程式從未讀取的 `NIDIN_ADMIN_PASSWORD_FILE`。
- **2026-09-24 實測**：站長以 Nidin 帳號登入成功，`/user/loginWeb` 回應格式與推測一致（`token`、`user_id`）。
  憑證是 JWT（header `typ: SK_Token`、`alg: HS256`），`exp` 為簽發後 **30 天**；以它 `GET /user/{uid}/info`
  回 HTTP 200／`status` 200，回應含 `token_need_replace`（當時為 0）。伺服器端提前作廢或輪替的時機仍未知。
- 後台「設定 Nidin 連線」打開時會 `POST /leader/auth/check`：實際向 Nidin 讀會員資料，顯示會員名稱、ID、
  憑證到期日；憑證失效且有記住密碼時先重登再顯示。按鈕文字改為「✓ Nidin 已連結（到 MM/DD）」。

## 2026-09-24 補充：開團與團購狀態（實測 + bundle 研究，Draft）

- **開團**（已實測成功一次）：`POST /carts/item`，body
  `{brand_id, store_id, menu_id, menu_ver_stamp, order_type: 2, is_empty: 1, delivery_type}`，回
  `order_id / order_sn / sharing_code / sharing_link`。`brand_id` 來自公開 `GET /store/{id}/info`，
  `menu_id`、`version.stamp` 來自公開 `GET /store/{id}/onShelfMenuVer`，`delivery_type` 取 `delivery_info` 中
  `is_default == 1` 的那筆。前提：`/user/{uid}/info` 的 `phone_need_verify == 0`。團名由 Nidin 自動命名（「XXX開的團」）。
- 開團**不會**送單或付款。真正下單只在團長結帳最後一步 `POST /order/productCouponFlow`。
  團長在結帳頁按「下一步」時官網會以 `PUT /order/{id}/infoCouponFlow` 帶 `group_buying_status: 20` 自動關團。
  刪除空團：`DELETE /carts/{order_id}`（不可還原）。
- `group_buying_status`：10 揪團中、20 已關團。`group_buy_info.role`：1 團主、2 非團主（參加者也讀得到全部跟團訂單）。
  訂單狀態碼：31 揪團中、35 跟團成功、38 被團長刪單、39 揪團失敗（菜單已更新）、100 未接單、110 已接單、99 取消。
- 同步解析修正（以 3 個真實舊團 22 筆驗證）：規格取 `product[].adjusts[].options[]`、金額取 Nidin `total_money`、
  名字取 `contact_name`、狀態取 `head_buying_status`；重新同步以 `lines.official_order_id` 整批替換。
- 未解：`GET /order/list` 以官網參數仍回 400（1101 輸入資訊錯誤）；`token_need_replace` 輪替（`GET /user/{uid}/replaceToken`）尚未處理。
