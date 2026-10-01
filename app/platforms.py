"""點單平台：本站的團開在 Nidin 或 Ocard 上。

送單紀錄、防重送、取消憑證、刷新節流這些本站的規則由 routers/live_orders.py 統一處理；
「在平台上實際做什麼」才分到這裡。新增平台＝多寫一個 Platform 實作並登記在 PLATFORMS。
"""

import asyncio
from dataclasses import dataclass
from typing import Protocol

import httpx
from fastapi import HTTPException

from app import auth as account_auth
from app import dependencies
from app.auth import AuthUser
from app.deps_shared import (
    build_default_session_name,
    fetch_current_menu,
    initialize_legacy_ownership,
)
from app.models import (
    CartSessionDetail,
    CartSessionLine,
    CartSessionSummary,
    GuestOrderReceipt,
    NormalizedMenu,
    OfficialGroupBuySummary,
)
from app.nidin import MenuChangedError, MenuDataError, extract_group_buy_target
from app.nidin.client import NidinApiError
from app.ocard import (
    GROUP_OPEN as OCARD_GROUP_OPEN,
)
from app.ocard import (
    OcardApiError,
    check_exclusions,
    group_status,
    is_ocard_url,
    normalize_group,
    parse_group_url,
    target_from_session,
)
from app.routers.leader_auth import call_with_nidin_relogin, fetch_official_info

NIDIN_GROUP_OPEN = 10  # group_buying_status：10 揪團中、20 已截止


@dataclass
class PlacedOrder:
    official_order_id: str
    receipt: GuestOrderReceipt | None = None
    # 之後取消這張單要用的東西（Ocard：這張單專屬的裝置權杖）；Nidin 靠主揪憑證刪單，不需要
    cancel_ref: str | None = None


class PartiallySentError(Exception):
    """已經在平台上成立了一部分、又撤不回來：不能當作「確定沒送出」讓人重試。"""


class Platform(Protocol):
    key: str
    label: str
    # 跟團者能不能留電話給團長
    accepts_phone: bool

    def owns_url(self, url: str) -> bool: ...

    async def create_session(self, url: str, session_name: str, owner_id: str) -> CartSessionSummary: ...

    async def fetch_menu(self, session: CartSessionSummary) -> NormalizedMenu: ...

    async def ensure_open(self, session: CartSessionSummary) -> None: ...

    async def submit(
        self,
        session: CartSessionSummary,
        *,
        name: str,
        phone: str,
        lines: list[CartSessionLine],
        menu: NormalizedMenu,
        progress: dict[str, object],
    ) -> PlacedOrder: ...

    def definitely_not_sent(self, exc: BaseException) -> bool: ...

    async def fetch_official(self, session: CartSessionSummary) -> OfficialGroupBuySummary: ...

    async def cancel(self, session: CartSessionSummary, official_order_id: str, cancel_ref: str | None) -> None: ...


def session_owner_user(session_id: str, label: str) -> AuthUser:
    initialize_legacy_ownership()
    owner_id = dependencies.get_cart_store().session_owner(session_id)
    owner = account_auth.get_user(owner_id) if owner_id else None
    if owner is None:
        raise HTTPException(status_code=409, detail=f"這個團沒有主揪，無法連到 {label}。")
    return owner


def _connection_failed(exc: BaseException) -> bool:
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))


def _client_error(exc: BaseException) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and 400 <= exc.response.status_code < 500


