"""HTTP transport and guest-order orchestration."""

import asyncio
import base64
from contextlib import asynccontextmanager
import json
import os
import secrets
from typing import Any

import httpx

from app.models import (
    CartSessionLine,
    ExpectedOrderLine,
    GroupBuyTarget,
    GuestOrderReceipt,
    NormalizedMenu,
    OfficialGroupBuySummary,
    ParsedOrder,
    StoreDeliverySummary,
)
from app.nidin.builder import (
    build_expected_cart_lines,
    build_group_buy_cart_payload,
    build_parsed_orders_from_cart_lines,
    validate_cart_lines,
    validated_order_menu,
)
from app.nidin.normalizer import (
    _extract_product_image_urls,
    _normalize_store_delivery_summary,
    _resolve_guest_phone,
    extract_group_buy_target,
    normalize_menu_from_api,
    normalize_official_group_buy_info,
)
from app.parser import parse_order_lines


NIDIN_API_BASE_URL = "https://loctw-service-api.nidin.shop/shopper/v2"


BROWSER_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/145.0.0.0 Safari/537.36"


# Nidin 前端遇到這些 status 會直接登出（order.nidin.shop 的 app bundle），
# 視為憑證失效；回應格式只從前端程式碼推得，尚未對真實失效回應實測
TOKEN_FAILURE_STATUSES = frozenset({1303, 1305, 1306, 1307})


class NidinLoginError(Exception):
    """Nidin 拒絕登入（帳密錯誤、帳號狀態異常等），訊息可直接給使用者看。"""


class NidinTokenExpired(Exception):
    """帶去的 MC-API-Token 已被 Nidin 作廢或輪替。"""


class NidinApiError(Exception):
    """Nidin 回了 HTTP 200 但 body 的 status 不是 200，訊息可直接給使用者看。"""


# 官網「我的訂單」頁的預設篩選（order/list 頁面 data() 的 status 陣列）。
# 13001／13002／13009 這類值只能放在 special_status，放進 status[] 會回 1101 輸入資訊錯誤
ORDER_HISTORY_STATUSES = (100, 110, 99, 120, 121, 122, 130, 140, 31, 35, 39, 38)


def make_device_id() -> str:
    suffix = secrets.token_urlsafe(12).replace("-", "A").replace("_", "B")[:14]
    return f"NIDINShopperWeb_{suffix}"


def _body_status(response: httpx.Response) -> Any:
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get("status") if isinstance(body, dict) else None


def _raise_if_token_rejected(response: httpx.Response) -> None:
    if response.status_code in (401, 403) or _body_status(response) in TOKEN_FAILURE_STATUSES:
        raise NidinTokenExpired()


def _checked_json(response: httpx.Response) -> dict[str, Any]:
    """寫入類呼叫共用：HTTP 錯誤照丟，HTTP 200 但 body status 不是 200 也當失敗。

    Nidin 常用 HTTP 200 回錯誤（例如 40058 團已截止、40315/40316 菜單版本不符），
    只看 raise_for_status 會把失敗當成功，或在取欄位時變成 KeyError。
    body 沒有 status 欄位時不判定（部分端點成功時不帶 status）。
    """
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise NidinApiError("Nidin 回應格式異常")
    if "status" in body and body["status"] != 200:
        raise NidinApiError(str(body.get("description") or body.get("message") or f"Nidin 錯誤 {body['status']}"))
    return body


def token_expires_at(token: str) -> float | None:
    """讀 Nidin 憑證（JWT）的 exp，只拿來顯示到期日。

    簽章是 Nidin 的密鑰簽的，這裡驗不了也不需要驗：憑證能不能用一律以
    Nidin API 的回應為準。實測（2026-09-24）Nidin 發的憑證 exp 為簽發後 30 天。
    """
    try:
        segment = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
        exp = claims.get("exp")
    except (IndexError, ValueError, AttributeError):
        return None
    return float(exp) if isinstance(exp, (int, float)) else None


