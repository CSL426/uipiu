"""點單即送進 Nidin：Nidin 的團購才是真相，本站只是替大家操作、再把狀態撈回來顯示。

- 送單：每人一張 Nidin 跟團單（訪客身分、名字＝點單人），送成功才寫本站並標上 Nidin 單號。
- 取消：跟團者送出後無法自己改單，只能由團長刪單；本站用主揪的 Nidin 憑證代刪。
- 刷新：公開頁定期請後端用主揪憑證同步，大家看到的是 Nidin 上的實際內容。
"""

from collections import deque
import hashlib
import hmac
import json
from threading import Lock
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request
import httpx
from pydantic import BaseModel, Field, field_validator

from app import auth as account_auth
from app import credential_crypto
from app import dependencies
from app.auth import AuthUser
from app.deps_shared import fetch_current_menu, initialize_legacy_ownership
from app.models import CartSessionDetail, GuestOrderReceipt
from app.nidin.builder import MenuChangedError, MenuDataError
from app.nidin.client import NidinApiError
from app.nidin.normalizer import _resolve_guest_phone
from app.routers.cart_sessions import AddCartLineDraftRequest
from app.routers.leader_auth import call_with_nidin_relogin, fetch_official_info

router = APIRouter()

GROUP_OPEN = 10  # group_buying_status：10 揪團中、20 已截止
REFRESH_INTERVAL_SECONDS = 10.0
# 結果不明的送單超過這個時間、同步後在 Nidin 仍找不到，就判定沒成立
UNCERTAIN_GRACE_SECONDS = 60.0
# 公開、免登入就能真的在主揪的 Nidin 團下單；同一個來源 IP 10 分鐘最多 60 筆（辦公室共用 NAT 也夠用）
SUBMIT_WINDOW_SECONDS = 600.0
SUBMIT_LIMIT_PER_IP = 60
_last_refresh: dict[str, float] = {}
_refresh_lock = Lock()
_submits_by_ip: dict[str, deque[float]] = {}
_submit_lock = Lock()


class SubmitOrderRequest(BaseModel):
    # 前端每按一次「送出」產生一個；逾時重試時沿用同一個，後端才認得出是同一筆
    submission_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    # Nidin 顯示時會截到 20 字
    contributor_name: str = Field(min_length=1, max_length=20)
    # 選填：給團長聯絡用。沒填就不送（Nidin 官網也是如此），不再替人亂編號碼
    contributor_phone: str = Field(default="", max_length=20)
    lines: list[AddCartLineDraftRequest] = Field(min_length=1, max_length=20)

    @field_validator("contributor_name")
    @classmethod
    def require_real_name(cls, value: str) -> str:
        value = value.strip()
        # 名字就是 Nidin 上這張跟團單的身分，也是本站收款、取消的依據；「訪客」會讓大家撞在一起
        if not value or value == "訪客":
            raise ValueError("請先填你的名字")
        return value

    @field_validator("contributor_phone")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        return _resolve_guest_phone(value)


class SubmitOrderResponse(BaseModel):
    receipt: GuestOrderReceipt | None = None
    official_order_id: str
    cancel_token: str
    session: CartSessionDetail
    already_submitted: bool = False


class RefreshSessionResponse(BaseModel):
    session: CartSessionDetail
    synced: bool
    message: str | None = None


def _limit_submissions(request: Request) -> None:
    peer = request.client.host if request.client else "unknown"
    now = time.monotonic()
    with _submit_lock:
        attempts = _submits_by_ip.setdefault(peer, deque())
        while attempts and attempts[0] <= now - SUBMIT_WINDOW_SECONDS:
            attempts.popleft()
        if len(attempts) >= SUBMIT_LIMIT_PER_IP:
            raise HTTPException(status_code=429, detail="送單太頻繁了，請稍後再試。")
        attempts.append(now)


def _request_hash(session_id: str, payload: "SubmitOrderRequest") -> str:
    """同一個 submission_id 只能代表同一份內容；換團或改了內容就不是同一筆。"""
    canonical = json.dumps(
        [session_id, payload.contributor_name, payload.contributor_phone, [line.model_dump() for line in payload.lines]],
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _definitely_not_sent(exc: BaseException) -> bool:
    """送出 productCouponFlow 之後的失敗中，哪些確定沒有在 Nidin 成立。"""
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, NidinApiError)):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and 400 <= exc.response.status_code < 500


