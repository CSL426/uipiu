# 你訂 (Nidin) 菜單解析、AI 智慧點餐與團購對帳助手

針對台灣常見的線上訂餐平台**「你訂 (Nidin)」**所開發的自動化點餐、菜單解析、AI 語音/文字點單辨識與**團長分飲料對帳系統**。

> **非官方專案。** 本專案為個人自用工具，與你訂（Nidin）及其營運公司沒有任何關聯，也未獲其授權或背書；「你訂」「Nidin」及各品牌名稱屬其權利人所有。
> 本工具呼叫的是 Nidin 未公開的介面，可能隨時失效；使用者須自行遵守 Nidin 服務條款並承擔帳號風險。
> 同樣也支援 Ocard 線上點（order.ocard.co）的揪團；與 Ocard 亦無任何關聯，呼叫的同樣是未公開介面。

---

## 🌟 核心特色

1. **🤖 AI 智慧自然語言點單 (LLM Agent)**
   - 支援台灣手搖飲日常黑話（例如「微微」= 微糖微冰、「+2 珍奶去冰」）。
   - 每次開團時自動注入當前店家的真實菜單與規格約束，精準匹配品項 ID 與甜度冰量配料 ID。
   - 缺少必填規格時主動親切追問，不盲目送單，告別手寫正規表達式地獄。
   - 支援本機免 Key 模式（調用 Google Antigravity CLI `agy`）或生產環境 `google-genai` SDK。

2. **📥 你訂官方訂單匯入 (Official Sync)**
   - 團長可在系統後台綁定你訂 `MC-API-Token`。
   - 一鍵打你訂官方 API 抓取所有跟團同事在 LINE 上的點單紀錄（含 LINE 暱稱、品項規格、金額）。

3. **📊 外送分杯與收款對帳表 (Split Sheet)**
   - **外送總杯數盤點**：外送送達時，第一時間清點外送袋內各品項總杯數是否齊全（如「大杯奶茶 x4、四季春 x2」）。
   - **人頭分杯與收錢核對**：依點單人分類，清楚標記每人點了哪杯飲料、應付總額。
   - **收款打勾標記**：點擊切換「待收款 / 已收清」，狀態自動保存於本地。

4. **🚀 多人共享購物車與一鍵送單**
   - 支援多人共同加入購物車（記名 contributor），集中彙整點單。
   - 支援一鍵將整車以訪客身分注入官方團購案中，主揪直接在官網刷卡結帳。

5. **🧋 Ocard 線上點揪團**
   - 後台貼上 Ocard 的揪團連結（`order.ocard.co/<品牌>/<分店>?group=<團碼>`）就能開團，不需要主揪的 Ocard 帳號。
   - 大家在本站點的單以訪客身分加入 Ocard 的團（名字＝點單人），本站畫面同步 Ocard 上的跟團清單。
   - 只能撤回從本站送出的單；團長自己或別人直接在 Ocard 點的單，請到 Ocard 處理。結帳一樣由團長在 Ocard 完成。

7. **🔍 依品牌找店、直接開團（Nidin）**
   - 管理頁「在 Nidin 開新團」：輸入品牌，依「離我最近」（從主揪在 Nidin 的常用地址算）或「評分相對高」（評價數加權、8 公里內）列出分店，按兩次「開團」就在主揪的 Nidin 帳號開好並加進本站。
   - 不要的團可在團購管理按「從本站移除」，只刪本站紀錄。

6. **🤖 給外部 agent 用的 MCP**
   - 主揪在管理頁「外部 agent 的 API key」發 key：**點單**（查團、看菜單、點單、撤回自己的單）或**主揪**（再加上開團、撤回團裡任何一筆、標記收款）。key 只顯示一次。
   - agent 連 `https://<你的網域>/mcp`（MCP streamable HTTP），帶 `Authorization: Bearer <key>`。工具：`list_groups`、`get_menu`、`place_order`、`list_orders`、`cancel_order`、`open_group`、`set_paid`、`find_stores`、`open_new_group`。
   - Claude Code 例：`claude mcp add --transport http mynig https://<你的網域>/mcp --header "Authorization: Bearer <key>"`
   - 送單走和公開頁同一條流程（防重送、Nidin／Ocard 皆可）；每把 key 各自限流。REST 文件在 `/api/docs`。