class NidinPlatform:
    key = "nidin"
    label = "Nidin"
    accepts_phone = True

    def owns_url(self, url: str) -> bool:
        try:
            extract_group_buy_target(url)
        except ValueError:
            return False
        return True

    async def create_session(self, url: str, session_name: str, owner_id: str) -> CartSessionSummary:
        target = extract_group_buy_target(url)
        client = dependencies.get_nidin_client()
        group_buy_brief, menu, delivery_summary = await asyncio.gather(
            client.fetch_group_buy_brief(target.group_buy_code),
            client.fetch_menu(target.store_id),
            client.fetch_store_delivery_summary(target.store_id),
        )
        return dependencies.get_cart_store().create_or_get_session(
            group_buy_url=url,
            store_id=target.store_id,
            group_buy_code=target.group_buy_code,
            session_name=build_default_session_name(
                explicit_name=session_name,
                group_buy_brief=group_buy_brief,
                menu=menu,
                delivery_summary=delivery_summary,
            ),
            menu=menu,
            delivery_summary=delivery_summary,
            owner_id=owner_id,
        )

    async def fetch_menu(self, session: CartSessionSummary) -> NormalizedMenu:
        return await fetch_current_menu(dependencies.get_nidin_client(), session.store_id)

    async def ensure_open(self, session: CartSessionSummary) -> None:
        brief = await dependencies.get_nidin_client().fetch_group_buy_brief(session.group_buy_code)
        if brief.get("group_buying_status") != NIDIN_GROUP_OPEN:
            raise HTTPException(status_code=409, detail="這個團在 Nidin 上已經截止，不能再點了。")

    async def submit(self, session, *, name, phone, lines, menu, progress) -> PlacedOrder:
        receipt = await dependencies.get_nidin_client().order_guest_cart_session(
            group_buy_url=session.group_buy_url,
            guest_name=name,
            guest_phone=phone,
            lines=lines,
            menu=menu,
            progress=progress,
        )
        return PlacedOrder(official_order_id=str(receipt.order_id), receipt=receipt)

    def definitely_not_sent(self, exc: BaseException) -> bool:
        """送出 productCouponFlow 之後的失敗中，哪些確定沒有在 Nidin 成立。"""
        return _connection_failed(exc) or isinstance(exc, NidinApiError) or _client_error(exc)

    async def fetch_official(self, session: CartSessionSummary) -> OfficialGroupBuySummary:
        return await fetch_official_info(session_owner_user(session.session_id, self.label), session.group_buy_code)

    async def cancel(self, session: CartSessionSummary, official_order_id: str, cancel_ref: str | None) -> None:
        """跟團者送出後 Nidin 不讓自己改單，由本站拿主揪憑證以團長身分刪。"""
        store = dependencies.get_cart_store()
        owner = session_owner_user(session.session_id, self.label)
        client = dependencies.get_nidin_client()
        head_order_id = store.nidin_order_id(session.session_id)
        if not head_order_id:
            head_order_id, _ = await call_with_nidin_relogin(
                owner,
                lambda token, user_id: client.fetch_head_order_id(
                    token=token, user_id=user_id, group_buy_code=session.group_buy_code
                ),
            )
            store.link_nidin_order(session.session_id, head_order_id)
        await call_with_nidin_relogin(
            owner,
            lambda token, user_id: client.delete_follower_order(
                token=token, user_id=user_id, head_order_id=head_order_id, follower_order_id=official_order_id
            ),
        )


