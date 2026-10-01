import os
import time
from typing import Awaitable, Callable, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request, Response
import httpx

from app import auth as account_auth
from app import dependencies
from app.auth import AuthUser, require_user
from app.deps_shared import initialize_legacy_ownership, require_session_owner
from app.models import (
    CartSessionDetail,
    LeaderAuthCheck,
    LeaderAuthRequest,
    LeaderAuthStatus,
    LeaderPasswordLoginRequest,
    OfficialGroupBuySummary,
)
from app.nidin.client import (
    NidinApiError,
    NidinLoginError,
    NidinTokenExpired,
    make_device_id,
    token_expires_at,
)

router = APIRouter()
T = TypeVar("T")
# 自動重登被 Nidin 拒絕後的冷卻時間。公開頁每 20 秒刷新一次，失敗就一直重試會拿錯的密碼反覆撞 Nidin 登入；
# 以前的做法是直接刪掉密碼，但一次暫時性的拒絕就要主揪重新綁定，代價太大
RELOGIN_BACKOFF_SECONDS = 30 * 60
_relogin_failed_at: dict[str, float] = {}


def _mask_token(token: str) -> str:
    return f"{token[:6]}...{token[-4:]}" if len(token) > 10 else "***"


@router.get("/leader/auth", response_model=LeaderAuthStatus)
async def get_leader_auth_status(
    user: AuthUser = Depends(require_user),
) -> LeaderAuthStatus:
    initialize_legacy_ownership()
    auth = account_auth.get_leader_credentials(user.id)
    if auth is None:
        return LeaderAuthStatus(is_authenticated=False)
    token, user_id = auth
    return LeaderAuthStatus(
        is_authenticated=True,
        user_id=user_id,
        masked_token=_mask_token(token),
        auto_relogin=account_auth.has_nidin_login(user.id),
        nidin_login_linked=account_auth.has_nidin_account_link(user.id),
        expires_at=token_expires_at(token),
    )


async def _nidin_login(account: str, password: str, device_id: str) -> tuple[str, str]:
    client = dependencies.get_nidin_client()
    try:
        return await client.login_web(account=account, password=password, device_id=device_id)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="連不上 Nidin，請稍後再試。") from exc


@router.post("/leader/auth/password", response_model=LeaderAuthStatus)
async def login_leader_with_password(
    request: LeaderPasswordLoginRequest,
    user: AuthUser = Depends(require_user),
) -> LeaderAuthStatus:
    initialize_legacy_ownership()
    device_id = make_device_id()
    try:
        token, nidin_user_id = await _nidin_login(request.account, request.password, device_id)
    except NidinLoginError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _relogin_failed_at.pop(user.id, None)
    account_auth.set_leader_credentials(
        user.id, token, nidin_user_id, keep_login=request.remember
    )
    if request.remember:
        account_auth.save_nidin_login(user.id, request.account, request.password, device_id)
    # user_id 是 Nidin 伺服器驗過密碼後回的，才可以拿來當登入本站的身分
    if nidin_user_id:
        account_auth.link_nidin_account(user.id, nidin_user_id)
    return LeaderAuthStatus(
        is_authenticated=True,
        user_id=nidin_user_id,
        masked_token=_mask_token(token),
        auto_relogin=request.remember,
        nidin_login_linked=bool(nidin_user_id),
        expires_at=token_expires_at(token),
    )


def _is_owner_phone(account: str) -> bool:
    owner = os.getenv("NIDIN_OWNER_PHONE", "").strip()
    return bool(owner) and owner == account


@router.post("/auth/nidin-login", response_model=AuthUser)
async def login_with_nidin_account(
    payload: LeaderPasswordLoginRequest,
    request: Request,
    response: Response,
) -> AuthUser:
    """用 Nidin 手機＋密碼登入本站，同時更新團長憑證。

    只接受已在後台綁定過的 Nidin 帳號；否則任何 Nidin 會員都能進後台。
    """
    account_auth.check_csrf(request)
    account_auth.limit_login(request, f"nidin:{payload.account}")
    device_id = make_device_id()
    try:
        token, nidin_user_id = await _nidin_login(payload.account, payload.password, device_id)
    except NidinLoginError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if nidin_user_id and _is_owner_phone(payload.account):
        # 站長的 Nidin 帳號（密碼剛由 Nidin 驗過）直接連到管理員；站上還沒帳號就建一個
        account_auth.link_nidin_account(account_auth.ensure_owner_admin(), nidin_user_id)
    user = account_auth.login_linked_nidin_account(nidin_user_id, response) if nidin_user_id else None
    if user is None:
        raise HTTPException(
            status_code=403,
            detail="這個 Nidin 帳號還沒連結到本站。請先用本站帳號登入，在「設定 Nidin 連線」用手機＋密碼綁定一次，之後就能直接用 Nidin 帳號登入。",
        )
    _relogin_failed_at.pop(user.id, None)
    account_auth.set_leader_credentials(user.id, token, nidin_user_id, keep_login=payload.remember)
    if payload.remember:
        account_auth.save_nidin_login(user.id, payload.account, payload.password, device_id)
    return user


def _upstream_error(exc: httpx.HTTPStatusError) -> HTTPException:
    return HTTPException(
        status_code=502,
        detail=f"Nidin API 回傳錯誤（HTTP {exc.response.status_code}），請稍後重試。",
    )