---

## 🏗 技術架構

* **後端**：FastAPI (Python 3.12)、Pydantic v2、SQLite (WAL 模式)、HTTPX、Google GenAI / `agy`
* **前端**：React 18、Vite、TypeScript、Framer Motion、Lucide Icons
* **容器化**：Docker Compose 多容器編排（FastAPI + Vite 建置的靜態前端 + Nginx），Nginx 統一反向代理於單一埠（預設 6464）。

---

## 模組邊界

- `frontend/src/App.tsx`：公開頁與管理頁的路由入口；頁面放在 `pages/`。
- `frontend/src/components/`：下拉選單、品項客製彈窗、聊天預覽、團長憑證彈窗各自管理互動狀態。共享購物車由頁面管理，元件透過 callback 回報更新。
- `frontend/src/types.ts` 與 `lib/`：共用資料契約、規格計算與團購摘要等純函式；不反向依賴頁面或 UI 元件。
- `frontend/src/styles.css`：明列樣式載入順序；各功能規則放在 `styles/`，避免在入口追加覆蓋。基礎樣式、功能樣式與既有響應式規則的先後關係由回歸測試保護。
- `app/nidin/client.py`：HTTP 呼叫與送單流程；`normalizer.py`：上游資料正規化；`builder.py`：購物車驗證與送單 payload 建立。既有 `from app.nidin import ...` 入口保留。
- `app/ocard/`：Ocard 的 HTTP 呼叫（`client.py`）與網址、菜單、跟團清單轉換（`normalizer.py`）。
- `app/agent_tools.py`：外部 agent 能做的事（權限檢查在這裡）；`app/mcp_server.py` 把它們包成 MCP 工具，`app/api_keys.py` 管 key。
- `app/platforms.py`：送單、同步、取消在各平台實際怎麼做；`routers/live_orders.py` 只放本站自己的規則（防重送、取消憑證、節流、對帳），依團的 `provider` 找對應平台。

---

## 🚀 快速啟動

### 方式一：Docker Compose（推薦）

```bash
# 首次部署：複製設定，填入自己的 GEMINI_API_KEY
cp .env.example .env

# 依序建置，完成後再啟動服務
docker compose build nidin-menu-parser
docker compose build frontend
docker compose up -d

# 查看日誌
docker compose logs -f
```

Compose 使用多階段正式映像：後端不啟用 reload，前端由 pnpm 建置後交給 Nginx 提供靜態檔案，啟動時不安裝套件。容器未安裝 `agy`，AI 後備路徑需設定 Gemini Key。

啟動後請訪問：
* **一般點餐介面**：`http://localhost:6464/`
* **團長管理後台**：`http://localhost:6464/admin`

---

### 方式二：本地開發模式 (Host)

#### 1. 啟動後端
```bash
# 建立虛擬環境或使用本機 Python 3.12
pip install -r requirements.txt

# 啟動 FastAPI 服務
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

#### 2. 啟動前端
```bash
cd frontend
pnpm install
pnpm dev --host 0.0.0.0 --port 5173
```

---

## 🔑 如何取得你訂 (Nidin) 團長憑證 (Token)

若希望系統能自動從你訂官方抓取跟團同事的名單，只需在後台綁定一次 Token。目前支援兩種方式：

### 方式一：瀏覽器擴充功能自動配對（推薦）
本專案提供專用的 Manifest V3 瀏覽器擴充功能（位於 `extension/`）：
1. 依照 [`extension/README.md`](extension/README.md) 指引，在 Chrome / Edge 載入未封裝項目。
2. 在後台點擊 **「設定團長憑證」** ➜ **「自動連結 (推薦)」** ➜ **「連結 Nidin 帳號」**。
3. 系統將自動開啟專用官方分頁，請在該分頁完成 LINE 或 Google 登入。
4. 憑證將安全自動回傳本站，主揪核對遮罩資訊後按「確認綁定」即可完成。

### 方式二：手動複製貼上或書籤提取（備援）
1. 在電腦瀏覽器打開 [你訂官網](https://order.nidin.shop/) 並以 LINE 或 Google 登入。
2. 按鍵盤 `F12` 打開「開發者工具」，切換至 **「Application (應用程式)」** 標籤頁。
3. 在左側欄展開 **「Local Storage (本機存放區)」** ➜ 點選 `https://order.nidin.shop`。
4. 找到以下兩項數值並複製：
   * `Nidin_token`（例如：`eyJhbGciOiJIUzI1...`）
   * `Nidin_userId`（例如：`12345678`）
