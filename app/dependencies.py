"""共用的應用層物件與存取器。

放在獨立模組是為了讓 routers 不必 import main（會造成循環匯入）。

注意：測試以字串路徑 patch `app.main.cart_store` / `app.main.get_nidin_client`，
所以 main 仍會 re-export 這些名稱，而各 router 一律透過 main 的存取器取用，
確保 patch 能穿透到 router。詳見 app/main.py 的說明。
"""

from app.agent_parser import AgentParser
from app.cart_store import CartStore
from app.nidin import NidinClient
from app.ocard import OcardClient

cart_store = CartStore()
agent_parser = AgentParser()


def get_cart_store() -> CartStore:
    return cart_store


def get_agent_parser() -> AgentParser:
    return agent_parser


def get_nidin_client() -> NidinClient:
    return NidinClient()


def get_ocard_client() -> OcardClient:
    return OcardClient()
