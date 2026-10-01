"""FastAPI 應用進入點：只做 app 宣告與路由掛載。

端點實作在 app/routers/，跨 router 的共用相依在 app/dependencies.py
與 app/deps_shared.py。
"""

from fastapi import FastAPI

from app import auth as account_auth
from app import dependencies
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

app = FastAPI(title="Nidin Menu Parser")

account_auth.configure_auth(lambda: dependencies.get_cart_store()._db_path)

app.include_router(account_auth.router)
app.include_router(stores.router)
app.include_router(cart_sessions.router)
app.include_router(guest_orders.router)
app.include_router(leader_auth.router)
app.include_router(nidin_group_buys.router)
app.include_router(live_orders.router)
app.include_router(nidin_pairing.router)
app.include_router(agent_order.router)
app.include_router(tts.router)
