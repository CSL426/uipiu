# 擴充功能配對規格 — 實作審查補充

狀態：Draft
日期：2026-09-21（2026-09-22 補記修復結果）
審查對象：[2026-09-21-nidin-extension-pairing.md](2026-09-21-nidin-extension-pairing.md) 及已落地的實作

---

## 先說結論

規格本身品質高於我原先那份分析，威脅模型、revision 防覆蓋、原子性要求都比我想得完整，
也正確修正了我兩處過度推論（§2：不能由「本地沒有 OAuth 程式碼」推論官方沒有第三方授權；
`app/nidin.py` 路徑已過期）。這份補充只記**實作與規格不符之處**，以及規格自己漏掉的洞。

實作進度（非規格描述的「待實作」）：後端 `app/nidin_pairing.py`(229 行)、
`app/routers/nidin_pairing.py`(130 行)、`extension/`(manifest + background + content + 測試)
都已存在，`tests/test_nidin_pairing.py` 4 項通過。

---

## 一、確認的 Bug：revision 防覆蓋實際上擋不住併發（高）

規格 §7 要求「驗證配對、檢查 revision、更新儲存、增加 revision、標記 consumed
必須在同一 SQLite transaction」，§11 要求「併發確認只寫入一次」。

**實作沒有達到這點，我用測試證明了。**

### 成因

`app/nidin_pairing.py:confirm_pairing()` 用 `auth_connection()`，而
`app/auth.py:_connection()` 只在**建立連線時**為了 bootstrap 跑過一次
`BEGIN IMMEDIATE`（line 163），那個交易在 caller 拿到 conn 前就已 commit。
之後 caller 的工作是 SQLite **預設的 deferred 模式** —— 第一個 SELECT 開的是
讀取交易，中間別的 writer 可以插進來 commit。

於是 `confirm_pairing` 的
「讀 revision → 比對 initial_revision → 寫 credentials → 寫 revision → 標 consumed」
不是一個不可分割的寫入交易。

### 實測

在 confirm 讀完 revision 之後、寫入之前，用另一條連線 `BEGIN IMMEDIATE` 寫入
`revision=99` 並 commit：

```
=== 競爭者狀態: ['competitor-committed']
=== confirm 結果: ('***', 'u1') 錯誤: None
=== 最終 revision: 1  flow: consumed
```

競爭者的 `revision=99` 被 confirm 覆寫成 `1`，flow 照樣 consumed，
**沒有任何 409 衝突**。這正是 revision 機制要防的 lost update。

（重現腳本在 scratchpad，未提交；要的話我可以整理成正式測試。）

### 建議修法

1. `confirm_pairing` 開頭顯式 `conn.execute("BEGIN IMMEDIATE")`，
   讓它從一開始就取得寫入鎖。`grant_pairing`/`cancel_pairing` 只要有寫入也一樣。
2. 或在 `auth.py` 提供一個 `auth_write_connection()` 明確語意的 helper，
   避免每個呼叫端各自記得。
3. **補一個併發測試**（§11 已要求，目前 4 項測試都沒有覆蓋）。
   `sqlite3.connect` 的 `timeout=15` 只會讓後到者等待，不會讓先到者偵測衝突——
   所以測試要斷言「其中一個拿到 409」，不是「兩個都成功」。

另外注意：`leader_credential_revisions` 用 `ON CONFLICT DO UPDATE SET revision=excluded.revision`
寫入計算好的 `new_rev`。改用 `revision=revision+1` 由資料庫原子遞增會更穩，
不過那會讓「回傳的 revision 要跟寫入一致」變麻煩，取捨看你。

---

## 二、state 比對非 timing-safe（中低）

`confirm_pairing` 等處用 `row["state_hash"] != state_hash` 直接比較字串。
`app/auth.py:148` 已經在用 `hmac.compare_digest`，這裡卻沒有。

風險實際上很低（比的是 SHA-256 hash 不是原始 state，且 flow_id 已先過濾），
但既有程式碼已有正確範例、改動成本是一行，沒有理由不一致。同理 `session_token_hash` 比對。

---

## 三、manifest 的 externally_connectable 比規格寬（中）

規格 §5 明確要求「只列明確的本站部署主機」、「預設開發 origin 為
`http://localhost:6464`、`http://127.0.0.1:6464`」、「service worker 再檢查完整 origin
（scheme、host、port）」。

實際 `extension/manifest.json`：

```json
"externally_connectable": { "matches": ["http://localhost/*", "http://127.0.0.1/*"] }
```

