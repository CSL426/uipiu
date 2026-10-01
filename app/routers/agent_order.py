from fastapi import APIRouter, Depends, HTTPException, Request

from app import dependencies
from app.auth import AuthUser, check_csrf, current_user
from app.deps_shared import initialize_legacy_ownership
from app.models import (
    AgentOrderSessionRequest,
    AgentOrderSessionResponse,
    AgentParsedItemPreview,
)

router = APIRouter()


@router.post("/cart-sessions/{session_id}/agent-order", response_model=AgentOrderSessionResponse)
async def agent_order_for_session(
    session_id: str,
    request: AgentOrderSessionRequest,
    http_request: Request,
    user: AuthUser | None = Depends(current_user),
) -> AgentOrderSessionResponse:
    store = dependencies.get_cart_store()
    try:
        session = store.get_session(session_id)
        menu = store.get_menu(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    parser = dependencies.get_agent_parser()
    decision = await parser.parse(request.text, menu)

    if decision.action == "add_to_cart" and decision.items:
        contributor_name = request.contributor_name.strip() or decision.items[0].customer_name or "訪客"

        # 計算解析預覽明細（含品名、選項名稱、金額）
        item_map = {it.id: it for it in menu.items}
        option_lookup: dict[str, tuple[str, int]] = {
            opt.id: (opt.name, opt.price)
            for g in menu.groups
            for opt in g.options
        }

        preview_items: list[AgentParsedItemPreview] = []
        grand_total = 0

        for it in decision.items:
            m_item = item_map.get(it.item_id)
            base_price = m_item.price if m_item else 0
            opt_names: list[str] = []
            opt_total = 0
            for oid in it.selection_option_ids:
                if oid in option_lookup:
                    name, price = option_lookup[oid]
                    opt_names.append(name if price == 0 else f"{name} (+${price})")
                    opt_total += price

            unit_price = base_price + opt_total
            line_total = unit_price * it.quantity
            grand_total += line_total
            preview_items.append(
                AgentParsedItemPreview(
                    item_id=it.item_id,
                    item_name=it.item_name,
                    quantity=it.quantity,
                    selection_option_ids=it.selection_option_ids,
                    selection_option_names=opt_names or it.selection_option_names,
                    unit_price=unit_price,
                    line_total=line_total,
                )
            )

        if request.dry_run:
            return AgentOrderSessionResponse(
                action="preview",
                reply_message=decision.reply_message or "已為您解析點單，請確認品項與規格：",
                items_added=0,
                parsed_items=preview_items,
                total_price=grand_total,
                contributor_name=contributor_name,
                cart_session=session,
            )

        # 直接寫本站會繞過 Nidin；一般人只拿預覽，確認後走 POST /cart-sessions/{id}/orders 送進 Nidin
        check_csrf(http_request)
        initialize_legacy_ownership()
        if user is None or store.session_owner(session_id) != user.id:
            raise HTTPException(status_code=403, detail="請先預覽，確認後再送出到 Nidin。")

        lines_payload = [
            {
                "item_id": it.item_id,
                "quantity": it.quantity,
                "selection_option_ids": it.selection_option_ids,
            }
            for it in decision.items
        ]
        updated_session = store.add_lines(
            session_id,
            contributor_name=contributor_name,
            lines=lines_payload,
        )
        return AgentOrderSessionResponse(
            action="add_to_cart",
            reply_message=decision.reply_message or f"已成功將 {len(decision.items)} 筆品項加入購物車！",
            items_added=len(decision.items),
            parsed_items=preview_items,
            total_price=grand_total,
            contributor_name=contributor_name,
            cart_session=updated_session,
        )

    # Clarification or conversational reply
    return AgentOrderSessionResponse(
        action="reply",
        reply_message=decision.reply_message or "請確認您的點單內容或規格選項。",
        items_added=0,
        parsed_items=[],
        total_price=0,
        contributor_name=request.contributor_name.strip() or "訪客",
        cart_session=session,
    )