5. 打開本地後台 `http://localhost:6464/admin`，點擊右上方 **「設定團長憑證」**，貼上儲存即可。

---

## 🧪 測試執行

容器內測試使用獨立暫存目錄與測試資料庫，不覆寫執行中的團購資料。先完成上方啟動步驟：

```bash
# 建置須依序完成；tester 映像與正式服務分離
docker compose --profile test build backend-test
docker compose --profile test run --rm --no-deps -T backend-test
docker compose --profile test build frontend-test
docker compose --profile test run --rm --no-deps -T frontend-test
```

以上是自動化回歸測試，不代表持真實團長憑證的官方送單流程或多人負載已驗證。

## 菜單資料與備份

- `data/menus/` 是本機跨店驗證用快照，已排除於 Git；一般啟動不需要這批檔案。
- 開團及載入團購菜單時向 Nidin 取最新資料，成功後存入 SQLite。部署／重啟本身不會批次抓取測試菜單。
- `app/data/cart.db` 保存團購、購物車、菜單快照與團長憑證，由目前 `app/` bind mount 保留；換機需另行備份還原。
- 目前 API 失敗不會自動回退 SQLite 菜單；顯示上次菜單及抓取時間仍為 Draft 待辦。

可在容器內產生一致的 SQLite 備份；備份含團長憑證，與資料庫同樣排除於 Git：

```bash
docker compose exec -T nidin-menu-parser python - <<'PYBACKUP'
import sqlite3
from datetime import datetime, timezone

from app.cart_store import DB_PATH

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
target = DB_PATH.with_name(f"cart-backup-{stamp}.db")
with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as source:
    with sqlite3.connect(target) as backup:
        source.backup(backup)
print(target)
PYBACKUP
```

不要在服務寫入期間只複製 `cart.db` 而漏掉 WAL。正式還原應在隔離環境驗證，再停寫切換；完整還原演練仍列於待辦。

若需要額外抓取跨店樣本，可執行以下選用流程（會呼叫 Nidin，輸出保存在容器暫存目錄，容器重建後不保留）：

```bash
docker compose exec -T -w /tmp/nidin-tests nidin-menu-parser python scripts/fetch_sample_stores.py
docker compose exec -T -w /tmp/nidin-tests -e PYTHONPATH=/app nidin-menu-parser python -m scripts.verify_menu_graph_stores
```

即時抓取會依當時的門市列表選店，不保證取得驗證腳本指定的固定店家。跨店驗證分別列出成功／失敗／跳過；缺少任何必要快照或零案例時回傳非零結束碼。單元測試使用 `tests/fixtures.py` 的最小資料，不依賴這批快照。

---

## 📚 相關文件
* 給外部 agent 的 MCP 介面：[docs/specs/2026-10-05-agent-mcp.md](docs/specs/2026-10-05-agent-mcp.md)
* Ocard 揪團介接（實測紀錄與決定）：[docs/specs/2026-10-01-ocard-group-ordering.md](docs/specs/2026-10-01-ocard-group-ordering.md)
* 核心規格與 API 定義：[docs/specs/2026-09-17-agent-order-and-official-sync.md](docs/specs/2026-09-17-agent-order-and-official-sync.md)
* 官方憑證自動配對規格：[docs/specs/2026-09-21-nidin-extension-pairing.md](docs/specs/2026-09-21-nidin-extension-pairing.md)
* 憑證取得問題整理：[docs/specs/2026-09-21-nidin-credential-acquisition.md](docs/specs/2026-09-21-nidin-credential-acquisition.md)
* 原始開發計劃：[docs/plans/2026-03-09-nidin-menu-parser.md](docs/plans/2026-03-09-nidin-menu-parser.md)
* 部署、憑證與可靠性待辦（Draft）：[docs/plans/2026-09-17-deployment-readiness.md](docs/plans/2026-09-17-deployment-readiness.md)
