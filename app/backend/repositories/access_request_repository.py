"""Repository for shared-data access requests (Phase 3).

Users request free access to the owner's shared market-data keys; the owner
approves/denies. An approved row grants the requester's email shared-key access
(consulted by ``key_resolver.is_shared_data_approved``)."""
from __future__ import annotations

from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.backend.database.app_models import AccessRequest


class AccessRequestRepository:
    """CRUD for access requests (not user-scoped — the owner sees all)."""

    def __init__(self, db: Session):
        self.db = db

    def upsert_for_user(self, user_id: str, email: Optional[str], note: Optional[str] = None) -> AccessRequest:
        """Create or refresh this user's request. Re-requesting resets a denied
        row back to pending; an already-approved row is left approved."""
        row = self.db.query(AccessRequest).filter(AccessRequest.user_id == user_id).first()
        if row is None:
            row = AccessRequest(user_id=user_id, email=email, status="pending", note=note)
            self.db.add(row)
        else:
            row.email = email
            row.note = note
            if row.status != "approved":
                row.status = "pending"
            row.updated_at = func.now()
        self.db.commit()
        self.db.refresh(row)
        return row

    def get_for_user(self, user_id: str) -> Optional[AccessRequest]:
        return self.db.query(AccessRequest).filter(AccessRequest.user_id == user_id).first()

    def list_all(self, status: Optional[str] = None) -> List[AccessRequest]:
        q = self.db.query(AccessRequest)
        if status:
            q = q.filter(AccessRequest.status == status)
        return q.order_by(AccessRequest.created_at.desc()).all()

    def set_status(self, request_id: int, status: str) -> Optional[AccessRequest]:
        row = self.db.query(AccessRequest).filter(AccessRequest.id == request_id).first()
        if row is None:
            return None
        row.status = status
        row.updated_at = func.now()
        self.db.commit()
        self.db.refresh(row)
        return row

    def is_email_approved(self, email: str) -> bool:
        """Whether an approved request exists for ``email`` (case-insensitive)."""
        e = email.strip().lower()
        if not e:
            return False
        return (
            self.db.query(AccessRequest)
            .filter(AccessRequest.status == "approved", func.lower(AccessRequest.email) == e)
            .first()
            is not None
        )
