"""Ocard 線上點（order.ocard.co）的未公開 API：讀菜單、讀揪團、以訪客身分跟團。

端點全是 POST form（qs 格式，陣列寫成 adds[0][id]）。成功與否看 body 的 code：500＝成功，
不是 HTTP 狀態碼；其他代碼帶 msg。介面只從官網前端與 2026-10-01 實測推得，詳見
docs/specs/2026-10-01-ocard-group-ordering.md。
"""

import asyncio
import os
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlencode

import httpx

from app.ocard.normalizer import OcardMenu, OcardTarget, normalize_menu

OCARD_API_BASE_URL = "https://api-order.ocard.co"
OCARD_SUCCESS = 500
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
# Ocard 菜單沒有一次給規格，要逐項呼叫 menu/item；同時打太多會像攻擊，也容易被擋
ITEM_FETCH_CONCURRENCY = 4
MENU_CACHE_SECONDS = 300.0

_menu_cache: dict[tuple[str, str, str], tuple[float, OcardMenu]] = {}


class OcardApiError(Exception):
    """Ocard 回了非成功的 code，訊息可直接給使用者看。"""

    def __init__(self, message: str, code: Any = None) -> None:
        super().__init__(message)
        self.code = code


def clear_menu_cache() -> None:
    _menu_cache.clear()


def _flatten(data: dict[str, Any]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, list):
            for index, entry in enumerate(value):
                for sub_key, sub_value in entry.items():
                    pairs.append((f"{key}[{index}][{sub_key}]", str(sub_value)))
        else:
            pairs.append((key, str(value)))
    return pairs


class OcardClient:
    def __init__(self, base_url: str | None = None, client: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url or os.getenv("OCARD_API_BASE_URL", OCARD_API_BASE_URL)
        self._client = client

    @asynccontextmanager
    async def _get_client(self):
        if self._client is not None:
            yield self._client
            return
        async with httpx.AsyncClient(timeout=10.0) as client:
            yield client

    async def _post(
        self, path: str, data: dict[str, Any], *, dtk: str | None = None, client: httpx.AsyncClient | None = None
    ) -> dict[str, Any]:
        payload = dict(data)
        if dtk:
            payload["dtk"] = dtk
        headers = {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/x-www-form-urlencoded",
            "origin": "https://order.ocard.co",
            "referer": "https://order.ocard.co/",
            "user-agent": USER_AGENT,
        }

        async def send(http: httpx.AsyncClient) -> httpx.Response:
            return await http.post(f"{self.base_url}/{path}", content=urlencode(_flatten(payload)), headers=headers)

        if client is not None:
            response = await send(client)
        else:
            async with self._get_client() as http:
                response = await send(http)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or body.get("code") != OCARD_SUCCESS:
            code = body.get("code") if isinstance(body, dict) else None
            message = body.get("msg") if isinstance(body, dict) else None
            raise OcardApiError(str(message or f"Ocard 回應異常（code {code}）"), code)
        data_out = body.get("data")
        return data_out if isinstance(data_out, dict) else {}

    async def register_device(self, target: OcardTarget) -> str:
        """每張跟團單用一個新裝置：權杖之後拿來取消這張單，不和別人的單共用。"""
        data = await self._post("login/registerDevice", {
            "group": target.group_code,
            "brand_id": target.brand_id,
            "device_name": "Chrome",
            "os": "Linux",
            "fingerprint": secrets.token_hex(16),
        })
        dtk = str(data.get("dtk") or "")
        if not dtk:
            raise OcardApiError("Ocard 沒有發裝置權杖。")
        return dtk

    async def fetch_group(self, target: OcardTarget, *, dtk: str | None = None) -> tuple[dict[str, Any], str]:
        """不必加入就能讀整個團（含每張跟團單）。回傳 (group, 店名)。"""
        dtk = dtk or await self.register_device(target)
        data = await self._post("store/get", {
            "group": target.group_code,
            "group_id": target.group_code,
            "brand_id": target.brand_id,
            "store_id": target.store_id,
        }, dtk=dtk)
        group = data.get("group")
        if not isinstance(group, dict) or str(group.get("id") or "") != target.group_code:
            raise OcardApiError("Ocard 上找不到這個揪團，可能已經取消或結束。")
        store_name = " ".join(part for part in (data.get("name"), data.get("branch")) if part)
        return group, store_name

    async def fetch_menu(self, target: OcardTarget, *, order_type: str = "DELIVERY") -> OcardMenu:
        key = (target.brand_id, target.store_id, order_type)
        cached = _menu_cache.get(key)
        if cached is not None and time.monotonic() - cached[0] < MENU_CACHE_SECONDS:
            return cached[1]

        dtk = await self.register_device(target)
        async with self._get_client() as http:
            group, store_name = await self.fetch_group(target, dtk=dtk)
            data = await self._post("menu/get", {
                "group": target.group_code,
                "store_id": target.store_id,
                "orderType": group.get("orderType") or order_type,
            }, dtk=dtk, client=http)
            products = data.get("products") or []
            menu_ids = [
                str(item["id"]) for category in products for item in category.get("items") or []
                if item.get("id") and not item.get("soldOut")
            ]
            limit = asyncio.Semaphore(ITEM_FETCH_CONCURRENCY)

            async def fetch_item(menu_id: str) -> tuple[str, dict[str, Any]]:
                async with limit:
                    return menu_id, await self._post("menu/item", {
                        "group": target.group_code,
                        "brand_id": target.brand_id,
                        "store_id": target.store_id,
                        "item_id": menu_id,
                    }, dtk=dtk, client=http)

            details = dict(await asyncio.gather(*(fetch_item(menu_id) for menu_id in menu_ids)))
        menu = normalize_menu(target, store_name, products, details)
        _menu_cache[key] = (time.monotonic(), menu)
        return menu

    async def join_group(self, target: OcardTarget, *, dtk: str, name: str) -> str:
        data = await self._post("group/join", {
            "group": target.group_code,
            "brand_id": target.brand_id,
            "store_id": target.store_id,
            "group_id": target.group_code,
            "name": name,
        }, dtk=dtk)
        order_id = str((data.get("order") or {}).get("id") or "")
        if not order_id:
            raise OcardApiError("Ocard 沒有回傳跟團單號。")
        return order_id

    async def add_item(
        self,
        target: OcardTarget,
        *,
        dtk: str,
        order_id: str,
        order_type: str,
        item_id: str,
        count: int,
        option_ids: list[str],
    ) -> dict[str, Any]:
        return await self._post("cart/addItem", {
            "group": target.group_code,
            "brand_id": target.brand_id,
            "store_id": target.store_id,
            "orderType": order_type,
            "item_id": item_id,
            "count": count,
            "adds": [{"id": option_id, "count": 1} for option_id in option_ids],
            "order_id": order_id,
        }, dtk=dtk)

    async def leave_group(self, target: OcardTarget, *, dtk: str, order_id: str) -> None:
        """跟團者退出＝移除這張跟團單的所有品項。"""
        await self._post("group/leave", {"group": target.group_code, "order_id": order_id}, dtk=dtk)