def _not_sent_message(exc: BaseException) -> tuple[int, str]:
    if isinstance(exc, NidinApiError):
        return 409, f"Nidin 拒絕了這筆：{exc}"
    if isinstance(exc, (MenuChangedError, MenuDataError)):
        return 409, str(exc)
    if isinstance(exc, HTTPException):
        return exc.status_code, str(exc.detail)
    if isinstance(exc, httpx.HTTPStatusError):
        return 502, f"Nidin 回傳錯誤（HTTP {exc.response.status_code}），這筆沒有送出。"
    if isinstance(exc, httpx.HTTPError):
        return 503, "連不上 Nidin，這筆還沒有送出。"
    if isinstance(exc, ValueError):
        return 422, str(exc)
    return 502, "送單前發生錯誤，這筆沒有送出。"


def _cancel_token(submission_id: str) -> str:
    """由伺服器金鑰衍生，不必存；同一個 submission_id 重送時可以原樣再給一次。"""
    data_dir = dependencies.get_cart_store()._db_path.parent
    secret = credential_crypto.derive_secret(data_dir, "order-cancel-token")
    return hmac.new(secret, submission_id.encode(), hashlib.sha256).hexdigest()


def _session_owner(session_id: str) -> AuthUser:
    initialize_legacy_ownership()
    owner_id = dependencies.get_cart_store().session_owner(session_id)
    owner = account_auth.get_user(owner_id) if owner_id else None
    if owner is None:
        raise HTTPException(status_code=409, detail="這個團沒有主揪，無法連到 Nidin。")
    return owner


def _get_session(session_id: str) -> CartSessionDetail:
    try:
        return dependencies.get_cart_store().get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="找不到這個團購。") from exc


async def _sync_from_nidin(session: CartSessionDetail) -> CartSessionDetail:
    store = dependencies.get_cart_store()
    official = await fetch_official_info(_session_owner(session.session_id), session.group_buy_code)
    detail = store.merge_official_orders_to_lines(session.session_id, official)
    store.reconcile_submissions(
        session.session_id,
        {str(order.order_id) for order in official.follower_orders},
        grace_seconds=UNCERTAIN_GRACE_SECONDS,
    )
    return detail


@router.post("/cart-sessions/{session_id}/orders", response_model=SubmitOrderResponse)
async def submit_order_to_nidin(
    session_id: str, payload: SubmitOrderRequest, request: Request
) -> SubmitOrderResponse:
    account_auth.check_csrf(request)
    session = _get_session(session_id)
    store = dependencies.get_cart_store()
    client = dependencies.get_nidin_client()

    existing = store.begin_submission(
        payload.submission_id, session_id, payload.contributor_name, _request_hash(session_id, payload)
    )
    if existing is not None:
        if existing["conflict"]:
            raise HTTPException(
                status_code=409, detail="這個送單編號已經用在別的內容上了。請重新整理頁面後再送。"
            )
        if existing["status"] == "submitted":
            return SubmitOrderResponse(
                official_order_id=existing["official_order_id"],
                cancel_token=_cancel_token(payload.submission_id),
                session=store.get_session(session_id),
                already_submitted=True,
            )
        if existing["status"] == "cancelled":
            raise HTTPException(status_code=409, detail="這筆已經取消了。要再點請重新下單。")
        raise HTTPException(
            status_code=409,
            detail="上一筆還在跟 Nidin 確認中。請等一分鐘後按「重新整理」，確認 Nidin 上有沒有這筆再決定要不要重送。",
        )
    _limit_submissions(request)

    def fail(exc: BaseException) -> HTTPException:
        status_code, message = _not_sent_message(exc)
        store.finish_submission(payload.submission_id, status="failed", message=message)
        return HTTPException(status_code=status_code, detail=message)

    # 送出前：這一段任何失敗都確定沒到 Nidin，標 failed 讓同一個 id 可以重試
    try:
        brief = await client.fetch_group_buy_brief(session.group_buy_code)
        if brief.get("group_buying_status") != GROUP_OPEN:
            raise HTTPException(status_code=409, detail="這個團在 Nidin 上已經截止，不能再點了。")
        menu = await fetch_current_menu(client, session.store_id)
        store.replace_menu(session_id, menu)
        built = store.build_order_lines(
            session_id,
            contributor_name=payload.contributor_name,
            lines=[line.model_dump() for line in payload.lines],
            menu=menu,
        )
    except Exception as exc:
        raise fail(exc) from exc

    progress: dict[str, object] = {}
    try:
        receipt = await client.order_guest_cart_session(
            group_buy_url=session.group_buy_url,
            guest_name=payload.contributor_name,
            guest_phone=payload.contributor_phone,
            lines=[line for line, _ in built],
            menu=menu,
            progress=progress,
        )
    except Exception as exc:
        if progress.get("stage") != "commit" or _definitely_not_sent(exc):
            raise fail(exc) from exc
        # 已送出最後一步卻沒拿到可信結果：Nidin 可能已成立。記下跟團購物車單號，之後同步時對帳
        pending_order_id = progress.get("cart_order_id")
        store.mark_submission_uncertain(payload.submission_id, str(pending_order_id) if pending_order_id else None)
        raise HTTPException(
            status_code=503,
            detail="送出後沒有收到 Nidin 的確定結果，這筆可能已經成立。請等一分鐘後按「重新整理」確認，不要直接重點。",
        ) from exc

    official_order_id = str(receipt.order_id)
    store.finish_submission(payload.submission_id, status="submitted", official_order_id=official_order_id)
    detail = store.record_order_lines(session_id, built, official_order_id=official_order_id)
    return SubmitOrderResponse(
        receipt=receipt,
        official_order_id=official_order_id,
        cancel_token=_cancel_token(payload.submission_id),
        session=detail,
    )