class NidinClient:
    def __init__(
        self,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url or os.getenv(
            "NIDIN_MENU_API_BASE_URL",
            NIDIN_API_BASE_URL,
        )
        self._client = client

    @asynccontextmanager
    async def _get_client(self):
        if self._client is not None:
            yield self._client
            return

        async with httpx.AsyncClient(timeout=10.0) as client:
            yield client

    def _headers(self, *, token: str | None = None, user_id: int | str | None = None) -> dict[str, str]:
        headers = {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json",
            "origin": "https://order.nidin.shop",
            "referer": "https://order.nidin.shop/",
            "user-agent": BROWSER_USER_AGENT,
        }
        if token is not None:
            headers["MC-API-Token"] = token
        if user_id is not None:
            headers["MC-API-User"] = str(user_id)
        return headers

    def _make_device_id(self) -> str:
        return make_device_id()

    async def fetch_menu(self, store_id: str) -> NormalizedMenu:
        async with self._get_client() as client:
            menu_response, image_response = await asyncio.gather(
                client.get(f"{self.base_url}/store/{store_id}/onShelfMenu"),
                client.get(f"{self.base_url}/store/{store_id}/productImages"),
                return_exceptions=True,
            )

            if isinstance(menu_response, Exception):
                raise menu_response

            menu_response.raise_for_status()

            product_image_urls: dict[str, str] = {}
            if isinstance(image_response, httpx.Response):
                if image_response.is_success:
                    product_image_urls = _extract_product_image_urls(image_response.json())

            return normalize_menu_from_api(
                menu_response.json(),
                store_id,
                product_image_urls=product_image_urls,
            )

    async def fetch_group_buy_brief(self, group_buy_code: str) -> dict[str, Any]:
        async with self._get_client() as client:
            response = await client.get(f"{self.base_url}/groupBuy/{group_buy_code}/brief")
            response.raise_for_status()
            return response.json()["group_buy_brief"]

    async def fetch_raw_menu(self, store_id: str) -> dict[str, Any]:
        async with self._get_client() as client:
            response = await client.get(f"{self.base_url}/store/{store_id}/onShelfMenu")
            response.raise_for_status()
            return response.json()

    async def fetch_store_delivery_summary(self, store_id: str) -> StoreDeliverySummary:
        async with self._get_client() as client:
            response = await client.get(f"{self.base_url}/store/{store_id}/info")
            response.raise_for_status()
            return _normalize_store_delivery_summary(response.json(), store_id)

    async def register_guest(self, *, guest_name: str, guest_phone: str, device_id: str) -> dict[str, Any]:
        payload = {
            "device_id": device_id,
            "is_guest_group_buy": 1,
            "nickname": guest_name,
        }
        phone = _resolve_guest_phone(guest_phone)
        if phone:
            payload["phone"] = phone
        async with self._get_client() as client:
            response = await client.post(
                f"{self.base_url}/user/registerGuest",
                headers=self._headers(),
                json=payload,
            )
            return _checked_json(response)

    async def login_guest(self, *, account: str, password: str, device_id: str) -> dict[str, Any]:
        payload = {
            "account": account,
            "password": password,
            "device_id": device_id,
        }
        async with self._get_client() as client:
            response = await client.post(
                f"{self.base_url}/user/loginWebGuest",
                headers=self._headers(),
                json=payload,
            )
            return _checked_json(response)

    async def login_web(self, *, account: str, password: str, device_id: str) -> tuple[str, str]:
        """用手機＋密碼登入 Nidin 會員，回傳 (token, user_id)。

        與官網 nidinLogin() 送出的內容一致：type 1 為手機密碼登入。
        """
        payload = {
            "type": 1,
            "account": account,
            "password": password,
            "device_id": device_id,
            "identity": "nidinphone",
        }
        async with self._get_client() as client:
            response = await client.post(
                f"{self.base_url}/user/loginWeb",
                headers=self._headers(),
                json=payload,
            )
        try:
            body = response.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        token = str(body.get("token") or "")
        if response.is_success and body.get("status", 200) == 200 and token:
            return token, str(body.get("user_id") or "")
        if response.status_code >= 500:
            response.raise_for_status()
        message = body.get("message") or body.get("msg") or "Nidin 拒絕登入，請確認手機號碼與密碼。"
        raise NidinLoginError(str(message))

    async def _authorized_json(
        self, method: str, url: str, *, token: str, user_id: str | int, json_body: Any = None
    ) -> dict[str, Any]:
        async with self._get_client() as client:
            response = await client.request(
                method, url, headers=self._headers(token=token, user_id=user_id), json=json_body
            )
        _raise_if_token_rejected(response)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or body.get("status") != 200:
            message = body.get("message") if isinstance(body, dict) else None
            raise NidinApiError(str(message or "Nidin 回應異常"))
        return body

    async def list_order_history(
        self, *, token: str, user_id: str | int, page: int = 1, count: int = 20
    ) -> list[dict[str, Any]]:
        """已送出的訂單（官網「我的訂單」），新到舊。"""
        # 查詢字串必須自己組：httpx 給了 params= 會整個蓋掉網址上既有的 status[]／sort[]
        query = "&".join(
            [f"status[]={status}" for status in ORDER_HISTORY_STATUSES]
            + ["sort[]=order_time%20DESC", f"page={page}", f"count={count}"]
        )
        body = await self._authorized_json(
            "GET", f"{self.base_url}/order/list?{query}", token=token, user_id=user_id
        )
        return [order for order in body.get("order_list") or [] if isinstance(order, dict)]

    async def list_carts(
        self, *, token: str, user_id: str | int, page: int = 1, count: int = 20
    ) -> list[dict[str, Any]]:
        """還沒送出的購物車；剛開、還在揪的團在這裡，不在訂單歷史。"""
        body = await self._authorized_json(
            "POST",
            f"{self.base_url}/carts/list",
            token=token,
            user_id=user_id,
            json_body={"page": page, "count": count, "order_way": 99},
        )
        return [cart for cart in body.get("carts") or [] if isinstance(cart, dict)]

    async def fetch_share_code(self, *, token: str, user_id: str | int, order_id: str | int) -> str:
        """團長單的揪團碼（官網 getShareByOrderId）。"""
        body = await self._authorized_json(
            "GET", f"{self.base_url}/carts/{order_id}/share", token=token, user_id=user_id
        )
        code = str(body.get("share_code") or "")
        if not code:
            raise NidinApiError("這張單沒有揪團碼，可能不是團購單。")
        return code

    async def fetch_head_order_id(self, *, token: str, user_id: str | int, group_buy_code: str) -> str:
        """團長單號。用團長憑證讀 /groupBuy/{code}/info 時 role 為 1，group_buy_list[0] 就是團長單。"""
        body = await self._authorized_json(
            "GET", f"{self.base_url}/groupBuy/{group_buy_code}/info", token=token, user_id=user_id
        )
        info = body.get("group_buy_info") or {}
        head_orders = info.get("group_buy_list") or []
        if info.get("role") != 1 or not head_orders or not head_orders[0].get("order_id"):
            raise NidinApiError("本站主揪不是這個團在 Nidin 的團長，無法代為刪單。")
        return str(head_orders[0]["order_id"])

    async def delete_follower_order(
        self, *, token: str, user_id: str | int, head_order_id: str, follower_order_id: str
    ) -> None:
        """團長刪掉一張跟團單（官網團長頁的刪單鈕；跟團者送出後無法自己改單）。"""
        await self._authorized_json(
            "DELETE",
            f"{self.base_url}/carts/{head_order_id}/subOrder/{follower_order_id}",
            token=token,
            user_id=user_id,
        )

    async def list_brands(self) -> list[dict[str, Any]]:
        """全部品牌（公開）。"""
        async with self._get_client() as client:
            response = await client.get(f"{self.base_url}/brands", headers=self._headers())
            response.raise_for_status()
        return [brand for brand in response.json().get("brands") or [] if isinstance(brand, dict)]

    async def list_brand_stores(self, brand_id: int | str) -> list[dict[str, Any]]:
        """品牌的所有分店（公開），含座標、評分與營業狀態。"""
        async with self._get_client() as client:
            response = await client.get(f"{self.base_url}/brand/{brand_id}/stores", headers=self._headers())
            response.raise_for_status()
        return [store for store in response.json().get("stores") or [] if isinstance(store, dict)]

    async def list_delivery_addresses(self, *, token: str, user_id: str | int) -> list[dict[str, Any]]:
        """主揪在 Nidin 存的常用地址，加上曾經外送過的地址；常用的排前面（官網地址選單用的兩支）。"""
        favorite = await self._authorized_json(
            "GET", f"{self.base_url}/user/{user_id}/favorite/address", token=token, user_id=user_id
        )
        ordered = await self._authorized_json(
            "GET", f"{self.base_url}/user/listOrderedAddress", token=token, user_id=user_id
        )
        favorites = sorted(favorite.get("list") or [], key=lambda a: not a.get("is_default"))
        return [a for a in favorites + list(ordered.get("list") or []) if isinstance(a, dict)]

    async def create_group_buy(self, *, token: str, user_id: str | int, store_id: str | int) -> dict[str, Any]:
        """以主揪身分在這家店開一個空的團（官網「我要揪團」）。不會下單；團名由 Nidin 自動取。

        回傳 order_id／order_sn／sharing_code。外送方式取店家標為預設的那一種，團長結帳時可以改。
        """
        async with self._get_client() as client:
            info_response, version_response = await asyncio.gather(
                client.get(f"{self.base_url}/store/{store_id}/info", headers=self._headers()),
                client.get(f"{self.base_url}/store/{store_id}/onShelfMenuVer", headers=self._headers()),
            )
        info_response.raise_for_status()
        version_response.raise_for_status()
        info, version = info_response.json(), version_response.json()
        deliveries = info.get("delivery_info") or []
        default = next((d for d in deliveries if d.get("is_default") == 1), deliveries[0] if deliveries else None)
        if not info.get("brand_id") or not version.get("menu_id") or default is None:
            raise NidinApiError("這家店的資料不完整，暫時無法開團。")
        user = await self.fetch_user_info(token=token, user_id=user_id)
        if (user.get("user") or user).get("phone_need_verify") not in (0, "0", None):
            raise NidinApiError("Nidin 要求主揪先驗證手機，請到 Nidin 完成驗證後再開團。")
        body = await self._authorized_json(
            "POST",
            f"{self.base_url}/carts/item",
            token=token,
            user_id=user_id,
            json_body={
                "brand_id": info["brand_id"],
                "store_id": int(store_id),
                "menu_id": version["menu_id"],
                "menu_ver_stamp": (version.get("version") or {}).get("stamp"),
                "order_type": 2,
                "is_empty": 1,
                "delivery_type": default.get("delivery_type"),
            },
        )
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        if not data.get("sharing_code") or not data.get("order_id"):
            raise NidinApiError("Nidin 沒有回傳揪團碼。")
        return data

    async def fetch_user_info(self, *, token: str, user_id: str | int) -> dict[str, Any]:
        """讀取憑證所屬會員的資料（官網 getUserInfo 用的同一支唯讀 GET）。"""
        async with self._get_client() as client:
            response = await client.get(
                f"{self.base_url}/user/{user_id}/info",
                headers=self._headers(token=token, user_id=user_id),
            )
        _raise_if_token_rejected(response)
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else {}

    async def add_group_buy_cart_item(self, *, token: str, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._get_client() as client:
            response = await client.post(
                f"{self.base_url}/carts/subItem",
                headers=self._headers(token=token, user_id=user_id),
                json=payload,
            )
            return _checked_json(response)

    async def submit_group_buy_order(
        self,
        *,
        token: str,
        user_id: int,
        order_id: int,
        guest_name: str,
        guest_phone: str,
        total_money: int,
    ) -> dict[str, Any]:
        payload = {
            "order_id": str(order_id),
            "order_type": 3,
            "name": guest_name,
            "total_money": total_money,
            "contact_method_remark": guest_phone,
            "phone_type": 2,
        }
        async with self._get_client() as client:
            response = await client.post(
                f"{self.base_url}/order/productCouponFlow",
                headers=self._headers(token=token, user_id=user_id),
                json=payload,
            )
            return _checked_json(response)

    async def order_guest_once(
        self,
        *,
        group_buy_url: str,
        guest_name: str,
        guest_phone: str,
        order_text: str,
        expected_lines: list[ExpectedOrderLine] | None = None,
    ) -> GuestOrderReceipt:
        target = extract_group_buy_target(group_buy_url)
        raw_menu = await self.fetch_raw_menu(target.store_id)
        menu = validated_order_menu(raw_menu, target.store_id)
        if expected_lines is None:
            parsed_orders = parse_order_lines(order_text, menu)
        else:
            lines = build_expected_cart_lines(expected_lines, menu)
            validate_cart_lines(lines, menu)
            parsed_orders = build_parsed_orders_from_cart_lines(lines, menu)
        return await self.order_guest_parsed_orders(
            group_buy_url=group_buy_url,
            guest_name=guest_name,
            guest_phone=guest_phone,
            parsed_orders=parsed_orders,
            target=target,
            raw_menu=raw_menu,
        )

    async def order_guest_cart_session(
        self,
        *,
        group_buy_url: str,
        guest_name: str,
        guest_phone: str,
        lines: list[CartSessionLine],
        menu: NormalizedMenu,
        progress: dict[str, Any] | None = None,
    ) -> GuestOrderReceipt:
        if progress is not None:
            progress["stage"] = "prepare"
        target = extract_group_buy_target(group_buy_url)
        raw_menu = await self.fetch_raw_menu(target.store_id)
        fresh_menu = validated_order_menu(raw_menu, target.store_id)
        validate_cart_lines(lines, fresh_menu)
        parsed_orders = build_parsed_orders_from_cart_lines(lines, fresh_menu)
        return await self.order_guest_parsed_orders(
            group_buy_url=group_buy_url,
            guest_name=guest_name,
            guest_phone=guest_phone,
            parsed_orders=parsed_orders,
            target=target,
            raw_menu=raw_menu,
            progress=progress,
        )

    async def order_guest_parsed_orders(
        self,
        *,
        group_buy_url: str,
        guest_name: str,
        guest_phone: str,
        parsed_orders: list[ParsedOrder],
        target: GroupBuyTarget | None = None,
        raw_menu: dict[str, Any] | None = None,
        progress: dict[str, Any] | None = None,
    ) -> GuestOrderReceipt:
        """progress（可選）會被寫入 stage 與 cart_order_id：

        stage 是 "commit" 之前的任何失敗都確定沒有在 Nidin 成立；進入 commit（送出 productCouponFlow）
        之後的失敗則可能已經成立，呼叫端要當成「結果不明」處理，不能直接讓人重送。
        """
        progress = progress if progress is not None else {}
        progress["stage"] = "prepare"
        resolved_target = target or extract_group_buy_target(group_buy_url)
        device_id = self._make_device_id()
        resolved_guest_phone = _resolve_guest_phone(guest_phone)

        group_buy_brief = await self.fetch_group_buy_brief(resolved_target.group_buy_code)
        resolved_raw_menu = raw_menu or await self.fetch_raw_menu(resolved_target.store_id)

        registration = await self.register_guest(
            guest_name=guest_name,
            guest_phone=resolved_guest_phone,
            device_id=device_id,
        )
        login = await self.login_guest(
            account=registration["account"],
            password=registration["password"],
            device_id=device_id,
        )

        cart_payload = build_group_buy_cart_payload(
            group_buy_code=resolved_target.group_buy_code,
            group_buy_brief=group_buy_brief,
            menu_payload=resolved_raw_menu,
            parsed_orders=parsed_orders,
        )
        cart_result = await self.add_group_buy_cart_item(
            token=login["token"],
            user_id=login["user_id"],
            payload=cart_payload,
        )
        # 跟團購物車的單號就是之後 /groupBuy/{code}/info 裡這張跟團單的 order_id，結果不明時靠它對帳
        progress["cart_order_id"] = str(cart_result["order_id"])
        progress["guest_user_id"] = str(login["user_id"])
        progress["stage"] = "commit"
        submit_result = await self.submit_group_buy_order(
            token=login["token"],
            user_id=login["user_id"],
            order_id=cart_result["order_id"],
            guest_name=guest_name,
            guest_phone=resolved_guest_phone,
            total_money=cart_payload["total_money"],
        )

        return GuestOrderReceipt(
            order_id=int(submit_result["order_id"]),
            order_status=int(submit_result["order_status"]),
            store_id=resolved_target.store_id,
            group_buy_code=resolved_target.group_buy_code,
            guest_user_id=int(login["user_id"]),
            total_money=int(cart_payload["total_money"]),
            item_name=", ".join(order.item.name for order in parsed_orders),
        )

    async def fetch_group_buy_official_info(
        self,
        group_buy_code: str,
        token: str,
        user_id: str | int = "",
    ) -> OfficialGroupBuySummary:
        async with self._get_client() as client:
            response = await client.get(
                f"{self.base_url}/groupBuy/{group_buy_code}/info",
                headers=self._headers(token=token, user_id=user_id),
            )
            _raise_if_token_rejected(response)
            body = _checked_json(response)
            # HTTP 200 但沒有跟團清單＝不是正常的團購資料；當成空團同步會把本站整團清空
            if "group_buy_follower_order_list" not in body:
                raise NidinApiError(str(body.get("message") or "Nidin 沒有回傳跟團清單"))
            return normalize_official_group_buy_info(body, group_buy_code)
