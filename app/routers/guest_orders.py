import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import dependencies
from app.auth import AuthUser, require_user
from app.models import ExpectedOrderLine, GuestOrderReceipt
from app.nidin import MenuChangedError, MenuDataError

router = APIRouter()


class GuestOrderRequest(BaseModel):
    group_buy_url: str
    guest_name: str
    guest_phone: str = ""
    order_text: str
    expected_lines: list[ExpectedOrderLine] | None = Field(default=None, min_length=1)


@router.post("/guest/order-once", response_model=GuestOrderReceipt)
async def guest_order_once(
    request: GuestOrderRequest,
    user: AuthUser = Depends(require_user),
) -> GuestOrderReceipt:
    """可對任意 group_buy_url 下單，限已登入主揪使用；公開頁改走 POST /cart-sessions/{id}/orders。"""
    try:
        client = dependencies.get_nidin_client()
        expected = (
            {"expected_lines": request.expected_lines}
            if request.expected_lines is not None else {}
        )
        return await client.order_guest_once(
            group_buy_url=request.group_buy_url,
            guest_name=request.guest_name,
            guest_phone=request.guest_phone,
            order_text=request.order_text,
            **expected,
        )
    except MenuChangedError as exc:
        raise HTTPException(409, str(exc)) from exc
    except MenuDataError as exc:
        raise HTTPException(502, str(exc)) from exc
    except (httpx.NetworkError, httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
        raise HTTPException(503, "Nidin 暫時無法連線，尚未確認送單，請保留草稿。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Nidin API 回傳錯誤（HTTP {exc.response.status_code}），請稍後重試。",
        ) from exc