class OcardPlatform:
    """以訪客身分跟團：不需要主揪的 Ocard 帳號，讀團內容也不必加入。"""

    key = "ocard"
    label = "Ocard"
    accepts_phone = False

    def owns_url(self, url: str) -> bool:
        return is_ocard_url(url)

    async def create_session(self, url: str, session_name: str, owner_id: str) -> CartSessionSummary:
        target = parse_group_url(url)
        client = dependencies.get_ocard_client()
        group, store_name = await client.fetch_group(target)
        if group_status(group) != OCARD_GROUP_OPEN:
            raise ValueError("這個 Ocard 揪團已經不在揪團中了。")
        ocard_menu = await client.fetch_menu(target, order_type=str(group.get("orderType") or "DELIVERY"))
        host = str((group.get("host") or {}).get("name") or "")
        default_name = session_name.strip() or store_name or (f"{host} 的揪團" if host else "")
        return dependencies.get_cart_store().create_or_get_session(
            group_buy_url=target.url,
            store_id=target.session_store_id,
            group_buy_code=target.group_code,
            session_name=default_name,
            menu=ocard_menu.menu,
            delivery_summary=None,
            owner_id=owner_id,
            provider=self.key,
        )

    async def _menu(self, session: CartSessionSummary):
        return await dependencies.get_ocard_client().fetch_menu(
            target_from_session(session.store_id, session.group_buy_code)
        )

    async def fetch_menu(self, session: CartSessionSummary) -> NormalizedMenu:
        try:
            return (await self._menu(session)).menu
        except (httpx.NetworkError, httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
            raise HTTPException(503, "Ocard 暫時無法連線，請稍後重試。") from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise HTTPException(503 if status >= 500 else 502, f"Ocard 菜單請求失敗（HTTP {status}）。") from exc
        except OcardApiError as exc:
            raise HTTPException(502, f"Ocard 菜單讀取失敗：{exc}") from exc

    async def ensure_open(self, session: CartSessionSummary) -> None:
        target = target_from_session(session.store_id, session.group_buy_code)
        try:
            group, _ = await dependencies.get_ocard_client().fetch_group(target)
        except OcardApiError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if group_status(group) != OCARD_GROUP_OPEN:
            raise HTTPException(status_code=409, detail="這個團在 Ocard 上已經截止，不能再點了。")

    async def submit(self, session, *, name, phone, lines, menu, progress) -> PlacedOrder:
        progress["stage"] = "prepare"
        target = target_from_session(session.store_id, session.group_buy_code)
        client = dependencies.get_ocard_client()
        ocard_menu = await self._menu(session)
        items = {item.id: item for item in ocard_menu.menu.items}
        for line in lines:
            if line.item_id not in items:
                raise MenuChangedError(f"「{line.item_name}」已不在 Ocard 菜單上，請重新選擇。")
            check_exclusions([s.option_id for s in line.selections], ocard_menu)
        group, _ = await client.fetch_group(target)
        order_type = str(group.get("orderType") or "DELIVERY")
        dtk = await client.register_device(target)

        # 加入後 Ocard 上就有一張（空的）跟團單；之後任何失敗都要試著退團，不留半套的單
        progress["stage"] = "commit"
        order_id = await client.join_group(target, dtk=dtk, name=name)
        progress["cart_order_id"] = order_id
        try:
            for line in lines:
                await client.add_item(
                    target,
                    dtk=dtk,
                    order_id=order_id,
                    order_type=order_type,
                    item_id=line.item_id,
                    count=line.quantity,
                    option_ids=[s.option_id for s in line.selections],
                )
        except Exception as exc:
            try:
                await client.leave_group(target, dtk=dtk, order_id=order_id)
            except Exception as leave_exc:
                # 退不掉：Ocard 上可能留著部分品項，交給呼叫端標成結果不明、之後同步對帳
                raise PartiallySentError(str(exc)) from leave_exc
            raise OcardApiError(f"加入品項失敗，已撤回這筆：{exc}") from exc
        return PlacedOrder(official_order_id=order_id, cancel_ref=dtk)

    def definitely_not_sent(self, exc: BaseException) -> bool:
        return _connection_failed(exc) or isinstance(exc, OcardApiError) or _client_error(exc)

    async def fetch_official(self, session: CartSessionSummary) -> OfficialGroupBuySummary:
        target = target_from_session(session.store_id, session.group_buy_code)
        client = dependencies.get_ocard_client()
        try:
            group, _ = await client.fetch_group(target)
            ocard_menu = await self._menu(session)
        except OcardApiError as exc:
            raise HTTPException(status_code=502, detail=f"Ocard 回應異常：{exc}") from exc
        return normalize_group(target, group, ocard_menu.menu_id_by_item_id)

    async def cancel(self, session: CartSessionSummary, official_order_id: str, cancel_ref: str | None) -> None:
        if not cancel_ref:
            raise HTTPException(
                status_code=409, detail="這筆不是從本站送出的，本站沒辦法取消。請團長到 Ocard 處理。"
            )
        target = target_from_session(session.store_id, session.group_buy_code)
        await dependencies.get_ocard_client().leave_group(target, dtk=cancel_ref, order_id=official_order_id)


NIDIN = NidinPlatform()
OCARD = OcardPlatform()
PLATFORMS: dict[str, Platform] = {NIDIN.key: NIDIN, OCARD.key: OCARD}


def platform_for_session(session: CartSessionSummary | CartSessionDetail) -> Platform:
    return PLATFORMS.get(session.provider, NIDIN)


def platform_for_url(url: str) -> Platform:
    for platform in (OCARD, NIDIN):
        if platform.owns_url(url):
            return platform
    raise ValueError("看不懂這個連結。請貼 Nidin 或 Ocard 的揪團連結。")


def platform_errors() -> tuple[type[Exception], ...]:
    """各平台「明確拒絕」的例外，給呼叫端統一處理。"""
    return (NidinApiError, OcardApiError, MenuChangedError, MenuDataError)
