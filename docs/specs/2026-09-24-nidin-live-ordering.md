# 點單即送 Nidin（Nidin 為真相）

- 狀態：Draft
- 日期：2026-09-24
- 相關：`2026-09-21-nidin-credential-acquisition.md`（憑證、開團、同步欄位）

## 問題

本站購物車原本是自己一份：聊天框「確認加入」只寫本站；公開頁「送出訂單」雖然會以訪客身分送進 Nidin，
之後又另寫一份沒有 Nidin 單號的本地資料，同步後同一杯會出現兩次。使用者在本站點了、Nidin App 看不到。

## 決定

1. **一條送單路徑**：公開頁與聊天框都走 `POST /cart-sessions/{id}/orders`。
   - 每次送出＝一張 Nidin 跟團單（訪客身分，名字＝點單人），送成功才寫本站並標上 `official_order_id`。
   - 送單網址取自本站紀錄，不接受前端指定。
   - 名字必填，不接受「訪客」（名字是 Nidin 上的身分，也是收款、取消的依據）；前端記在 localStorage。
2. **防重複下單**：前端每批內容產生 `submission_id`，同一批重按沿用同一個；後端 `order_submissions` 記錄
   `pending / submitted / failed / cancelled`。
   - `submitted` 重送 → 回既有結果，不再下單。
   - 送出後逾時 → 保持 `pending` 並擋下重送，請使用者先重新整理確認（請求可能已在 Nidin 成立）。
   - 送出前就失敗（團已截止、菜單抓不到、驗證不過）→ `failed`，同一個 id 可重試。
3. **送單前檢查**：`group_buying_status` 必須是 10（揪團中）；每一步檢查 Nidin body 的 `status`
   （HTTP 200 帶錯誤碼時視為失敗）。點單規則（必選規格、固定選項自動帶入）在送 Nidin **之前**執行。
4. **取消**：跟團者送出後 Nidin 不讓自己改單，只能團長刪單。`DELETE /cart-sessions/{id}/orders/{order_id}`
   由本站用**主揪**的 Nidin 憑證呼叫 `DELETE /carts/{團長單號}/subOrder/{跟團單號}`。
   授權：點單本人（送單時拿到的 `cancel_token`，由伺服器金鑰 HMAC 衍生、只存在點單人的瀏覽器）或主揪。
   團長單號取自 `/groupBuy/{code}/info`（role 1 的 `group_buy_list[0].order_id`），快取在 `sessions.nidin_order_id`。
5. **顯示以 Nidin 為準**：公開頁載入、送單或取消後、分頁回到前景、每 20 秒呼叫
   `POST /cart-sessions/{id}/refresh`；後端同一團 10 秒內只真的同步一次（單一行程內的節流）。
6. **同步**：不再清除「已收款」；規格名稱對回菜單選項 id 與加價（對不到才留 `opt` 佔位）；
   `line_id` 由「團購＋Nidin 單號＋第幾杯」固定算出；時間用 Nidin 的 `create_time`（原為台灣時間，轉成 UTC 儲存）。
7. **關閉繞過 Nidin 的寫入**：`POST /lines`、agent-order 非預覽模式限主揪；`/guest/order-once` 需登入。

## 審查後修正（2026-09-24，對抗式審查確認 28 項）

- **結果不明只限最後一步**：`order_guest_cart_session` 回報 `progress.stage`；進入 commit（productCouponFlow）
  之前的任何失敗一律 `failed` 可重試。commit 後只有 ConnectError／ConnectTimeout／PoolTimeout、Nidin 明確拒絕、4xx
  確定沒成立；ReadTimeout、5xx、回應解析失敗視為「結果不明」保持 `pending`，並記下跟團購物車單號。
- **對帳**：每次同步後用 Nidin 跟團單比對 `pending`：找到→`submitted`（同一 id 重送可取回取消憑證）；
  超過 60 秒仍找不到→`failed`（可安全重試）。前端「重新整理」按鈕會觸發 Nidin 同步。
- **送單編號綁定內容**：後端存 `request_hash`（團＋名字＋品項）；同一 id 換了內容且非 failed → 409。
  前端暫存的 id 納入團與名字；聊天卡的 id 納入內容指紋，改了杯數或規格就是新的一筆。
- **同步不會清空**：`/groupBuy/{code}/info` 回 HTTP 200 但 body status 異常或沒有跟團清單時丟錯，保留畫面上的內容。
- **收款**：`payments.paid_amount` 記錄標記當下的應付金額；應付超過它就不算已收清。不再以刪除收款紀錄處理新單，
  別人冒名下單再取消也不會清掉你的收款狀態。
- **自動重登**：失敗後 30 分鐘內不再嘗試（不再刪除記住的密碼）；主揪重新綁定即解除。
- **公開送單限流**：同一 IP 10 分鐘 60 筆。取消已取消的單不再打 Nidin；非 ASCII 取消憑證回 403 而非 500；
  agent-order 非預覽模式加 CSRF 檢查；舊 submit-guest-order 只送沒有 Nidin 單號的本地品項。
- **前端**：換團後回來的結果不套用；送單／取消後，比它早發出的刷新作廢；5xx 無說明或連線中斷時提示
  「可能已經成立」而非「沒有送出」；localStorage 被封鎖時取消憑證留在記憶體；聊天送單用確認當下的名字。
- **同步細節**：規格對回菜單時先比群組名稱（同名選項如「L」）；Nidin 時間轉成 UTC 儲存以免字串排序錯亂。

## 未解

- 改版前留下、沒有 Nidin 單號的本地品項不會被同步取代（需人工清理或確認）。2026-09-30 已清掉
  「測試開的團」裡唯一一筆（訪客／春烏龍）；其他舊團購（3 月、9/17）若再同步仍可能重複計算。
- 同名跟團者：取消靠單號與憑證，不受影響；但收款標記仍以名字為 key。
- 節流、限流、重登冷卻都只在單一行程內；若後端改多 worker 需改用共享儲存。
- 「團長刪單」尚未以真實 Nidin 驗證；訪客不帶電話註冊（新做法）也尚未以真實 Nidin 驗證。

## 2026-09-30 決定

- **訪客電話選填**：公開頁新增「電話（選填）」欄（存 localStorage），格式需為 09 開頭 10 碼。
  沒填就不送：訪客註冊不帶 `phone`、跟團送單的 `contact_method_remark` 為空字串——與 Nidin 官網
  （官網的訪客註冊只在有填且格式正確時帶 phone，跟團送單沒填聯絡方式時送空字串）一致。
  不再亂數產生號碼（可能是陌生人的真實號碼，而且會顯示給團長）。
- **後台代刪**：明細分頁每張 Nidin 跟團單的第一杯有「刪除這筆」，需再按「確定刪除？」才執行
  （`DELETE /cart-sessions/{id}/orders/{order_id}`，主揪身分）。團長自己的飲料不提供。

## 已實測

- 2026-09-30：公開頁送出一杯（小華／春烏龍）成功。本站 ledger `submitted`、official_order_id 300000004；
  Nidin `/groupBuy/{share_code}/info` 跟團清單出現同一個 order_id、contact_name、規格（大杯／微糖／微冰）與金額 35。
  證實送單回傳的 order_id 等於跟團清單的 order_id，同步後本站那筆被同單號取代、沒有重複。
