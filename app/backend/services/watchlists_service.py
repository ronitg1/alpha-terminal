"""Service for reading/writing the multi-watchlist store.

Runtime user state lives under ``app/data/`` (same dir as
``portfolio_settings.json``) — consolidated from the old ``app/backend/data/``
location. ``_LEGACY_STORE_PATH`` is read once as a fallback so existing
installs keep their watchlists; the next write lands in the new location.

Storage backend: when ``STORAGE_BACKEND=db`` each public function dispatches to
:class:`WatchlistRepository` (Postgres) instead of the JSON file. The dict
shapes returned are identical either way, so routes and the frontend are
unaffected by the backend choice. Default is ``file`` — local behavior is
unchanged until the flag is flipped. See :mod:`app.backend.services._storage`.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.backend.repositories.watchlist_repository import WatchlistRepository
from app.backend.services._storage import (
    current_user_id,
    RESERVED_OPPORTUNISTIC_WATCHLIST,
    integrity_as_value_error,
    session_scope,
    use_db,
)

# app/data/watchlists.json  (parents[2] == the app/ dir)
_STORE_PATH = Path(__file__).resolve().parents[2] / "data" / "watchlists.json"
# Pre-consolidation location (app/backend/data/), read-only fallback.
_LEGACY_STORE_PATH = Path(__file__).resolve().parents[1] / "data" / "watchlists.json"
_lock = threading.Lock()


def _load() -> dict[str, Any]:
    path = _STORE_PATH if _STORE_PATH.exists() else _LEGACY_STORE_PATH
    if not path.exists():
        return {"watchlists": [{"name": "Market Watchlist", "tickers": []}]}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict[str, Any]) -> None:
    """Atomic write (temp file + os.replace) so a crash mid-write can't
    corrupt the store — matches the pattern in portfolio_settings_service."""
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=".watchlists.", suffix=".tmp", dir=str(_STORE_PATH.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, _STORE_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _reject_reserved(name: str) -> None:
    """Block writes targeting the reserved opportunistic-watchlist name.

    Applied in BOTH backends for parity: under db the name aliases the legacy
    opportunistic store (writing it would clobber that hidden list); under file
    it would create a confusing ``__opportunistic__`` entry in watchlists.json.
    Either way it's a bad request."""
    if name == RESERVED_OPPORTUNISTIC_WATCHLIST:
        raise HTTPException(status_code=400, detail=f"'{name}' is a reserved name.")


def get_all() -> list[dict]:
    """Return all watchlists [{name, tickers}].

    The reserved opportunistic-watchlist name is hidden under the db backend so
    the legacy single watchlist (stored in the same table) never leaks into the
    multi-watchlist list — matching the file backend, where it's a separate file.
    """
    if use_db():
        with session_scope() as db:
            return [
                w for w in WatchlistRepository(db, current_user_id()).get_all()
                if w["name"] != RESERVED_OPPORTUNISTIC_WATCHLIST
            ]
    with _lock:
        return _load()["watchlists"]


def get_one(name: str) -> dict | None:
    """Return the watchlist with the given name, or None if not found."""
    if use_db():
        if name == RESERVED_OPPORTUNISTIC_WATCHLIST:
            return None  # hidden from the multi-watchlist surface
        with session_scope() as db:
            return WatchlistRepository(db, current_user_id()).get_one(name)
    with _lock:
        for wl in _load()["watchlists"]:
            if wl["name"] == name:
                return wl
    return None


def upsert(name: str, tickers: list[dict]) -> dict:
    """Create or replace the watchlist with this name."""
    _reject_reserved(name)
    if use_db():
        with session_scope() as db, integrity_as_value_error():
            return WatchlistRepository(db, current_user_id()).upsert(name, tickers)
    with _lock:
        data = _load()
        for wl in data["watchlists"]:
            if wl["name"] == name:
                wl["tickers"] = tickers
                _save(data)
                return wl
        # New list
        new_wl = {"name": name, "tickers": tickers}
        data["watchlists"].append(new_wl)
        _save(data)
        return new_wl


def rename(old_name: str, new_name: str) -> bool:
    """Rename a watchlist. Returns True if found and renamed, False otherwise.

    Under the DB backend, raises ValueError if ``new_name`` already exists (the
    repo enforces per-user name uniqueness). The route is responsible for
    catching that and returning 409. The file backend never enforced uniqueness
    and so never raises here — a known, accepted divergence until the file
    backend is retired."""
    _reject_reserved(new_name)  # can't rename a list INTO the reserved name
    if use_db():
        if old_name == RESERVED_OPPORTUNISTIC_WATCHLIST:
            return False  # not visible here, so "not found"
        with session_scope() as db, integrity_as_value_error():
            return WatchlistRepository(db, current_user_id()).rename(old_name, new_name)
    with _lock:
        data = _load()
        for wl in data["watchlists"]:
            if wl["name"] == old_name:
                wl["name"] = new_name
                _save(data)
                return True
    return False


def delete(name: str) -> bool:
    """Delete a watchlist by name. Returns True if found and deleted, False otherwise."""
    if use_db():
        if name == RESERVED_OPPORTUNISTIC_WATCHLIST:
            return False  # not deletable through the multi-watchlist surface
        with session_scope() as db:
            return WatchlistRepository(db, current_user_id()).delete(name)
    with _lock:
        data = _load()
        before = len(data["watchlists"])
        data["watchlists"] = [w for w in data["watchlists"] if w["name"] != name]
        if len(data["watchlists"]) < before:
            _save(data)
            return True
    return False
