"""Thin service that loads a user's decrypted API keys for request injection.

Used by the legacy hedge-fund LLM path; the sleeves/scan/news paths use the key
resolver (step 4). Scoped to a ``user_id`` and returns **decrypted** values, so
callers must treat the result as a secret (never log it, never return it to a
client)."""
from __future__ import annotations

from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.backend.context import DEFAULT_USER_ID
from app.backend.repositories.api_key_repository import ApiKeyRepository


class ApiKeyService:
    """Load a user's API keys (decrypted) for injecting into requests."""

    def __init__(self, db: Session, user_id: str = DEFAULT_USER_ID):
        self.repository = ApiKeyRepository(db, user_id)

    def get_api_keys_dict(self) -> Dict[str, str]:
        """All active keys for this user as ``{provider: decrypted_key}``."""
        return self.repository.decrypted_map(include_inactive=False)

    def get_api_key(self, provider: str) -> Optional[str]:
        """The decrypted key for ``provider``, or None."""
        return self.repository.get_decrypted(provider)
