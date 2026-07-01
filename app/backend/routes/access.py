"""Shared-data access requests (Phase 3).

A non-approved user can request free access to the owner's shared market-data
keys; the owner approves/denies. An approved request grants that email shared
access (see ``key_resolver.is_shared_data_approved``). All routes require auth
(applied at the router include); owner-only routes additionally check
``is_owner``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.backend.auth import get_current_user_id
from app.backend.context import current_user_id
from app.backend.database import get_db
from app.backend.database.app_models import DEFAULT_USER_ID
from app.backend.repositories.access_request_repository import AccessRequestRepository
from app.backend.services.key_resolver import is_owner, is_shared_data_approved

router = APIRouter(prefix="/access", tags=["access"])


class AccessRequestBody(BaseModel):
    note: str | None = None


def _identity(request: Request) -> tuple[str, str | None, bool]:
    """(user_id, email, email_verified) for the request, from the middleware's
    resolved AuthResult (falls back to the context var if absent)."""
    auth = getattr(request.state, "auth", None)
    if auth is not None:
        return auth.user_id, auth.email, auth.email_verified
    return current_user_id(), None, False


def _require_owner(request: Request) -> None:
    uid, email, verified = _identity(request)
    if not is_owner(uid, email, verified):
        raise HTTPException(status_code=403, detail="Owner only.")


@router.get("/me")
async def access_me(
    request: Request,
    _user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """The current user's access status — drives the Settings UI (whether to show
    the 'request access' link, the pending state, or the owner's review panel)."""
    uid, email, verified = _identity(request)
    req = AccessRequestRepository(db).get_for_user(uid)
    return {
        "is_owner": is_owner(uid, email, verified),
        "shared_data_approved": is_shared_data_approved(uid, email, verified),
        "request_status": req.status if req is not None else None,
    }


@router.post("/request")
async def request_access(
    body: AccessRequestBody,
    request: Request,
    _user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """Create/refresh the current user's request for shared-key access."""
    uid, email, _verified = _identity(request)
    row = AccessRequestRepository(db).upsert_for_user(uid, email, body.note)
    return {"status": row.status}


@router.get("/requests")
async def list_requests(
    request: Request,
    _user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Owner-only: all access requests, newest first. The seed ``default`` row
    (the owner's own local data) is never a real requester, so it is hidden."""
    _require_owner(request)
    rows = AccessRequestRepository(db).list_all()
    return [
        {"id": r.id, "user_id": r.user_id, "email": r.email, "status": r.status, "note": r.note}
        for r in rows
        if r.user_id != DEFAULT_USER_ID
    ]


@router.post("/requests/{request_id}/{action}")
async def decide_request(
    request_id: int,
    action: str,
    request: Request,
    _user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """Owner-only: approve or deny an access request."""
    _require_owner(request)
    if action not in ("approve", "deny"):
        raise HTTPException(status_code=400, detail="action must be 'approve' or 'deny'")
    status = "approved" if action == "approve" else "denied"
    row = AccessRequestRepository(db).set_status(request_id, status)
    if row is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return {"id": row.id, "status": row.status}


@router.delete("/requests/{request_id}")
async def delete_request(
    request_id: int,
    request: Request,
    _user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    """Owner-only: remove a request row entirely. Used both to deny a pending
    request (it disappears, not kept as 'denied') and to revoke an approved
    user's shared access (they lose it and drop off the list)."""
    _require_owner(request)
    removed = AccessRequestRepository(db).delete(request_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Request not found")
    return {"deleted": True}