**沒有 port 限制** —— 本機任何 port 的頁面都能連上擴充功能。規格自己預見了這點
（「manifest 匹配不能取代精確 port 驗證」），所以要確認 `background.ts` 真的有做
port 驗證；若有，這是深度防禦的一層缺失而非漏洞，但仍與規格不符。

同時缺正式 HTTPS origin 條目，規格 §5 說「正式設定使用確定的 HTTPS origin，
不接受 LAN IP 的明文 HTTP」——上線前必須補，且 §12 已把它列為待實測。

---

## 四、規格本身可以再補的三點

### 1. Token 輪替 / 失效沒有策略

規格處理了「取得」與「覆蓋」，但沒講 token **過期後**怎麼辦。
§8 只說「後續真正呼叫官方 API 時提示重新連結」。實務上 Nidin token 會過期，
而過期是在**背景同步時**才發現的。建議明確：

- 官方 API 回 401 時，是否自動把該憑證標記為失效？
- 標記後 UI 怎麼提示？（現在會靜默失敗到使用者自己發現）
- 是否要在 `leader_credentials` 加 `last_verified_at` / `invalid_since`？

### 2. `credentialRevision` 的跨裝置語意

§7 說 revision「必須在清除憑證後仍保留且單調增加」，實作也開了獨立表（good）。
但沒說**使用者在 A 裝置發起配對、在 B 裝置手動貼 token** 時前端要怎麼呈現。
現在會是 409「憑證版本已變更，請重新配對」——正確，但錯誤訊息沒告訴使用者
「你剛剛在別的地方更新過」。這是少見但會讓人困惑的路徑。

### 3. 擴充功能未安裝的偵測方式沒定義

§8 有 `extension_missing` 狀態、§3 有「本站檢查擴充功能是否已安裝」，
但沒寫**怎麼檢查**。`chrome.runtime.sendMessage(extensionId, ...)` 在未安裝時
的失敗方式在不同瀏覽器/版本不一致（有時 callback 帶 `lastError`，有時靜默）。
建議規格指定：帶 timeout 的 `HELLO` 握手，逾時即視為未安裝，並寫明 timeout 值。

---

## 五、小事

- §10 說樣式進「擁有該元件的樣式模組」——`frontend/src/styles/` 已經拆成 19 個檔案，
  對應的應該是 `login.css` 或新開一個。順帶提醒：專案慣例避免 `px`，
  規格沒特別說但實作時會踩到（現有 extension 相關 UI 還沒寫）。
- §11「既有前後端全套回歸測試通過；不以文件舊測試數量取代實際收集結果」——
  這句寫得好。目前實測：後端 205（含 pairing 4）、前端 46。
- `app/nidin_pairing.py` 與 `app/routers/nidin_pairing.py` 同名不同層，
  讀 traceback 時容易看錯。規格 §10 就這樣訂的，不改也行，但值得知道。

---

## 修復紀錄（2026-09-22）

第一、二、三點已修復，各自附上會失敗的測試：

| 項目 | 修法 | 測試 |
|---|---|---|
| 交易未上寫入鎖 | `app/auth.py` 新增 `auth_write_connection()`（`BEGIN IMMEDIATE`），`app/nidin_pairing.py` 四處改用 | `test_confirm_holds_write_lock_against_interleaved_update` |
| hash 比對非 timing-safe | 六處改為 `hmac.compare_digest` | 既有測試覆蓋 |
| origin 未比對 port | `isOriginAllowed()` 改用已宣告卻沒被使用的 `ALLOWED_WEB_ORIGINS` 比完整 origin；manifest 白名單補上 port | `rejects localhost on a port that is not on the allow-list` |

**因果都驗證過**：把修復還原 → 測試失敗；裝回去 → 通過。不是寫個永遠會過的測試。

`isOriginAllowed()` 原本宣告了 `ALLOWED_WEB_ORIGINS` 卻只比 hostname，等於白名單形同虛設，
本機任何 port 的頁面都能連上擴充功能。既有測試只用清單上的 port，所以沒抓到。

測試：後端 210、前端 49、擴充功能 4，全綠。後端映像已重建部署。

第四點（規格可補的三項：token 失效策略、跨裝置 revision 語意、未安裝偵測方式）**未動**，
那些要先決定設計再改，尤其 token 失效會影響資料模型。

---

## 建議處理順序

1. **修 transaction（第一點）** —— 唯一確認的 bug，而且是靜默的資料覆蓋
2. 補併發測試（§11 本來就要求）
3. manifest port 限制 + 確認 background.ts 的 origin 驗證
4. `compare_digest` 一致化
5. 規格補 token 失效策略（第四點之一）—— 這個會影響資料模型，愈早定愈好

要我動手就說一聲，我會先從第 1、2 點開始（同一個改動 + 證明它有效的測試）。
