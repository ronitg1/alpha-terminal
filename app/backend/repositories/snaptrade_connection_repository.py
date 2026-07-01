"""DB persistence for a user's SnapTrade connection (read-only brokerage sync).

One row per user, scoped to ``self.user_id`` like the other repositories. The
SnapTrade ``user_secret`` is a bearer-equivalent secret, so it is encrypted with
Fernet on write and decrypted only by the explicit :meth:`get_secret` path (never
by :meth:`get_metadata`, which a REST route may surface). Mirrors
``ApiKeyRepository``'s encrypt-at-rest handling.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.backend import crypto
from app.backend.database.app_models import DEFAULT_USER_ID, SnapTradeConnection


class SnapTradeConnectionRepository:
    """CRUD for one user's SnapTrade connection."""

    def __init__(self, db: Session, user_id: str = DEFAULT_USER_ID):
        self.db = db
        self.user_id = user_id

    def _row(self) -> Optional[SnapTradeConnection]:
        return (
            self.db.query(SnapTradeConnection)
            .filter(SnapTradeConnection.user_id == self.user_id)
            .first()
        )

    def get_metadata(self) -> Optional[dict[str, Any]]:
        """Non-secret connection info, or None if this user hasn't registered.
        Never includes the decrypted ``user_secret``."""
        row = self._row()
        if row is None:
            return None
        return {
            "snaptrade_user_id": row.snaptrade_user_id,
            "connected": True,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    def get_secret(self) -> Optional[tuple[str, str]]:
        """``(snaptrade_user_id, decrypted_user_secret)`` for API calls, or None."""
        row = self._row()
        if row is None:
            return None
        return row.snaptrade_user_id, crypto.decrypt(row.user_secret)

    def save(self, snaptrade_user_id: str, user_secret: str) -> dict[str, Any]:
        """Create or replace this user's connection (encrypts the secret)."""
        encrypted = crypto.encrypt(user_secret)
        row = self._row()
        if row is not None:
            row.snaptrade_user_id = snaptrade_user_id
            row.user_secret = encrypted
            row.updated_at = func.now()
        else:
            row = SnapTradeConnection(
                user_id=self.user_id,
                snaptrade_user_id=snaptrade_user_id,
                user_secret=encrypted,
            )
            self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return self.get_metadata() or {}

    def delete(self) -> bool:
        """Forget this user's connection. True if a row was removed."""
        row = self._row()
        if row is None:
            return False
        self.db.delete(row)
        self.db.commit()
        return True
