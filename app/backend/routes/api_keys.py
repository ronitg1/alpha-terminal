"""Per-user BYOK API-key management (Phase 3).

Every route is scoped to the authenticated user (``get_current_user_id``). Keys
are validated with a live provider call before being stored (so a bad key fails
at save time), encrypted at rest, and the plaintext value is **never** returned
to the client — responses carry metadata only (provider, presence, timestamps).
"""
from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.backend.auth import get_current_user_id
from app.backend.crypto import EncryptionNotConfigured
from app.backend.database import get_db
from app.backend.models.schemas import (
    ApiKeyCreateRequest,
    ApiKeySummaryResponse,
    ErrorResponse,
)
from app.backend.repositories.api_key_repository import ApiKeyRepository
from app.backend.services.api_key_validation import (
    KeyValidationError,
    KeyValidationUnavailable,
    is_known_provider,
    validate_provider_key,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


@router.post(
    "/",
    response_model=ApiKeySummaryResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Unknown provider or invalid key"},
        503: {"model": ErrorResponse, "description": "Provider unavailable; try again"},
        500: {"model": ErrorResponse, "description": "Server misconfiguration"},
    },
)
async def upsert_api_key(
    request: ApiKeyCreateRequest,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """Validate, encrypt, and store the current user's key for a provider.

    The provider must be one of the known BYOK providers, and the key is checked
    against the provider before it is saved. The response never includes the key
    value."""
    provider = request.provider.strip().lower()
    if not is_known_provider(provider):
        raise HTTPException(status_code=400, detail=f"Unknown provider '{provider}'.")

    try:
        validate_provider_key(provider, request.key_value)
    except KeyValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except KeyValidationUnavailable as exc:
        # The provider couldn't confirm the key right now — don't tell the user
        # their (possibly valid) key is bad; ask them to retry.
        raise HTTPException(status_code=503, detail=str(exc))

    try:
        row = ApiKeyRepository(db, user_id).set_key(
            provider=provider,
            key_value=request.key_value,
            description=request.description,
            is_active=request.is_active,
        )
    except EncryptionNotConfigured as exc:
        logger.error("Cannot store API key: %s", exc)
        raise HTTPException(status_code=500, detail="Server is not configured to store secrets.")
    return ApiKeySummaryResponse.model_validate(row)


@router.get(
    "/",
    response_model=List[ApiKeySummaryResponse],
    responses={500: {"model": ErrorResponse, "description": "Internal server error"}},
)
async def list_api_keys(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """List the current user's stored keys (metadata only — no key values)."""
    rows = ApiKeyRepository(db, user_id).list_keys(include_inactive=include_inactive)
    return [ApiKeySummaryResponse.model_validate(r) for r in rows]


@router.get(
    "/{provider}",
    response_model=ApiKeySummaryResponse,
    responses={404: {"model": ErrorResponse, "description": "API key not found"}},
)
async def get_api_key(
    provider: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """Return metadata for the current user's key for ``provider`` (no value)."""
    row = ApiKeyRepository(db, user_id).get_row(provider.strip().lower(), active_only=False)
    if row is None:
        raise HTTPException(status_code=404, detail="API key not found")
    return ApiKeySummaryResponse.model_validate(row)


@router.delete(
    "/{provider}",
    responses={
        204: {"description": "API key deleted"},
        404: {"model": ErrorResponse, "description": "API key not found"},
    },
)
async def delete_api_key(
    provider: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    """Delete the current user's key for ``provider``."""
    if not ApiKeyRepository(db, user_id).delete(provider.strip().lower()):
        raise HTTPException(status_code=404, detail="API key not found")
    return {"message": "API key deleted"}
