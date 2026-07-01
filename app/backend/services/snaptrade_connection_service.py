"""Per-user SnapTrade connection storage (file + DB backends).

Stores one connection per user — the ``snaptrade_user_id`` we registered plus the
``user_secret`` SnapTrade issued (a bearer-equivalent secret). Mirrors the
storage-seam pattern of the other services (see
:mod:`app.backend.services._storage`): ``db`` dispatches to
:class:`SnapTradeConnectionRepository` (always Fernet-encrypted at rest); ``file``
keeps a small JSON file for local dev.

Secret-at-rest policy:
- **DB backend** (cloud): always encrypted — the deploy always has
  ``API_KEY_ENCRYPTION_KEY``.
- **File backend** (local single-tenant): encrypted when
  ``API_KEY_ENCRYPTION_KEY`` is set; otherwise stored as plaintext on the user's
  own machine with an ``encrypted: false`` marker and a one-time warning, so local
  testing doesn't require configuring an encryption key.

Both backends expose the same two reads: :func:`get_status` (non-secret metadata,
safe for a REST route) and :func:`get_credentials` (the decrypted
``(snaptrade_user_id, user_secret)`` for signing API calls).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from app.backend import crypto
from app.backend.repositories.snaptrade_connection_repository import (
    SnapTradeConnectionRepository,
)
from app.backend.services._storage import current_user_id, session_scope, use_db

logger = logging.getLogger(__name__)

# app/data/snaptrade_connections.json  (parents[2] == the app/ dir)
_STORE_PATH = Path(__file__).resolve().parents[2] / "data" / "snaptrade_connections.json"

_warned_plaintext = False


# ─── file-backend helpers ────────────────────────────────────────────────────

def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _encrypt_for_file(secret: str) -> tuple[str, bool]:
    """Encrypt for the file backend if a key is configured, else keep plaintext
    (local single-tenant). Returns ``(stored_value, was_encrypted)``."""
    global _warned_plaintext
    if crypto.encryption_configured():
        return crypto.encrypt(secret), True
    if not _warned_plaintext:
        logger.warning(
            "Storing the SnapTrade user secret as PLAINTEXT in %s because "
            "API_KEY_ENCRYPTION_KEY is not set. Set it to encrypt at rest.",
            _STORE_PATH,
        )
        _warned_plaintext = True
    return secret, False


def _decrypt_from_file(record: dict[str, Any]) -> Optional[str]:
    secret = record.get("user_secret")
    if not isinstance(secret, str):
        return None
    if record.get("encrypted"):
        return crypto.decrypt(secret)
    return secret


# ─── public API ──────────────────────────────────────────────────────────────

def get_status() -> Optional[dict[str, Any]]:
    """Non-secret connection metadata for the current user, or None if not
    connected. Never returns the decrypted secret."""
    if use_db():
        with session_scope() as db:
            return SnapTradeConnectionRepository(db, current_user_id()).get_metadata()
    record = _read_json(_STORE_PATH).get(current_user_id())
    if not isinstance(record, dict) or not record.get("user_secret"):
        return None
    return {
        "snaptrade_user_id": record.get("snaptrade_user_id"),
        "connected": True,
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def get_credentials() -> Optional[tuple[str, str]]:
    """``(snaptrade_user_id, decrypted_user_secret)`` for the current user, or
    None if not connected."""
    if use_db():
        with session_scope() as db:
            return SnapTradeConnectionRepository(db, current_user_id()).get_secret()
    record = _read_json(_STORE_PATH).get(current_user_id())
    if not isinstance(record, dict):
        return None
    snaptrade_user_id = record.get("snaptrade_user_id")
    secret = _decrypt_from_file(record)
    if not (snaptrade_user_id and secret):
        return None
    return str(snaptrade_user_id), secret


def save(snaptrade_user_id: str, user_secret: str) -> dict[str, Any]:
    """Create or replace the current user's connection (encrypts the secret)."""
    if use_db():
        with session_scope() as db:
            return SnapTradeConnectionRepository(db, current_user_id()).save(
                snaptrade_user_id, user_secret
            )
    import datetime

    store = _read_json(_STORE_PATH)
    uid = current_user_id()
    stored_value, was_encrypted = _encrypt_for_file(user_secret)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    existing = store.get(uid) if isinstance(store.get(uid), dict) else {}
    store[uid] = {
        "snaptrade_user_id": snaptrade_user_id,
        "user_secret": stored_value,
        "encrypted": was_encrypted,
        "created_at": existing.get("created_at", now),
        "updated_at": now,
    }
    _write_json(_STORE_PATH, store)
    return {
        "snaptrade_user_id": snaptrade_user_id,
        "connected": True,
        "created_at": store[uid]["created_at"],
        "updated_at": now,
    }


def delete() -> bool:
    """Forget the current user's connection. True if one existed."""
    if use_db():
        with session_scope() as db:
            return SnapTradeConnectionRepository(db, current_user_id()).delete()
    store = _read_json(_STORE_PATH)
    uid = current_user_id()
    if uid not in store:
        return False
    del store[uid]
    _write_json(_STORE_PATH, store)
    return True
