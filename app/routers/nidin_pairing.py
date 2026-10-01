"""Nidin 官方憑證一次性配對 API 路由。"""

from fastapi import APIRouter, Depends, Request, Response

from app import auth as account_auth
from app import nidin_pairing
from app.auth import (
    AuthUser,
    normalize_request_origin,
    require_session,
)
from app.deps_shared import initialize_legacy_ownership
from app.models import (
    LeaderAuthStatus,
    PairingCancelRequest,
    PairingConfirmRequest,
    PairingInitResponse,
    PairingLeaseRequest,
    PairingLeaseResponse,
)

router = APIRouter()


@router.post("/leader/auth/pairings", response_model=PairingInitResponse)
async def create_pairing(
    request: Request,
    response: Response,
    session: tuple[AuthUser, str] = Depends(require_session),
) -> PairingInitResponse:
    initialize_legacy_ownership()
    user, session_token_hash = session
    origin = normalize_request_origin(request)

    flow_id, state, expires_at, revision = nidin_pairing.init_pairing(
        user_id=user.id,
        username=user.username,
        session_token_hash=session_token_hash,
        origin=origin,
    )

    response.headers["Cache-Control"] = "no-store"
    return PairingInitResponse(
        flow_id=flow_id,
        state=state,
        expires_at=expires_at,
        user_summary=user.username,
        credential_revision=revision,
    )


@router.post("/leader/auth/pairings/{flow_id}/lease", response_model=PairingLeaseResponse)
async def renew_pairing_lease(
    flow_id: str,
    payload: PairingLeaseRequest,
    request: Request,
    response: Response,
    session: tuple[AuthUser, str] = Depends(require_session),
) -> PairingLeaseResponse:
    initialize_legacy_ownership()
    user, session_token_hash = session
    origin = normalize_request_origin(request)

    lease_expires_at = nidin_pairing.grant_lease(
        flow_id=flow_id,
        user_id=user.id,
        session_token_hash=session_token_hash,
        origin=origin,
        state=payload.state,
    )

    response.headers["Cache-Control"] = "no-store"
    return PairingLeaseResponse(
        status="pending",
        lease_expires_at=lease_expires_at,
    )


@router.post("/leader/auth/pairings/{flow_id}/confirm", response_model=LeaderAuthStatus)
async def confirm_pairing(
    flow_id: str,
    payload: PairingConfirmRequest,
    request: Request,
    response: Response,
    session: tuple[AuthUser, str] = Depends(require_session),
) -> LeaderAuthStatus:
    initialize_legacy_ownership()
    user, session_token_hash = session
    origin = normalize_request_origin(request)

    masked_token, nidin_user_id = nidin_pairing.confirm_pairing(
        flow_id=flow_id,
        user_id=user.id,
        session_token_hash=session_token_hash,
        origin=origin,
        state=payload.state,
        token=payload.token,
        nidin_user_id=payload.user_id,
    )

    response.headers["Cache-Control"] = "no-store"
    return LeaderAuthStatus(
        is_authenticated=True,
        user_id=nidin_user_id,
        masked_token=masked_token,
    )


@router.post("/leader/auth/pairings/{flow_id}/cancel")
async def cancel_pairing(
    flow_id: str,
    payload: PairingCancelRequest,
    request: Request,
    response: Response,
    session: tuple[AuthUser, str] = Depends(require_session),
) -> dict[str, bool]:
    initialize_legacy_ownership()
    user, session_token_hash = session
    origin = normalize_request_origin(request)

    nidin_pairing.cancel_pairing(
        flow_id=flow_id,
        user_id=user.id,
        session_token_hash=session_token_hash,
        origin=origin,
        state=payload.state,
    )

    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}
