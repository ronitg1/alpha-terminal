"""Auth introspection route.

A single endpoint, ``GET /auth/me``, that returns the resolved user id for the
request. It is the smallest real consumer of :func:`get_current_user_id`, so it
both proves the dependency end-to-end (401 without a valid token when auth is on;
the ``default`` user when auth is off) and gives the frontend a cheap way to
confirm its token is accepted and learn its own user id.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.backend.auth import auth_enabled, get_current_user_id

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me")
async def get_me(user_id: str = Depends(get_current_user_id)) -> dict:
    """Return the authenticated user's id and whether auth is enforced."""
    return {"user_id": user_id, "auth_enabled": auth_enabled()}
