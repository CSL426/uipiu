import asyncio
from datetime import datetime, timezone
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
import httpx
from pydantic import BaseModel, Field

from app import dependencies
from app.auth import AuthUser, require_user
from app.deps_shared import (
    build_default_session_name,
    fetch_current_menu,
    initialize_legacy_ownership,
    require_session_owner,
)
from app.models import (
    CartSessionDetail,
    CartSessionMenu,
    CartSessionSummary,
    SubmitCartSessionOrderRequest,
    SubmitCartSessionOrderResponse,
)
from app.nidin import (
    MenuChangedError,
    MenuDataError,
    extract_group_buy_target,
    validate_cart_lines,
)

router = APIRouter()


class PaymentRequest(BaseModel):
    contributor_name: str = Field(min_length=1, max_length=200)
    paid: bool


class CreateCartSessionRequest(BaseModel):
    group_buy_url: str
    session_name: str = ""


class AddCartLineDraftRequest(BaseModel):
    item_id: str
    quantity: int = 1
    selection_option_ids: list[str] = Field(default_factory=list)


class AddCartSessionLinesRequest(BaseModel):
    contributor_name: str
    lines: list[AddCartLineDraftRequest]


@router.get("/admin/cart-sessions", response_model=list[CartSessionSummary])
async def list_owned_cart_sessions(
    user: AuthUser = Depends(require_user),
) -> list[CartSessionSummary]:
    initialize_legacy_ownership()
    return dependencies.get_cart_store().list_owned_sessions(user.id)


@router.get("/cart-sessions/{session_id}/payments")
async def get_payments(
    session_id: str,
    user: AuthUser = Depends(require_session_owner),
) -> dict[str, bool]:
    return dependencies.get_cart_store().get_payments(session_id)


@router.put("/cart-sessions/{session_id}/payments")
async def set_payment(
    session_id: str,
    request: PaymentRequest,
    user: AuthUser = Depends(require_session_owner),
) -> dict[str, bool]:
    try:
        return dependencies.get_cart_store().set_payment(
            session_id, request.contributor_name, request.paid,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/cart-sessions", response_model=list[CartSessionSummary])
async def list_cart_sessions() -> list[CartSessionSummary]:
    return dependencies.get_cart_store().list_sessions()


async def create_session_from_group_buy_url(
    group_buy_url: str, session_name: str, owner_id: str
) -> CartSessionSummary:
    """後台貼連結開團與「從 Nidin 匯入」共用；同一個團已存在就回傳既有的。"""
    target = extract_group_buy_target(group_buy_url)
    client = dependencies.get_nidin_client()
    group_buy_brief, menu, delivery_summary = await asyncio.gather(
        client.fetch_group_buy_brief(target.group_buy_code),
        client.fetch_menu(target.store_id),
        client.fetch_store_delivery_summary(target.store_id),
    )
    return dependencies.get_cart_store().create_or_get_session(
        group_buy_url=group_buy_url,
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


@router.post("/cart-sessions", response_model=CartSessionSummary)
async def create_cart_session(
    request: CreateCartSessionRequest,
    user: AuthUser = Depends(require_user),
) -> CartSessionSummary:
    initialize_legacy_ownership()
    try:
        return await create_session_from_group_buy_url(
            request.group_buy_url, request.session_name, user.id
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/cart-sessions/{session_id}", response_model=CartSessionDetail)
async def get_cart_session(session_id: str) -> CartSessionDetail:
    try:
        return dependencies.get_cart_store().get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/cart-sessions/{session_id}/menu", response_model=CartSessionMenu)
async def get_cart_session_menu(session_id: str) -> CartSessionMenu:
    store = dependencies.get_cart_store()
    try:
        session = store.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    try:
        menu = await fetch_current_menu(dependencies.get_nidin_client(), session.store_id)
    except HTTPException as exc:
        if exc.status_code != 503:
            raise
        try:
            return store.get_menu(session_id)
        except (ValueError, KeyError) as cache_exc:
            raise HTTPException(
                503, "Nidin 暫時無法連線，且沒有可用的上次菜單。請稍後重試。",
            ) from cache_exc
    try:
        return store.replace_menu(session_id, menu)
    except sqlite3.OperationalError:
        return CartSessionMenu(
            **menu.model_dump(), menu_source="live",
            menu_fetched_at=datetime.now(timezone.utc).isoformat(),
        )


@router.post("/cart-sessions/{session_id}/lines", response_model=CartSessionDetail)
async def add_cart_session_lines(
    session_id: str,
    request: AddCartSessionLinesRequest,
    user: AuthUser = Depends(require_session_owner),
) -> CartSessionDetail:
    """只寫本站、不送 Nidin，限主揪手動補登。一般點單走 POST /cart-sessions/{id}/orders。"""
    try:
        return dependencies.get_cart_store().add_lines(
            session_id,
            contributor_name=request.contributor_name,
            lines=[line.model_dump() for line in request.lines],
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/cart-sessions/{session_id}/lines", response_model=CartSessionDetail)
async def clear_cart_session_lines(
    session_id: str,
    user: AuthUser = Depends(require_session_owner),
) -> CartSessionDetail:
    try:
        return dependencies.get_cart_store().clear_lines(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/cart-sessions/{session_id}/submit-guest-order", response_model=SubmitCartSessionOrderResponse)
async def submit_cart_session_guest_order(
    session_id: str,
    request: SubmitCartSessionOrderRequest,
    user: AuthUser = Depends(require_session_owner),
) -> SubmitCartSessionOrderResponse:
    try:
        store = dependencies.get_cart_store()
        session = store.get_session(session_id)
        # 已經在 Nidin 上的（同步回來或新流程送出的）不能再送一次
        local_lines = [line for line in session.lines if line.official_order_id is None]
        if not local_lines:
            raise ValueError("沒有只存在本站、需要送出的品項。")
        session = session.model_copy(update={"lines": local_lines})

        client = dependencies.get_nidin_client()
        menu = await fetch_current_menu(client, session.store_id)
        validate_cart_lines(session.lines, menu)
        store.replace_menu(session_id, menu)
        receipt = await client.order_guest_cart_session(
            group_buy_url=session.group_buy_url,
            guest_name=request.guest_name,
            guest_phone=request.guest_phone,
            lines=session.lines,
            menu=menu,
        )
        cleared_session = store.clear_submitted_lines(
            session_id, [line.line_id for line in session.lines],
        )
        return SubmitCartSessionOrderResponse(
            receipt=receipt,
            cart_session=cleared_session,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MenuChangedError as exc:
        raise HTTPException(409, str(exc)) from exc
    except MenuDataError as exc:
        raise HTTPException(502, str(exc)) from exc
    except (httpx.NetworkError, httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
        raise HTTPException(503, "Nidin 暫時無法連線，尚未確認送單，購物車已保留。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Nidin API 回傳錯誤（HTTP {exc.response.status_code}），請稍後重試。",
        ) from exc
