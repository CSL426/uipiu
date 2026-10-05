"""FastAPI 應用進入點：只做 app 宣告與路由掛載。

端點實作在 app/routers/，跨 router 的共用相依在 app/dependencies.py
與 app/deps_shared.py。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

from app import api_keys
from app import auth as account_auth
from app import dependencies
from app.mcp_server import endpoint as mcp_endpoint
from app.routers import (
    agent_order,
    cart_sessions,
    guest_orders,
    leader_auth,
    live_orders,
    nidin_group_buys,
    nidin_pairing,
    stores,
    tts,
)



@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    async with mcp_endpoint.running():
        yield


app = FastAPI(title="Nidin Menu Parser", docs_url=None, lifespan=lifespan)
# 給外部 agent 的 MCP；用 add_route 掛原生 ASGI 端點（mount 會把 /mcp 轉址到 /mcp/）
app.add_route("/mcp", mcp_endpoint, include_in_schema=False)


@app.get("/docs", include_in_schema=False)
def api_docs() -> HTMLResponse:
    # 對外經 nginx 的 /api/ 前綴；預設文件頁去抓 /openapi.json 會被導到前端
    return get_swagger_ui_html(openapi_url="/api/openapi.json", title="Nidin Menu Parser API")

account_auth.configure_auth(lambda: dependencies.get_cart_store()._db_path)

app.include_router(account_auth.router)
app.include_router(api_keys.router)
app.include_router(stores.router)
app.include_router(cart_sessions.router)
app.include_router(guest_orders.router)
app.include_router(leader_auth.router)
app.include_router(nidin_group_buys.router)
app.include_router(live_orders.router)
app.include_router(nidin_pairing.router)
app.include_router(agent_order.router)
app.include_router(tts.router)
