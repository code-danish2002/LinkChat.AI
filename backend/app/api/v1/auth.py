from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.deps import get_auth, get_principal, guest_header
from app.api.schemas import (
    MeResponse,
    RequestCodeRequest,
    RequestCodeResponse,
    SessionResponse,
    VerifyCodeRequest,
)
from app.domain.models import Principal
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/request-code", response_model=RequestCodeResponse)
async def request_code(body: RequestCodeRequest, auth: AuthService = Depends(get_auth)):
    """Email a six-digit sign-in code. New addresses must include a display name."""
    code, raw = await auth.request_code(body.email, body.display_name)
    return RequestCodeResponse(
        sent=True,
        email=code.email,
        expires_at=code.expires_at,
        delivery=auth.email_provider,
        # Only when codes are merely logged (local development), so the UI stays usable.
        debug_code=raw if auth.reveals_codes else None,
    )


@router.post("/verify", response_model=SessionResponse)
async def verify_code(
    body: VerifyCodeRequest,
    auth: AuthService = Depends(get_auth),
    guest_id: str | None = Depends(guest_header),
):
    """Exchange the emailed code for a session token.

    Send the guest id you were using and everything it created moves into
    the account.
    """
    token, session, user = await auth.verify_code(body.email, body.code, guest_id=guest_id)
    return SessionResponse.build(token, session, user)


@router.get("/me", response_model=MeResponse)
async def me(principal: Principal = Depends(get_principal), auth: AuthService = Depends(get_auth)):
    return MeResponse.from_principal(principal, guest_ttl_hours=int(auth.guest_ttl.total_seconds() // 3600))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(principal: Principal = Depends(get_principal), auth: AuthService = Depends(get_auth)):
    await auth.logout(principal)


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(principal: Principal = Depends(get_principal), auth: AuthService = Depends(get_auth)):
    """Delete the signed-in account and everything it owns."""
    await auth.delete_account(principal)


@router.delete("/guest", status_code=status.HTTP_204_NO_CONTENT)
async def forget_guest(principal: Principal = Depends(get_principal), auth: AuthService = Depends(get_auth)):
    """Delete everything the current guest id created, right now."""
    await auth.forget_guest(principal)