async def call_with_nidin_relogin(
    user: AuthUser, call: Callable[[str, str], Awaitable[T]]
) -> tuple[T, bool]:
    """帶團長憑證呼叫 Nidin；憑證被作廢時，若有記住密碼就重新登入並重試一次。

    回傳 (結果, 這次是否重新登入過)。
    """
    auth = account_auth.get_leader_credentials(user.id)
    if not auth:
        raise HTTPException(status_code=401, detail="尚未連結 Nidin，請先到「設定 Nidin 連線」登入。")

    token, user_id = auth
    try:
        return await call(token, user_id), False
    except NidinTokenExpired:
        pass
    except httpx.HTTPStatusError as exc:
        raise _upstream_error(exc) from exc

    login = account_auth.get_nidin_login(user.id)
    if login is None:
        raise HTTPException(status_code=401, detail="Nidin 憑證已失效，請到「設定 Nidin 連線」重新綁定。")
    failed_at = _relogin_failed_at.get(user.id)
    if failed_at is not None and time.monotonic() - failed_at < RELOGIN_BACKOFF_SECONDS:
        raise HTTPException(
            status_code=401,
            detail="Nidin 憑證已失效，自動重新登入最近失敗過，請主揪到「設定 Nidin 連線」重新登入。",
        )
    account, password, device_id = login
    try:
        token, user_id = await _nidin_login(account, password, device_id)
    except NidinLoginError as exc:
        _relogin_failed_at[user.id] = time.monotonic()
        raise HTTPException(
            status_code=401,
            detail=f"Nidin 憑證已失效，自動重新登入也失敗（{exc}），請到「設定 Nidin 連線」重新綁定。",
        ) from exc
    _relogin_failed_at.pop(user.id, None)
    account_auth.set_leader_credentials(user.id, token, user_id, keep_login=True)
    try:
        return await call(token, user_id), True
    except NidinTokenExpired as exc:
        raise HTTPException(status_code=401, detail="Nidin 剛發的憑證也被拒絕，請稍後再試。") from exc
    except httpx.HTTPStatusError as exc:
        raise _upstream_error(exc) from exc


async def fetch_official_info(user: AuthUser, group_buy_code: str) -> OfficialGroupBuySummary:
    client = dependencies.get_nidin_client()
    try:
        summary, _ = await call_with_nidin_relogin(
            user,
            lambda token, user_id: client.fetch_group_buy_official_info(
                group_buy_code=group_buy_code, token=token, user_id=user_id
            ),
        )
    except NidinApiError as exc:
        raise HTTPException(status_code=502, detail=f"Nidin 回應異常：{exc}") from exc
    return summary


@router.post("/leader/auth/check", response_model=LeaderAuthCheck)
async def check_leader_auth(
    user: AuthUser = Depends(require_user),
) -> LeaderAuthCheck:
    """實際拿憑證向 Nidin 讀會員資料，證明連線真的可用（不只是資料庫裡有一串字）。

    用 POST：憑證失效時會重新登入並寫回新憑證，不是純讀取。
    """
    initialize_legacy_ownership()
    client = dependencies.get_nidin_client()
    info, relogged_in = await call_with_nidin_relogin(
        user, lambda token, user_id: client.fetch_user_info(token=token, user_id=user_id)
    )
    if info.get("status") != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Nidin 回應異常：{info.get('message') or info.get('status')}",
        )
    token, stored_user_id = account_auth.get_leader_credentials(user.id) or ("", "")
    return LeaderAuthCheck(
        nidin_user_id=str(info.get("user_id") or stored_user_id),
        display_name=str(info.get("nickname") or info.get("name") or ""),
        expires_at=token_expires_at(token),
        relogged_in=relogged_in,
        auto_relogin=account_auth.has_nidin_login(user.id),
    )


@router.post("/leader/auth", response_model=LeaderAuthStatus)
async def set_leader_auth(
    request: LeaderAuthRequest,
    user: AuthUser = Depends(require_user),
) -> LeaderAuthStatus:
    initialize_legacy_ownership()
    token = request.token.strip()
    if not token:
        raise HTTPException(status_code=422, detail="Token 不能為空")
    account_auth.set_leader_credentials(user.id, token, request.user_id.strip())
    return LeaderAuthStatus(
        is_authenticated=True,
        user_id=request.user_id.strip(),
        masked_token=_mask_token(token),
    )


@router.delete("/leader/auth", response_model=LeaderAuthStatus)
async def clear_leader_auth(
    user: AuthUser = Depends(require_user),
) -> LeaderAuthStatus:
    initialize_legacy_ownership()
    account_auth.clear_leader_credentials(user.id)
    # 解除綁定的人會預期 Nidin 帳號從此進不了後台
    account_auth.unlink_nidin_account(user.id)
    return LeaderAuthStatus(is_authenticated=False)


@router.get("/cart-sessions/{session_id}/official-orders", response_model=OfficialGroupBuySummary)
async def get_official_group_buy_orders(
    session_id: str,
    user: AuthUser = Depends(require_session_owner),
) -> OfficialGroupBuySummary:
    store = dependencies.get_cart_store()
    try:
        session = store.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return await fetch_official_info(user, session.group_buy_code)


@router.post("/cart-sessions/{session_id}/sync-official", response_model=CartSessionDetail)
async def sync_official_group_buy_orders(
    session_id: str,
    user: AuthUser = Depends(require_session_owner),
) -> CartSessionDetail:
    store = dependencies.get_cart_store()
    try:
        session = store.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    official_summary = await fetch_official_info(user, session.group_buy_code)
    return store.merge_official_orders_to_lines(session_id, official_summary)
