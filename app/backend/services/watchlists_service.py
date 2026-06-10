"""Service for reading/writing the multi-watchlist store.

Runtime user state lives under ``app/data/`` (same dir as
``portfolio_settings.json``) — consolidated from the old ``app/backend/data/``
location. ``_LEGACY_STORE_PATH`` is read once as a fallback so existing
installs keep their watchlists; the next write lands in the new location.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

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


def get_all() -> list[dict]:
    """Return all watchlists [{name, tickers}]."""
    with _lock:
        return _load()["watchlists"]


def get_one(name: str) -> dict | None:
    """Return the watchlist with the given name, or None if not found."""
    with _lock:
        for wl in _load()["watchlists"]:
            if wl["name"] == name:
                return wl
    return None


def upsert(name: str, tickers: list[dict]) -> dict:
    """Create or replace the watchlist with this name."""
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
    """Rename a watchlist. Returns True if found and renamed, False otherwise."""
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
    with _lock:
        data = _load()
        before = len(data["watchlists"])
        data["watchlists"] = [w for w in data["watchlists"] if w["name"] != name]
        if len(data["watchlists"]) < before:
            _save(data)
            return True
    return False
