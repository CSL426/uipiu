# Ocard 跟團點單評估

- 狀態：Draft
- 日期：2026-10-01
- 相關：`2026-09-24-nidin-live-ordering.md`（Nidin 點單即送，Ocard 若接入照同一模式）

## 問題

先前研究判定 Ocard「部分可行」：結帳有 reCAPTCHA，伺服器端行為未驗證。要決定的是 Ocard 能不能比照 Nidin，
讓本站點的單直接成為 Ocard 揪團裡的一張跟團單，以及值不值得為此把平台相關程式拆成介面。

## 已實測（2026-10-01，未登入的瀏覽器，對真實揪團）

以訪客身分加入一個進行中的揪團、點一杯、團長端確認看得到、再退團，全程成功。

- 跟團**不需登入、沒有 reCAPTCHA**。三步都是 `POST https://api-order.ocard.co/...`，
  body 為 `application/x-www-form-urlencoded`，前端另會帶 `dtk`（裝置權杖）：
  1. `login/registerDevice`（`group, brand_id, device_name, os, fingerprint`）→ 回 `data.dtk`
  2. `group/join`（`group, brand_id, store_id, group_id, name`）→ 回 `data.order.id`（跟團單號）與整個團
  3. `cart/addItem`（`group, brand_id, store_id, orderType, item_id, count, adds[i][id], adds[i][count], order_id`）
- 退出：`group/leave`（`group, order_id`），會移除該跟團單的品項。
- 成功的回應是 body `code: 500`（不是 HTTP 狀態碼）；其他代碼帶 `msg`，並可能帶 `clearOrder`、`redirect`。
  例：`order_id` 已失效時 `cart/addItem` 回 `496 no such data` 加上 `clearOrder: true`。
- 菜單：`menu/get`（`group, store_id, orderType, orderDatetime`）回整份菜單，帶 `group` 時也決定揪團資訊；
  `menu/item`（`group, brand_id, store_id, item_id`）回單品規格：`adds[]` 為規格群組（`min`、`max`、`required`），
  `adds[].options[]` 有 `id`、`name`、`price`、`soldOut`。可對應本站 `NormalizedMenu`。
- 同步：`store/get` 帶 `group_id` 就回整個團，**不必加入、不必登入**（伺服器端 curl 實測）。
  `group/join`、`cart/addItem` 的回應也附同樣的 `data.group`：`status`（`ORDERING`…）、`host.name`、
  `orders[]`（每張跟團單的 `id`、`name`、`paid`、`total`、`items[]`，品項含 `adds[]` 規格名稱與加價）。
  欄位足以對照本站的跟團清單。
- reCAPTCHA v3 只在團長結帳：`group/lock` → `cart/checkout`（帶 `googleReCaptchaToken`、TapPay `prime`）。
  團長本來就在 Ocard 自己結帳，不影響跟團送單。

## 實測時踩到的

- 必須**先** `group/join` 拿到跟團單號，再 `addItem`。先加購物車會建立一張不在團裡的個人購物車。
- `menu/get` 不含規格，要逐項呼叫 `menu/item`（實測一家 50 項約 4.7 秒）。規格群組與選項的 id 在全店共用、
  定義一致（24 組、85 個選項無衝突），可以直接當本站的 `MenuGroup`。
- 同一杯飲料可出現在多個分類：菜單 `id` 不同、`item_id` 相同。`addItem` 用的是菜單 `id`，跟團單上的品項只帶 `item_id`。
- 選項的 `block` 是互斥規則（例：溫／熱不能加春梅凍、馬告檸檬凍）。
- 同一裝置殘留的舊 `order_id` 會讓 `addItem` 回 496；伺服器端每次送單應用新的 `dtk` 與新的 join。

## 隱私

`data.group.property` 含團長外送地址的經緯度與地點，所有跟團者都拿得到。本站同步時不得儲存或顯示這個欄位。

## 未解

- 團長刪別人的跟團單有沒有端點（Nidin 有 `subOrder` 刪除），未知。跟團者自己可 `group/leave`。
- `dtk` 有效期、同一 `dtk` 能否加入多個團，未驗證。
- 服務條款：跟團用訪客身分，不碰「共用帳號」；但伺服器直接呼叫未公開 API，仍可能落在「非官方程式存取」條款內，
  原文尚未逐條確認。實際風險較可能是被封鎖，而非帳號受影響。

## 決定（2026-10-01 實作）

1. **平台介面** `app/platforms.py`：開團、抓菜單、確認揪團中、送跟團單、判斷「確定沒送出」、讀跟團清單、取消。
   Nidin 與 Ocard 各一個實作。防重送、取消憑證、節流、對帳留在 `routers/live_orders.py`，兩個平台共用。
2. **資料**：`sessions.provider`（舊資料預設 `nidin`）；Ocard 的 `store_id` 存成 `品牌/分店`。
   `order_submissions.cancel_ref` 存取消要用的平台憑證。`nidin_order_id` 仍是 Nidin 專用，不改名。
3. **開團**：後台貼連結，網址主機是 `order.ocard.co` 就走 Ocard。不需要主揪的 Ocard 帳號。
4. **送單**：每張跟團單用一個新的裝置權杖（`registerDevice` → `group/join` → 逐杯 `cart/addItem`）。
   - 送出前先在本站擋互斥選項，並確認品項還在 Ocard 菜單上。
   - join 之後任何一杯加失敗就 `group/leave` 撤回，回報「確定沒送出」可重試；撤回也失敗才標成結果不明、等同步對帳。
   - Ocard 沒有電話欄位：前端在 Ocard 團隱藏電話，後端也不送。
5. **取消**：用送單時記下的裝置權杖 `group/leave`。沒有權杖（團長自己或別人直接在 Ocard 點的）就回 409 說明。
6. **同步**：`store/get` 讀團，品項用 `item_id` 對回本站菜單 id，規格名稱照 Nidin 的方式對回選項 id。
   不保存團長地點。菜單在行程內快取 5 分鐘，避免每次刷新都逐項重抓。
