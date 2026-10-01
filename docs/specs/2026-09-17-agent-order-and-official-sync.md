# AI Agent 智慧點餐與你訂 (Nidin) 官方團購對帳規格書

本文件詳細記錄 `nidin-menu-parser` 於 2026-09 擴充之「AI Agent 自然語言點餐」與「團長官方訂單同步與分飲料對帳」核心規格。

---

## 1. 系統架構與流程概觀

```
[同事 (LINE / Web)]
  │
  ├─ 方式 A: 在 Web 手動選品項客製化
  │
  └─ 方式 B: 直接貼純文字「小明 +2 大杯奶茶 正常冰半糖加珍珠」
         │
         ▼
[AI Agent (AgentParser / agy)]
  │  1. 讀取當前店家最新菜單結構 (Menu, Groups, Options)
  │  2. 自動識別品項、容量、甜度、冰量與加料 ID
  │  3. 若缺漏必填規格，主動切換 reply 追問
  ▼
[共享購物車 (CartStore - SQLite)] ── 每一杯飲料綁定 contributor_name
  │
  ├─ 模式 1: 系統代為一鍵注入 Nidin 官方團購
  │     │
  │     ▼
  │  [Nidin 官方團購案] ◄── [同事也可以用官方 LINE 點餐]
  │     │
  │     ▼
  └─ 模式 2: 團長帶 MC-API-Token 抓取官方團購名單
        │
        ▼
   [分飲料與收款對帳表 (Split Sheet)]
        - 依訂購人自動分組、計算每人應付金額
        - 提供「已收清 / 待收款」打勾確認
        - 自動彙整外送「總杯數盤點清單」
```

---

## 2. AI 點餐大腦 (AgentParser)

### 2.1 解決的痛點
* 各家手搖飲店菜單規格不一（50嵐、迷客夏、得正、麻古等），品項大小杯可能在品名或在選項。
* 台灣手搖黑話多（「微微」= 微糖微冰、「波霸/珍珠/混珠」等），傳統 Regex 經常衝突或解析失敗。
* 同事經常漏講必填項目（例如漏講冰量），傳統系統硬送會被 Nidin 官方 API 報 422 拒收。

### 2.2 實現機制 (`app/agent_parser.py` 與 `app/menu_graph.py`)
* **動態菜單圖譜 (Hierarchical Menu Graph)**：
  - 以 `ItemNode`、`DimensionNode`、`OptionNode` 建立結構化圖模型。
  - 建立名稱、簡稱、倒排字元與同義詞索引，解決台灣手搖飲品項繁雜、字串長度與選項修飾詞問題。
* **分層雙層處理架構 (2-Tier Architecture)**：
  1. **Tier 1 (Menu Graph 快速路徑，0ms / 0 Token)**：
     - 採用非重疊區間匹配 (Non-overlapping Span Matching)，徹底杜絕子字串誤傷（如「正常」誤判「正常冰」）與規格重複選取。
     - 自動校驗 `min_select` 與 `max_select` 約束，若有缺漏必選規格（如漏講甜度冰塊），即刻生成友善追問，完全不耗費 LLM 呼叫。
     - 支援單筆自然語言點單與多行多人批次點單（Batch Order）。
  2. **Tier 2 (窄圖剪枝 Prompt + LLM 後備)**：
     - 若使用者輸入含開放式推薦問題（如「推薦什麼茶？」）或複雜語意歧義，系統利用圖譜搜尋將數百項的龐大菜單裁剪為 <1.5KB 的局部精確子圖 (Narrow Subgraph)。
     - 徹底解決大型菜單（如 50嵐 195 品項、黑沃咖啡 597 品項）高達 214KB flat prompt 引發的 `OSError: [Errno 7] Argument list too long` 系統限制。
* **推理引擎 (Engine)**：
  - 本機免 Key 模式：呼叫本機安裝之 Google Antigravity CLI (`agy -p`)，直連頂級 Gemini 模型。
  - 生產環境模式：支援透過 `GEMINI_API_KEY` 呼叫官方 `google.genai` SDK。

### 2.3 核心手搖維度智慧強制選取與豁免機制 (Smart Mandatory Enforcement)
* **痛點**：Nidin 上部分店家（如大茗本位製茶堂）後台未將甜度、冰塊設為必填（`min_limit = 0`，介面標示「可不選」）。若使用者或同事忘記指定，送出至店家往往會被做成全糖正常冰，造成客訴與悲劇。
* **我們系統這層的智慧升級**：
  1. **手搖核心維度強制選取 (Core Dimension Enforcement)**：
     - 針對「溫度（冰塊）」、「甜度」、「尺寸（容量）」等核心維度，若該商品提供 **>1 種選項**且**非全部固定**，系統在此層強制升級為 `必選 1 項`。
     - **Web 手動介面**：彈窗標示「必選 1 項」，未選取時按「加入購物車」會明確阻擋並提示「請選擇 溫度」等防呆。
     - **AI 點單 Agent**：若同事未說甜度冰塊（例如「小明 +1 紅玉紅茶」），AI 會主動發起親切追問，要求補齊必選規格。
  2. **固定規格智慧豁免與自動帶入 (Auto-attach Fixed Option)**：
     - 若該品項的某維度**僅有 1 個選項**（例如大茗「青抹浮島」只有「固定冰量」與「M 杯」）或選項名稱全為「固定」：
       - Web 介面標示為「固定規格」，並自動預設勾選。
       - AI Agent 自動帶入該唯一選項（例如自動帶入 `固定冰量`、`M`），不煩擾同事反覆確認。
  3. **加料與加購維持選填**：
     - 配料（波霸珍珠、奶蓋等）維持 `可不選`，絕不強制多扣費用。

