"""SnapTrade read-only brokerage sync (Fidelity) — connect flow + positions.

Endpoints (all under ``/snaptrade``):
  GET    /snaptrade/status       — is the server configured, is this user connected
  POST   /snaptrade/connect      — register (once) + return a connection-portal URL
  GET    /snaptrade/portfolio    — every connected account's stock + option positions
  DELETE /snaptrade/connection   — forget this user's connection

Two gates, checked by :func:`_require_access` on every action except ``status``:
- **Configured**: dormant (503) unless ``SNAPTRADE_CLIENT_ID`` /
  ``SNAPTRADE_CONSUMER_KEY`` are set — so an un-provisioned deploy exposes nothing.
- **Approved**: when auth is on, limited to the owner + shared-data-approved users
  (reusing :func:`is_shared_data_approved`) so connections stay within SnapTrade's
  free tier. When auth is off (single-tenant local), the local owner is allowed.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from app.backend.auth import auth_enabled
from app.backend.context import (
    current_user_email,
    current_user_email_verified,
    current_user_id,
)
from app.backend.models.schemas import ErrorResponse
from app.backend.services import snaptrade_service
from app.backend.services.key_resolver import is_shared_data_approved
from app.backend.services.snaptrade_client import (
    SnapTradeAuthRequired,
    SnapTradeError,
    SnapTradeNotConfigured,
    snaptrade_configured,
)

router = APIRouter(prefix="/snaptrade", tags=["snaptrade"])
logger = logging.getLogger(__name__)

_NOT_CONFIGURED = "Brokerage sync is not configured on the server."
_NOT_APPROVED = "Your account isn't approved for brokerage sync yet."
_UPSTREAM_FAILED = "SnapTrade request failed. Please try reconnecting."


def _require_access() -> None:
    """Enforce the configured + approved gates. Raises HTTPException on failure."""
    if not snaptrade_configured():
        raise HTTPException(status_code=503, detail=_NOT_CONFIGURED)
    if auth_enabled() and not is_shared_data_approved(
        current_user_id(), current_user_email(), current_user_email_verified()
    ):
        raise HTTPException(status_code=403, detail=_NOT_APPROVED)


@router.get("/status")
async def get_status() -> dict[str, Any]:
    """Whether the feature is configured and whether this user is connected.

    Not gated by approval so the UI can decide what to show (an approved user sees
    a Connect button; a non-approved user sees why they can't)."""
    approved = not auth_enabled() or is_shared_data_approved(
        current_user_id(), current_user_email(), current_user_email_verified()
    )
    status = snaptrade_service.connection_status()
    status["approved"] = approved
    return status


@router.post(
    "/connect",
    responses={
        200: {"description": "Connection-portal URL"},
        403: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def connect(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Register the user (once) and return a one-time SnapTrade portal URL to open
    so they can link Fidelity. ``custom_redirect`` (optional) is where the portal
    returns them afterward."""
    _require_access()
    custom_redirect = payload.get("custom_redirect") if isinstance(payload, dict) else None
    try:
        url = snaptrade_service.connect_url(custom_redirect=custom_redirect)
    except SnapTradeNotConfigured:
        raise HTTPException(status_code=503, detail=_NOT_CONFIGURED)
    except (SnapTradeAuthRequired, SnapTradeError) as exc:
        logger.warning("SnapTrade connect failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail=_UPSTREAM_FAILED)
    return {"redirect_uri": url}


@router.get(
    "/portfolio",
    responses={
        200: {"description": "Connected accounts with normalized positions"},
        400: {"model": ErrorResponse, "description": "Not connected"},
        502: {"model": ErrorResponse},
    },
)
async def get_portfolio() -> dict[str, Any]:
    """Every connected account's stock and option positions, normalized."""
    _require_access()
    try:
        return snaptrade_service.fetch_portfolio()
    except LookupError:
        raise HTTPException(status_code=400, detail="Connect a brokerage first.")
    except SnapTradeNotConfigured:
        raise HTTPException(status_code=503, detail=_NOT_CONFIGURED)
    except (SnapTradeAuthRequired, SnapTradeError) as exc:
        logger.warning("SnapTrade portfolio fetch failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail=_UPSTREAM_FAILED)


@router.delete("/connection")
async def delete_connection() -> dict[str, Any]:
    """Forget this user's SnapTrade connection (local record only)."""
    _require_access()
    removed = snaptrade_service.disconnect()
    return {"disconnected": removed}
