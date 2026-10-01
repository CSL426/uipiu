from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import dependencies
from app.models import NormalizedMenu, ParsedOrder, StoreDeliverySummary
from app.nidin import extract_store_id, normalize_menu
from app.parser import parse_order_text

router = APIRouter()


class ResolveStoreRequest(BaseModel):
    menu_url: str


class ResolveStoreResponse(BaseModel):
    store_id: str


class ParseOrderRequest(BaseModel):
    text: str
    menu: dict[str, Any]


@router.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/resolve-store", response_model=ResolveStoreResponse)
async def resolve_store(request: ResolveStoreRequest) -> ResolveStoreResponse:
    try:
        store_id = extract_store_id(request.menu_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return ResolveStoreResponse(store_id=store_id)


@router.get("/menus/{store_id}", response_model=NormalizedMenu)
async def get_menu(store_id: str) -> NormalizedMenu:
    client = dependencies.get_nidin_client()
    return await client.fetch_menu(store_id)


@router.get("/stores/{store_id}/delivery-summary", response_model=StoreDeliverySummary)
async def get_store_delivery_summary(store_id: str) -> StoreDeliverySummary:
    client = dependencies.get_nidin_client()
    return await client.fetch_store_delivery_summary(store_id)


@router.post("/parse-order", response_model=ParsedOrder)
async def parse_order(request: ParseOrderRequest) -> ParsedOrder:
    try:
        menu = normalize_menu(request.menu)
        return parse_order_text(request.text, menu)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
