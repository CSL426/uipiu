---
name: group-order
description: 用對話操作飲料團購：開團（Nidin 或 Ocard）、找最近或評分高的分店、查今日停賣、看或改取餐方式、幫人點單、撤回或改單、看誰點了什麼、標記收款、移除過期的團。使用者說「開一團○○」「幫我開○○」「○○有哪家」「幫我點一杯」「誰點了什麼」「改單」「取消這筆」「停賣」「團還開著嗎」時都用這個 skill，即使沒提到 Nidin 或 Ocard。
---

# 飲料團購

這個 repo 是團購站（6464，FastAPI + nginx），團開在 Nidin 或 Ocard 上。平台才是真相，本站負責替大家點單和把狀態同步回來。

所有操作走容器內的指令，輸出 JSON，失敗時 exit 1 並回 `{"error": …}`。不要自己寫程式進容器呼叫內部函式：這些指令已經接好權限、防重送和隱私過濾。

```bash
docker compose exec -T nidin-menu-parser python -m app.cli <指令>
```

| 指令 | 用途 | 會動到真實資料？ |
|---|---|---|
| `groups` | 本站的團（`group_id` 從這裡來） | 否 |
| `find-stores <品牌> [--sort distance\|rating]` | Nidin 分店：距離、評分、可否接單 | 否 |
| `disabled <store_id>` | Nidin 分店今天停賣什麼 | 否 |
| `status <group_id>` | 平台上還開著嗎、取餐方式、有沒有設地址 | 否 |
| `menu <group_id> [--search 關鍵字]` | 品項與規格 | 否 |
| `orders <group_id>` | 同步後列出誰點了什麼、金額 | 只更新本站 |
| `open <store_id>` | 用主揪帳號在 Nidin 開新團並加進本站 | **是** |
| `import <連結> [--name 名稱]` | 把現成的 Nidin／Ocard 揪團連結加進本站 | 只更新本站 |
| `order <group_id> --name 名字 --item '{"item_id":"…","quantity":1,"option_ids":["…"]}'` | 送出跟團單，`--item` 可重複 | **是，真的下單** |
| `cancel <group_id> <order_id>` | 撤回一張跟團單 | **是** |
| `paid <group_id> <名字> [--unpaid]` | 標記收款 | 只更新本站 |
| `remove <group_id>` | 從本站移除，平台上的團不受影響 | 只更新本站 |

`docker compose` 要在 repo 根目錄執行。改了 `app/` 的程式要重建映像才會生效（`docker compose build nidin-menu-parser` 建完再 `up -d`，接著 `restart nginx`，否則 nginx 拿舊 IP 回 502）。資料和憑證放在 `app/data` 的 bind mount，重建不會掉。

## 開團

**1. 先判斷平台。** 跑 `find-stores <品牌>`。

- 找到 → Nidin，走第 2 步。
- 回「找不到這個品牌」→ 不代表沒有這家店，上網搜尋 `<品牌> order.ocard.co` 找 Ocard 頁面（季緣就是這樣，品牌代號 `chiyuan`）。連結是 `order.ocard.co/<品牌>/<分店>`，走下面的「Ocard 開團」。
- 兩邊都沒有 → 告訴使用者，有些店用 iCHEF 等本站接不了的系統。

**2. Nidin：挑分店。** 使用者沒指定就同時看 `--sort distance` 和 `--sort rating`，再對前幾名跑 `disabled`。距離、評分、評價數和今日停賣一起考慮：曾有分店當天那堤全部停賣，開在那裡整團都受影響；也曾到團長結帳才發現兩杯停賣。若最佳選項不明顯，列出 2～3 家讓使用者選；很明顯就直接開，並說明理由。

**3. Nidin：開團並確認。** `open <store_id>` 後看回應的 `pickup` 與 `pickup_note`，再跑 `status` 讀回平台上的實際狀態，不要只相信回應。開團不會下單，空團可以留著。

**Ocard 開團**：本站沒有使用者的 Ocard 帳號，只能用使用者的 Chrome 操作，流程見 [references/ocard-browser.md](references/ocard-browser.md)。

開完一定給使用者：Nidin／Ocard 揪團連結、外送門檻或運費規則、取餐方式現況、平台公告裡會影響下單的事。

## 點單與改單

下單是真的訂單。使用者明確說了品項和規格才送；沒說清楚就先用 `menu` 查，問一次。送出前先 `disabled` 或看 `status`，避免點到停賣品項。

名字會顯示在平台上，也是收款依據，所以要用真名，不能用「訪客」。逾時或不確定有沒有成功時，帶**同一個** `--idempotency-key` 重送，不要換一個重試，否則可能下兩次。

**平台不支援修改跟團單**，「改單」就是 `cancel` 再 `order`。先跟使用者說清楚會這樣做。Ocard 只能撤回從本站送出的單；團長自己或別人直接在 Ocard 點的，要到 Ocard 處理。Nidin 團長自己的飲料（單號 `head:…`）要到 Nidin 改。

## 取餐方式

Nidin 代碼：1 自取、2 內用、3 外送、4 外帶。開團時會自動改成外送到主揪在 Nidin 的預設地址；`pickup_note` 說明結果。Nidin 還沒有這家店到該地址的距離紀錄時會維持店家預設（多半是自取），請使用者到 Nidin 選一次地址。

要改已開的團，請使用者到平台上改，或在瀏覽器操作。原因：這支更新 API 帶錯欄位會關團、也可能蓋掉使用者設好的預約時間，所以 CLI 刻意沒有提供。

## 不能做的事，以及為什麼

- **不代填密碼、驗證碼、付款資料**：登入和結帳由使用者本人完成，結帳本來就是團長的事。
- **不呼叫 Nidin 的 `saveAddressDistance`**：那是所有使用者共用的距離資料，不能放估的數字。
- **不用 Nidin 網頁裡的 Google Maps key**：那是別人的憑證。
- **不把團長外送地點寫進回覆、repo 或 log**：Ocard 的團資料裡有經緯度，所有跟團者都拿得到，本站刻意不存。
- **repo 是公開的**：測試和文件不放真實團碼、姓名、地址。
