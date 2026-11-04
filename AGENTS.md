# 你訂／Ocard 飲料團購站

團開在 Nidin 或 Ocard 上，本站（FastAPI + React，對外 6464）替大家點單並同步回來。
平台上的團才是真相。規格在 `docs/specs/`，動架構前先讀。

## 使用者要開團、點飲料、查誰點了什麼

**讀 `.claude/skills/group-order/SKILL.md` 照著做。** 所有操作都走容器內的指令，輸出 JSON：

```bash
docker compose exec -T nidin-menu-parser python -m app.cli <指令>
```

這是對真實帳號、真實訂單的操作，不是測試環境：
- `order` 真的會下單；`open` 真的會在主揪的 Nidin 帳號開團。
- 登入和結帳由使用者本人做，不代填密碼、驗證碼、付款資料。
- 回覆裡不寫團長的外送地址。這個 repo 是公開的，測試和文件不放真實團碼、姓名、地址。

別的專案或機器上的 agent，改用 `/mcp`（要主揪發 API key，見 `docs/specs/2026-10-05-agent-mcp.md`）。

## 改程式

- 測試跑容器：`docker compose --profile test build backend-test && docker compose --profile test run --rm backend-test`。
  主機的 Python 套件版本對不上（mcp 是舊版），直接跑 pytest 會失敗。
- 前端用 pnpm：`docker compose --profile test build frontend-test && docker compose --profile test run --rm frontend-test`。
- 部署一次建一個映像，建完再 `docker compose up -d <服務>`。資料和憑證在 `app/data`（bind mount），重建不會掉。
- 對外只有 `frontend` 容器（內含 nginx，設定在 `frontend/docker/nginx.conf`），沒有獨立的 nginx 服務。

## 功能要更新的地方

一個功能要檢查這些出口，不需要改的要明說：

- 管理頁與公開頁（`frontend/src/pages/`）、聊天框（`components/ChatWidget.tsx`）
- REST（`app/routers/`）、MCP 工具（`app/mcp_server.py` 與 `app/agent_tools.py`）
- 指令（`app/cli.py`）與 skill（`.claude/skills/group-order/`）
- `README.md` 與 `docs/specs/`

平台行為差異放 `app/platforms.py`；防重送、取消憑證、節流、對帳放 `app/routers/live_orders.py`，兩個平台共用。

`.agents/skills/group-order` 是指向 `.claude/skills/group-order` 的連結，給 Codex 和 Antigravity 讀，只改 `.claude/` 那份。
