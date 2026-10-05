"""給外部 agent 用的 MCP（streamable HTTP），掛在 /mcp。工具實作在 app/agent_tools.py。

- 認證：每個請求帶 `Authorization: Bearer <API key>`，在進到 MCP 之前就驗；無效回 401。
- stateless＋JSON 回應：每個請求各自驗 key，不用 session id，也不會有「session 被別把 key 接手」的問題。
- 關掉 SDK 的 Host 檢查：它預設只放行 localhost，經 nginx／ngrok 的網域會被擋成 421；
  每個請求都要有效的 key，DNS rebinding 拿不到 key 也做不了事。
- SDK 的 session manager 一個實例只能 run() 一次，而測試會重複啟動 app；所以每次 lifespan 都建新的。
"""

from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError as McpToolError
from mcp.server.streamable_http_manager import (
    StreamableHTTPASGIApp,
    StreamableHTTPSessionManager,
)
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from app import agent_tools, api_keys
from app.api_keys import ApiKeyPrincipal

INSTRUCTIONS = (
    "飲料團購點單。先 list_groups 找團，get_menu 看品項與規格，再 place_order。"
    "送出的是真的訂單：名字就是平台上這張單的名字；同一筆重送請帶相同的 idempotency_key。"
)


class OrderItem(BaseModel):
    item_id: str = Field(description="get_menu 回傳的 item_id")
    quantity: int = Field(default=1, ge=1, le=20)
    option_ids: list[str] = Field(default_factory=list, description="各規格群組所選選項的 option_id")


def _principal(ctx: Context) -> ApiKeyPrincipal:
    return ctx.request_context.request.state.api_key


async def _run(call: Awaitable[dict[str, Any]]) -> dict[str, Any]:
    """agent_tools 的錯誤是要給 agent 看的；SDK 只會把它自己的 ToolError 訊息轉給呼叫端，其餘一律藏起來。"""
    try:
        return await call
    except agent_tools.ToolError as exc:
        raise McpToolError(str(exc)) from exc


def build_server() -> MCPServer:
    server = MCPServer("mynig-group-order", instructions=INSTRUCTIONS)

    @server.tool()
    async def list_groups(ctx: Context) -> dict[str, Any]:
        """列出目前開著的團：平台（Nidin／Ocard）、名稱、已點筆數與金額、外送門檻。"""
        return await _run(agent_tools.list_groups(_principal(ctx)))

    @server.tool()
    async def get_menu(group_id: str, ctx: Context, search: str = "") -> dict[str, Any]:
        """取得團的菜單。items 列出品項與它用到的 spec_group_ids；spec_groups 列出每組規格要選幾個（min～max）
        與選項。search 可用品名關鍵字縮小範圍。"""
        return await _run(agent_tools.get_menu(_principal(ctx), group_id, search))

    @server.tool()
    async def place_order(
        group_id: str,
        name: str,
        items: list[OrderItem],
        ctx: Context,
        phone: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        """送出一張跟團單（真的會下單）。name 是點單人的名字，會顯示在平台上；phone 選填，只有 Nidin 會用。
        逾時或不確定有沒有成功時，用同一個 idempotency_key 重送，不會重複下單。"""
        return await _run(agent_tools.place_order(
            _principal(ctx),
            group_id,
            name,
            [item.model_dump() for item in items],
            phone=phone,
            idempotency_key=idempotency_key,
        ))

    @server.tool()
    async def list_orders(group_id: str, ctx: Context) -> dict[str, Any]:
        """先和平台同步，再列出每個人點了什麼與金額。主揪 key 看自己的團時會多一個 paid（是否已收款）。"""
        return await _run(agent_tools.list_orders(_principal(ctx), group_id))

    @server.tool()
    async def cancel_order(group_id: str, order_id: str, ctx: Context) -> dict[str, Any]:
        """撤回一張跟團單。點單 key 只能撤回自己送出的；主揪 key 可撤回自己團裡從本站送出的任何一筆。"""
        return await _run(agent_tools.cancel_order(_principal(ctx), group_id, order_id))

    @server.tool()
    async def open_group(link: str, ctx: Context, name: str = "") -> dict[str, Any]:
        """（主揪 key）用 Nidin 或 Ocard 的揪團連結開一個團。name 不填就用店名。"""
        return await _run(agent_tools.open_group(_principal(ctx), link, name))

    @server.tool()
    async def set_paid(group_id: str, name: str, paid: bool, ctx: Context) -> dict[str, Any]:
        """（主揪 key）標記某人已收款或未收款。"""
        return await _run(agent_tools.set_paid(_principal(ctx), group_id, name, paid))

    return server


class McpEndpoint:
    """/mcp 的 ASGI 端點：先驗 key，再交給目前這次 lifespan 建立的 session manager。"""

    def __init__(self) -> None:
        self.manager: StreamableHTTPSessionManager | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = dict(scope.get("headers") or [])
        key = api_keys.bearer_key(headers.get(b"authorization", b"").decode("latin-1"))
        principal = api_keys.authenticate(key) if key else None
        if principal is None:
            response = JSONResponse(
                {"error": "invalid_token", "detail": "需要有效的 API key（Authorization: Bearer …）。"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        if self.manager is None:
            await JSONResponse({"detail": "MCP 尚未啟動。"}, status_code=503)(scope, receive, send)
            return
        scope.setdefault("state", {})["api_key"] = principal
        await StreamableHTTPASGIApp(self.manager)(scope, receive, send)

    @asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        server = build_server()
        server.streamable_http_app(
            stateless_http=True,
            json_response=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        self.manager = server.session_manager
        try:
            async with self.manager.run():
                yield
        finally:
            self.manager = None


endpoint = McpEndpoint()
