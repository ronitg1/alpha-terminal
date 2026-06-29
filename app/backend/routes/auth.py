"""Auth introspection routes.

``GET /auth/me`` returns the resolved user id for the request — the smallest real
consumer of :func:`get_current_user_id`, so it proves the dependency end-to-end
(401 without a valid token when auth is on; the ``default`` user when auth is off)
and gives the frontend a cheap way to confirm its token is accepted and learn its
own user id. It also carries the per-user ``onboarding_completed`` flag so the
first-login walkthrough shows exactly once per account.

``POST /auth/onboarding-complete`` records that the user has finished or skipped
the walkthrough.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.backend.auth import auth_enabled, get_current_user_id
from app.backend.services import user_settings_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me")
async def get_me(user_id: str = Depends(get_current_user_id)) -> dict:
    """Return the user's id, whether auth is enforced, and onboarding status."""
    return {
        "user_id": user_id,
        "auth_enabled": auth_enabled(),
        "onboarding_completed": user_settings_service.get_onboarding_completed(),
    }


@router.post("/onboarding-complete")
async def complete_onboarding(user_id: str = Depends(get_current_user_id)) -> dict:
    """Mark the first-login walkthrough as finished for the current user."""
    user_settings_service.set_onboarding_completed(True)
    return {"ok": True, "onboarding_completed": True}
