"""Per-ticker portfolio settings overlay service.

Stores per-ticker overrides for allocation and agent selection on a per-sleeve
basis. The schema is intentionally flat JSON so it round-trips without a
migration step:

    {
        "<sleeve_name>": {
            "<TICKER>": {
                "allocation_pct": <float>,
                "agents": null | ["agent_key", ...]
            }
        }
    }

``agents: null`` means "inherit the sleeve's default agent list".
``agents: [...]`` means the ticker runs only those agents on its next scan.

Writes are atomic (temp file + ``os.replace``) and thread-safe (module-level
``threading.Lock``). Reads return empty dicts gracefully when the file does
not exist yet.

Storage backend: when ``STORAGE_BACKEND=db`` each public function dispatches to
:class:`PortfolioSettingsRepository` (Postgres). The nested
``{sleeve: {ticker: {allocation_pct, agents}}}`` shape is identical either way.
Validation (the ``HTTPException`` 400s below) runs in both backends; only the
persistence target changes. Default ``file`` — local behavior unchanged. See
:mod:`app.backend.services._storage`.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.backend.repositories.portfolio_settings_repository import (
    PortfolioSettingsRepository,
)
from app.backend.services._storage import DEFAULT_USER_ID, session_scope, use_db

_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "portfolio_settings.json"
_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()


# ─── Internal helpers ────────────────────────────────────────────────────────


def _load() -> dict[str, Any]:
    """Read the JSON file; return empty dict if it doesn't exist or is corrupt."""
    if not _DATA_PATH.exists():
        return {}
    try:
        with _DATA_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, Any]) -> None:
    """Atomically write ``data`` to the settings file."""
    dir_ = _DATA_PATH.parent
    dir_.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".portfolio_settings.", suffix=".tmp", dir=str(dir_)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, _DATA_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ─── Public API ─────────────────────────────────────────────────────────────


def get_all() -> dict[str, Any]:
    """Return the full settings dict (sleeve → ticker → {allocation_pct, agents})."""
    if use_db():
        with session_scope() as db:
            return PortfolioSettingsRepository(db, DEFAULT_USER_ID).get_all()
    with _lock:
        return _load()


def put_all(settings: dict[str, Any]) -> dict[str, Any]:
    """Replace the entire settings dict atomically. Returns the saved state."""
    if not isinstance(settings, dict):
        raise HTTPException(status_code=400, detail="settings must be a JSON object.")
    if use_db():
        with session_scope() as db:
            return PortfolioSettingsRepository(db, DEFAULT_USER_ID).put_all(settings)
    with _lock:
        _save(settings)
        return _load()


def get_sleeve(sleeve: str) -> dict[str, Any]:
    """Return per-ticker settings for one sleeve; empty dict if not configured."""
    if use_db():
        with session_scope() as db:
            return PortfolioSettingsRepository(db, DEFAULT_USER_ID).get_sleeve(sleeve)
    with _lock:
        data = _load()
    return dict(data.get(sleeve, {}))


def upsert_ticker(
    sleeve: str,
    ticker: str,
    allocation_pct: float,
    agents: list[str] | None,
) -> dict[str, Any]:
    """Create or update a single ticker entry within a sleeve.

    Returns the updated full settings dict.
    Raises 400 on bad ``allocation_pct`` or invalid ``agents`` value.
    """
    try:
        alloc = float(allocation_pct)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="allocation_pct must be a number.")
    if alloc < 0 or alloc > 100:
        raise HTTPException(status_code=400, detail="allocation_pct must be 0..100.")
    if agents is not None:
        if not isinstance(agents, list):
            raise HTTPException(status_code=400, detail="agents must be a list or null.")
        agents = [str(a).strip() for a in agents if str(a).strip()]

    if use_db():
        # Match the file backend's key normalization (ticker stored uppercased).
        with session_scope() as db:
            return PortfolioSettingsRepository(db, DEFAULT_USER_ID).upsert_ticker(
                sleeve, ticker.upper(), alloc, agents
            )
    with _lock:
        data = _load()
        sleeve_data = data.setdefault(sleeve, {})
        sleeve_data[ticker.upper()] = {
            "allocation_pct": alloc,
            "agents": agents,
        }
        _save(data)
        return _load()


def delete_ticker(sleeve: str, ticker: str) -> None:
    """Remove a ticker from a sleeve's settings. No-op if not present."""
    if use_db():
        with session_scope() as db:
            PortfolioSettingsRepository(db, DEFAULT_USER_ID).delete_ticker(
                sleeve, ticker.upper()
            )
        return
    with _lock:
        data = _load()
        sleeve_data = data.get(sleeve, {})
        sleeve_data.pop(ticker.upper(), None)
        # Clean up empty sleeve entries so the file stays tidy.
        if not sleeve_data and sleeve in data:
            del data[sleeve]
        elif sleeve in data:
            data[sleeve] = sleeve_data
        _save(data)
