"""點單即送進平台（Nidin／Ocard）：平台上的團才是真相，本站只是替大家操作、再把狀態撈回來顯示。

- 送單：每人一張跟團單（訪客身分、名字＝點單人），送成功才寫本站並標上平台單號。
- 取消：Nidin 由本站拿主揪憑證以團長身分刪單；Ocard 用送單時那張單專屬的裝置權杖退團。
- 刷新：公開頁定期請後端同步，大家看到的是平台上的實際內容。

平台各自怎麼做在 app/platforms.py；這裡只放本站自己的規則（防重送、取消憑證、節流、對帳）。
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
from app.models import CartSessionDetail, GuestOrderReceipt
from app.nidin.builder import MenuChangedError, MenuDataError
from app.nidin.client import NidinApiError
from app.nidin.normalizer import _resolve_guest_phone
from app.ocard import OcardApiError
from app.platforms import Platform, platform_for_session, session_owner_user
from app.routers.cart_sessions import AddCartLineDraftRequest

router = APIRouter()

REFRESH_INTERVAL_SECONDS = 10.0
# 結果不明的送單超過這個時間、同步後在平台上仍找不到，就判定沒成立
UNCERTAIN_GRACE_SECONDS = 60.0
# 公開、免登入就能真的在主揪的團下單；同一個來源 IP 10 分鐘最多 60 筆（辦公室共用 NAT 也夠用）
SUBMIT_WINDOW_SECONDS = 600.0
SUBMIT_LIMIT_PER_IP = 60
_last_refresh: dict[str, float] = {}
_refresh_lock = Lock()
_submits_by_ip: dict[str, deque[float]] = {}
_submit_lock = Lock()


class SubmitOrderRequest(BaseModel):
    # 前端每按一次「送出」產生一個；逾時重試時沿用同一個，後端才認得出是同一筆
    submission_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    # Nidin 顯示時會截到 20 字；Ocard 沒說上限，以同樣長度為準
    contributor_name: str = Field(min_length=1, max_length=20)
    # 選填：給團長聯絡用。沒填就不送（Nidin 官網也是如此），不再替人亂編號碼
    contributor_phone: str = Field(default="", max_length=20)
    lines: list[AddCartLineDraftRequest] = Field(min_length=1, max_length=20)

    @field_validator("contributor_name")
    @classmethod
    def require_real_name(cls, value: str) -> str:
        value = value.strip()
        # 名字就是平台上這張跟團單的身分，也是本站收款、取消的依據；「訪客」會讓大家撞在一起
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


def _not_sent_message(exc: BaseException, label: str) -> tuple[int, str]:
    if isinstance(exc, (NidinApiError, OcardApiError)):
        return 409, f"{label} 拒絕了這筆：{exc}"
    if isinstance(exc, (MenuChangedError, MenuDataError)):
        return 409, str(exc)
    if isinstance(exc, HTTPException):
        return exc.status_code, str(exc.detail)
    if isinstance(exc, httpx.HTTPStatusError):
        return 502, f"{label} 回傳錯誤（HTTP {exc.response.status_code}），這筆沒有送出。"
    if isinstance(exc, httpx.HTTPError):
        return 503, f"連不上 {label}，這筆還沒有送出。"
    if isinstance(exc, ValueError):
        return 422, str(exc)
    return 502, "送單前發生錯誤，這筆沒有送出。"


def _cancel_token(submission_id: str) -> str:
    """由伺服器金鑰衍生，不必存；同一個 submission_id 重送時可以原樣再給一次。"""
    data_dir = dependencies.get_cart_store()._db_path.parent
    secret = credential_crypto.derive_secret(data_dir, "order-cancel-token")
    return hmac.new(secret, submission_id.encode(), hashlib.sha256).hexdigest()


def _get_session(session_id: str) -> CartSessionDetail:
    try:
        return dependencies.get_cart_store().get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="找不到這個團購。") from exc


async def _sync_from_platform(session: CartSessionDetail, platform: Platform) -> CartSessionDetail:
    store = dependencies.get_cart_store()
    official = await platform.fetch_official(session)
    detail = store.merge_official_orders_to_lines(session.session_id, official)
    store.reconcile_submissions(
        session.session_id,
        {str(order.order_id) for order in official.follower_orders},
        grace_seconds=UNCERTAIN_GRACE_SECONDS,
    )
    return detail


@router.post("/cart-sessions/{session_id}/orders", response_model=SubmitOrderResponse)
async def submit_order_to_platform(
    session_id: str, payload: SubmitOrderRequest, request: Request
) -> SubmitOrderResponse:
    account_auth.check_csrf(request)
    session = _get_session(session_id)
    store = dependencies.get_cart_store()
    platform = platform_for_session(session)

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
            detail=f"上一筆還在跟 {platform.label} 確認中。請等一分鐘後按「重新整理」，確認 {platform.label} 上有沒有這筆再決定要不要重送。",
        )
    _limit_submissions(request)

    def fail(exc: BaseException) -> HTTPException:
        status_code, message = _not_sent_message(exc, platform.label)
        store.finish_submission(payload.submission_id, status="failed", message=message)
        return HTTPException(status_code=status_code, detail=message)

    # 送出前：這一段任何失敗都確定沒到平台，標 failed 讓同一個 id 可以重試
    try:
        await platform.ensure_open(session)
        menu = await platform.fetch_menu(session)
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
        placed = await platform.submit(
            session,
            name=payload.contributor_name,
            phone=payload.contributor_phone if platform.accepts_phone else "",
            lines=[line for line, _ in built],
            menu=menu,
            progress=progress,
        )
    except Exception as exc:
        if progress.get("stage") != "commit" or platform.definitely_not_sent(exc):
            raise fail(exc) from exc
        # 已送出最後一步卻沒拿到可信結果：平台上可能已成立。記下跟團單號，之後同步時對帳
        pending_order_id = progress.get("cart_order_id")
        store.mark_submission_uncertain(payload.submission_id, str(pending_order_id) if pending_order_id else None)
        raise HTTPException(
            status_code=503,
            detail=f"送出後沒有收到 {platform.label} 的確定結果，這筆可能已經成立。請等一分鐘後按「重新整理」確認，不要直接重點。",
        ) from exc

    official_order_id = placed.official_order_id
    store.finish_submission(
        payload.submission_id, status="submitted", official_order_id=official_order_id, cancel_ref=placed.cancel_ref
    )
    detail = store.record_order_lines(session_id, built, official_order_id=official_order_id)
    return SubmitOrderResponse(
        receipt=placed.receipt,
        official_order_id=official_order_id,
        cancel_token=_cancel_token(payload.submission_id),
        session=detail,
    )


@router.delete("/cart-sessions/{session_id}/orders/{official_order_id}", response_model=CartSessionDetail)
async def cancel_order_on_platform(
    session_id: str,
    official_order_id: str,
    request: Request,
    x_cancel_token: str | None = Header(default=None),
    requester: AuthUser | None = Depends(account_auth.current_user),
) -> CartSessionDetail:
    """點單本人（持送單時拿到的取消憑證）或主揪可以取消；實際怎麼刪看平台。"""
    account_auth.check_csrf(request)
    session = _get_session(session_id)
    platform = platform_for_session(session)
    if official_order_id.startswith("head:"):
        raise HTTPException(status_code=400, detail=f"團長自己的飲料請到 {platform.label} 修改。")

    store = dependencies.get_cart_store()
    owner = session_owner_user(session_id, platform.label)
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
        # 已取消過就不要再拿主揪憑證去打平台
        raise HTTPException(status_code=409, detail="這筆已經取消了。")

    try:
        await platform.cancel(session, official_order_id, submission.get("cancel_ref") if submission else None)
    except (NidinApiError, OcardApiError) as exc:
        raise HTTPException(status_code=409, detail=f"{platform.label} 沒有刪掉這筆：{exc}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"連不上 {platform.label}，這筆還沒有取消。") from exc

    if submission is not None:
        store.finish_submission(submission["submission_id"], status="cancelled")
    store.remove_official_order(session_id, official_order_id)
    try:
        return await _sync_from_platform(store.get_session(session_id), platform)
    except (HTTPException, NidinApiError, OcardApiError, httpx.HTTPError):
        # 已經在平台上刪掉了；同步失敗只是暫時看不到最新狀態，不影響取消結果
        return store.get_session(session_id)


@router.post("/cart-sessions/{session_id}/refresh", response_model=RefreshSessionResponse)
async def refresh_from_platform(session_id: str, request: Request) -> RefreshSessionResponse:
    """公開頁用：把平台上的跟團單撈回來。同一團 10 秒內只真的同步一次。"""
    account_auth.check_csrf(request)
    session = _get_session(session_id)
    platform = platform_for_session(session)
    now = time.monotonic()
    with _refresh_lock:
        last = _last_refresh.get(session_id)
        if last is not None and now - last < REFRESH_INTERVAL_SECONDS:
            return RefreshSessionResponse(session=session, synced=False)
        _last_refresh[session_id] = now
    try:
        return RefreshSessionResponse(session=await _sync_from_platform(session, platform), synced=True)
    except HTTPException as exc:
        return RefreshSessionResponse(session=session, synced=False, message=str(exc.detail))
    except (NidinApiError, OcardApiError, httpx.HTTPError):
        return RefreshSessionResponse(
            session=session, synced=False, message=f"暫時連不上 {platform.label}，顯示的是上次同步的內容。"
        )