---

## 3. 你訂 (Nidin) 官方認證與團長抓單機制

### 3.1 認證原理 (Authentication)
* 你訂官方的 LINE 登入是由官方前端將 LINE OAuth code/token 送往：
  `POST https://loctw-service-api.nidin.shop/shopper/v2/user/loginWeb`
* 成功後伺服器會核發認證金鑰：
  - **Token**：對應 HTTP Header `MC-API-Token`
  - **User ID**：對應 HTTP Header `MC-API-User`
* 只要持有這組 Token，即代表團長身分，可查看該團長名下所有的團購訂單。

### 3.2 團長抓單 API (Official Group Buy Info)
* **API 端點**：`GET https://loctw-service-api.nidin.shop/shopper/v2/groupBuy/{group_buy_code}/info`
* **所需 Headers**：
  ```http
  MC-API-Token: <你的 Token>
  MC-API-User: <你的 User ID>
  Origin: https://order.nidin.shop
  Referer: https://order.nidin.shop/
  User-Agent: Mozilla/5.0 ...
  ```
* **回傳關鍵資料**：
  - `group_buy_head_order_info`：團購案名稱、店家、目前累積總額。
  - `group_buy_follower_order_list`：所有跟團同事的訂單陣列，每一筆包含：
    - `name` / `nickname`（跟團同事 LINE 暱稱）
    - `total_money`（個人小計）
    - `product` / `product_simple`（飲品品名、數量、規格選項、備註）

### 3.3 如何取得團長 Token（使用者操作指引）
1. 在電腦瀏覽器（Chrome / Edge）打開 [你訂官網](https://order.nidin.shop/)，以 LINE 登入。
2. 按 `F12` 打開開發者工具，切換至 **「應用程式 (Application)」** 分頁。
3. 在左側欄展開 **「本機存放區 (Local Storage)」**，點選 `https://order.nidin.shop`。
4. 找到以下兩項數值並複製：
   - `Nidin_token`（例如：`eyJhbGciOiJIUzI1...`）
   - `Nidin_userId`（例如：`12345678`）
5. 在本系統的後台頁面（`/admin`）點擊 **「設定團長憑證」**，貼上並儲存即可。

---

## 4. REST API 規格

### 4.1 團長憑證管理
* **`GET /leader/auth`**
  - 回傳：`{"is_authenticated": bool, "user_id": str, "masked_token": str}`
* **`POST /leader/auth`**
  - Payload: `{"token": "...", "user_id": "..."}`
  - 回傳更新後之狀態。
* **`DELETE /leader/auth`**
  - 清除儲存之團長憑證。

### 4.2 AI 智慧點單
* **`POST /cart-sessions/{session_id}/agent-order`**
  - Request: `{"text": "小明 +2 大杯奶茶 正常冰半糖", "contributor_name": "小明"}`
  - Response:
    ```json
    {
      "action": "add_to_cart",
      "reply_message": "已為小明將 2 杯大杯奶茶加入購物車！",
      "items_added": 2,
      "cart_session": { ... }
    }
    ```
  - 若規格缺漏：
    ```json
    {
      "action": "reply",
      "reply_message": "小美您好！阿薩姆紅茶需要選擇溫度喔，請問要正常冰還是熱的呢？",
      "items_added": 0,
      "cart_session": { ... }
    }
    ```

### 4.3 官方訂單同步
* **`GET /cart-sessions/{session_id}/official-orders`**
  - 需已設定團長憑證（否則回傳 401）。
  - 回傳官方 `OfficialGroupBuySummary`，含所有官方跟團者點單。
* **`POST /cart-sessions/{session_id}/sync-official`**
  - 將官方的跟團者訂單自動轉換並合併寫入本地 Session 的購物車中。

---

## 5. 前端功能與操作

1. **公開點單頁面 (`/`)**
   - 支援點擊飲料品項進行傳統規格選取。
   - **新增「🤖 AI 快速點單」卡片**：直接輸入自然語言，一秒加入購物車；若缺漏規格會直接彈出提示訊息氣泡引導確認。
2. **管理員後台 (`/admin`)**
   - **新增團購**：輸入 Nidin 團購連結即可發起。
   - **團長憑證綁定**：點擊右上角「設定團長憑證」，支援隨時更新與解除綁定。
   - **同步官方點單**：點擊「📥 同步官方點單」按鈕，即時拉取大家在官方 LINE 點的每一杯飲料。
   - **分飲料與對帳表 (Split Sheet)**：
     - 自動依點單人分類，列出個人應付總額與飲料細節。
     - 具備「待收款 / 已收清」切換按鈕（狀態自動保存在瀏覽器 LocalStorage）。
     - 頂部提供「🥤 外送總杯數盤點清單」，外送送達時能第一時間清點外送袋內各品項總杯數是否齊全。
