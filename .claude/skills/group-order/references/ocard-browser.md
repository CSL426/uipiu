# Ocard 開團（用使用者的 Chrome）

Ocard 開團要團長本人的帳號，之後才能用那個帳號結帳。未登入也按得到「開始揪團」，但不要在未登入時按。

1. 載入 Claude in Chrome 工具，開新分頁到 `https://order.ocard.co/<品牌>/<分店>`。
2. 確認登入：在頁面執行 `document.querySelector('#app').__vue__.$store.state.user.logged_in`。
   - `false` → 請使用者自己在該分頁登入，說明登入是本人操作。等對方回覆，不要代填。
3. 確認取餐方式：頁面上「外送預約／來店自取」決定 `group/init` 帶的 `orderType`。使用者沒指定時用預設並在回報裡說明。
4. 點「揪團訂購」→「開始揪團」。分享連結在視窗的 `#send-text`，形如 `https://order.ocard.co/<品牌>/<分店>?group=<團碼>`。
   - 如果畫面已是「進行中的揪團」，不要再發起新揪團，也不要碰「取消揪團」。
5. `import <連結>` 加進本站，再 `status <group_id>` 確認。`has_delivery_address: false` 表示還沒選外送地址，提醒使用者到 Ocard 選。
6. 關掉分頁。

## 踩過的坑

- **分頁在背景時**，截圖會逾時、座標點擊沒反應（`document.visibilityState` 是 `hidden`）。用 `javascript_exec` 找元素再 `.click()`，或請使用者把分頁切到前面。
- 網站的確認框是 SweetAlert：按鈕在 `.swal2-popup`。**離開揪團**的確定鍵是 `.swal2-cancel`（文字「離開」），`.swal2-confirm` 是「返回」，跟直覺相反。
- `login/registerDevice` 什麼品牌代號都接受，不能拿來驗證代號存不存在。要驗證請用 `store/get`。
- 同一杯可能出現在多個分類且菜單 id 相同（季緣的「人氣精選」），本站已處理。
- 登出會清掉登入狀態，測試時不要登出使用者的帳號，也不要用它測跟團流程。
