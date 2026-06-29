"""Per-user onboarding-flag read/write service.

The first-login walkthrough must show exactly once per account. Storing the
"completed" flag server-side (rather than only in the browser's localStorage)
makes it truly once-per-account: it survives a localStorage clear or a sign-in
on a new device.

Storage backend (see :mod:`app.backend.services._storage`):
- ``file`` (default): a small JSON file ``app/data/onboarding.json`` mapping
  ``user_id -> bool``. Atomic temp-file + ``os.replace`` write, matching the
  other file stores (e.g. ``pnl_service``).
- ``db``: the ``user_settings.onboarding_completed`` column via
  :class:`PortfolioRepository`.

Both paths return a plain ``bool``, so the route is identical either way.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from app.backend.repositories.portfolio_repository import PortfolioRepository
from app.backend.services._storage import current_user_id, session_scope, use_db

# app/data/onboarding.json  (parents[2] == the app/ dir)
_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "onboarding.json"


def _read_file_map() -> dict[str, bool]:
    if not _DATA_PATH.exists():
        return {}
    try:
        with _DATA_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): bool(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        # A corrupt/unreadable flag file just means "not seen yet" — the
        # walkthrough re-shows once rather than crashing the request.
        return {}


def _write_file_map(data: dict[str, bool]) -> None:
    _DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".onboarding.", suffix=".tmp", dir=str(_DATA_PATH.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, _DATA_PATH)
    except BaseException:
        # Don't leave a stray temp file behind if the write/replace failed.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get_onboarding_completed() -> bool:
    """Whether the current user has finished/skipped the onboarding walkthrough."""
    if use_db():
        with session_scope() as db:
            return PortfolioRepository(db, current_user_id()).get_onboarding_completed()
    return _read_file_map().get(current_user_id(), False)


def set_onboarding_completed(flag: bool = True) -> bool:
    """Record that the current user has finished/skipped onboarding."""
    if use_db():
        with session_scope() as db:
            return PortfolioRepository(db, current_user_id()).set_onboarding_completed(flag)
    data = _read_file_map()
    data[current_user_id()] = bool(flag)
    _write_file_map(data)
    return bool(flag)