@router.delete("/cart-sessions/{session_id}/orders/{official_order_id}", response_model=CartSessionDetail)
async def cancel_order_on_nidin(
    session_id: str,
    official_order_id: str,
    request: Request,
    x_cancel_token: str | None = Header(default=None),
    requester: AuthUser | None = Depends(account_auth.current_user),
) -> CartSessionDetail:
    """點單本人（持送單時拿到的取消憑證）或主揪可以取消；實際動作是團長在 Nidin 刪掉這張跟團單。"""
    account_auth.check_csrf(request)
    session = _get_session(session_id)
    if official_order_id.startswith("head:"):
        raise HTTPException(status_code=400, detail="團長自己的飲料請到 Nidin 修改。")

    store = dependencies.get_cart_store()
    owner = _session_owner(session_id)
    submission = store.submission_for_order(session_id, official_order_id)
    token_ok = bool(
        x_cancel_token
        and submission is not None
        # 轉成 bytes 比對：compare_digest 遇到非 ASCII 的 str 會丟 TypeError
        and hmac.compare_digest(
            x_cancel_token.encode("utf-8", "surrogatepass"),
            _cancel_token(submission["submission_id"]).encode(),
        )
    )
    if not token_ok and (requester is None or requester.id != owner.id):
        raise HTTPException(status_code=403, detail="只有點這筆的人或主揪可以取消。")
    if submission is not None and submission["status"] == "cancelled":
        # 已取消過就不要再拿主揪憑證去打 Nidin
        raise HTTPException(status_code=409, detail="這筆已經取消了。")

    client = dependencies.get_nidin_client()
    try:
        head_order_id = store.nidin_order_id(session_id)
        if not head_order_id:
            head_order_id, _ = await call_with_nidin_relogin(
                owner,
                lambda token, user_id: client.fetch_head_order_id(
                    token=token, user_id=user_id, group_buy_code=session.group_buy_code
                ),
            )
            store.link_nidin_order(session_id, head_order_id)
        await call_with_nidin_relogin(
            owner,
            lambda token, user_id: client.delete_follower_order(
                token=token, user_id=user_id, head_order_id=head_order_id, follower_order_id=official_order_id
            ),
        )
    except NidinApiError as exc:
        raise HTTPException(status_code=409, detail=f"Nidin 沒有刪掉這筆：{exc}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="連不上 Nidin，這筆還沒有取消。") from exc

    if submission is not None:
        store.finish_submission(submission["submission_id"], status="cancelled")
    store.remove_official_order(session_id, official_order_id)
    try:
        return await _sync_from_nidin(store.get_session(session_id))
    except (HTTPException, NidinApiError, httpx.HTTPError):
        # 已經在 Nidin 刪掉了；同步失敗只是暫時看不到最新狀態，不影響取消結果
        return store.get_session(session_id)


@router.post("/cart-sessions/{session_id}/refresh", response_model=RefreshSessionResponse)
async def refresh_from_nidin(session_id: str, request: Request) -> RefreshSessionResponse:
    """公開頁用：以主揪憑證把 Nidin 上的跟團單撈回來。同一團 10 秒內只真的同步一次。"""
    account_auth.check_csrf(request)
    session = _get_session(session_id)
    now = time.monotonic()
    with _refresh_lock:
        last = _last_refresh.get(session_id)
        if last is not None and now - last < REFRESH_INTERVAL_SECONDS:
            return RefreshSessionResponse(session=session, synced=False)
        _last_refresh[session_id] = now
    try:
        return RefreshSessionResponse(session=await _sync_from_nidin(session), synced=True)
    except HTTPException as exc:
        return RefreshSessionResponse(session=session, synced=False, message=str(exc.detail))
    except (NidinApiError, httpx.HTTPError):
        return RefreshSessionResponse(session=session, synced=False, message="暫時連不上 Nidin，顯示的是上次同步的內容。")
