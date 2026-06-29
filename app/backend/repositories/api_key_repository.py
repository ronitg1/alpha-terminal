"""User-scoped, encrypted-at-rest repository for provider API keys (BYOK).

Every method is scoped to a single ``user_id`` (Phase 3), mirroring the other
multi-tenant repositories. Secrets are encrypted with Fernet on write and
decrypted on read (see ``app/backend/crypto.py``); the plaintext key never
touches the database and is only ever returned by the explicit ``*_decrypted``
methods used by the per-user key resolver — never by the API-key REST routes,
which return metadata only.
"""
from __future__ import annotations

from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.backend import crypto
from app.backend.context import DEFAULT_USER_ID
from app.backend.database.models import ApiKey


class ApiKeyRepository:
    """CRUD for one user's provider API keys."""

    def __init__(self, db: Session, user_id: str = DEFAULT_USER_ID):
        self.db = db
        self.user_id = user_id

    def _scoped(self):
        return self.db.query(ApiKey).filter(ApiKey.user_id == self.user_id)

    def set_key(
        self,
        provider: str,
        key_value: str,
        description: Optional[str] = None,
        is_active: bool = True,
    ) -> ApiKey:
        """Create or replace this user's key for ``provider`` (encrypts at rest)."""
        encrypted = crypto.encrypt(key_value)
        row = self._scoped().filter(ApiKey.provider == provider).first()
        if row is not None:
            row.key_value = encrypted
            row.description = description
            row.is_active = is_active
            row.updated_at = func.now()
        else:
            row = ApiKey(
                user_id=self.user_id,
                provider=provider,
                key_value=encrypted,
                description=description,
                is_active=is_active,
            )
            self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def get_row(self, provider: str, *, active_only: bool = True) -> Optional[ApiKey]:
        """The raw row (encrypted value) for ``provider``, or None."""
        q = self._scoped().filter(ApiKey.provider == provider)
        if active_only:
            q = q.filter(ApiKey.is_active.is_(True))
        return q.first()

    def get_decrypted(self, provider: str, *, active_only: bool = True) -> Optional[str]:
        """The decrypted secret for ``provider``, or None if not stored."""
        row = self.get_row(provider, active_only=active_only)
        return crypto.decrypt(row.key_value) if row is not None else None

    def list_keys(self, include_inactive: bool = False) -> List[ApiKey]:
        """All of this user's key rows (encrypted values), ordered by provider."""
        q = self._scoped()
        if not include_inactive:
            q = q.filter(ApiKey.is_active.is_(True))
        return q.order_by(ApiKey.provider).all()

    def decrypted_map(self, include_inactive: bool = False) -> dict[str, str]:
        """{provider: decrypted_key} for this user — for the key resolver."""
        return {row.provider: crypto.decrypt(row.key_value) for row in self.list_keys(include_inactive)}

    def delete(self, provider: str) -> bool:
        """Delete this user's key for ``provider``. True if a row was removed."""
        row = self._scoped().filter(ApiKey.provider == provider).first()
        if row is None:
            return False
        self.db.delete(row)
        self.db.commit()
        return True

    def update_last_used(self, provider: str) -> bool:
        """Stamp ``last_used`` for ``provider``. True if a row was updated."""
        row = self.get_row(provider, active_only=True)
        if row is None:
            return False
        row.last_used = func.now()
        self.db.commit()
        return True
