"""Persisted thesis store.

Every LLM thesis the user runs (portfolio memo, per-sleeve memo, per-ticker
quick/deep analysis) is saved here so it survives page refreshes and backend
restarts — analyses cost real LLM credits and represent a point-in-time
read worth keeping until the user explicitly re-runs them.

Storage: ``app/data/theses.json`` — a flat dict keyed by scope:

    "portfolio"                → portfolio memo payload
    "sleeve:<name>"            → sleeve memo payload
    "ticker:<SYMBOL>:<depth>"  → per-ticker thesis payload (depth: quick|deep)

Each saved payload carries a ``saved_at`` ISO timestamp added on write.
Writes are atomic (temp file + ``os.replace``) and thread-safe.

Storage backend: when ``STORAGE_BACKEND=db`` ``save``/``get_all`` dispatch to
:class:`ThesisRepository` (Postgres). The ``saved_at`` stamp is still added here
(same payload either way), so the ``{key: payload}`` shape is identical. Default
``file`` — local behavior unchanged. See :mod:`app.backend.services._storage`.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.backend.repositories.thesis_repository import ThesisRepository
from app.backend.services._storage import DEFAULT_USER_ID, session_scope, use_db

logger = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "theses.json"
_lock = threading.Lock()


def _load() -> dict[str, Any]:
    if not _DATA_PATH.exists():
        return {}
    try:
        with _DATA_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("thesis store unreadable (%s) — starting empty", exc)
        return {}


def _write(data: dict[str, Any]) -> None:
    _DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".theses.", suffix=".tmp", dir=str(_DATA_PATH.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
            f.write("\n")
        os.replace(tmp, _DATA_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save(key: str, thesis: dict[str, Any]) -> None:
    """Persist a thesis under ``key``, stamping ``saved_at``. Best-effort —
    callers treat a failed save as non-fatal (the live response still flows)."""
    record = {**thesis, "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if use_db():
        with session_scope() as db:
            ThesisRepository(db, DEFAULT_USER_ID).upsert(key, record)
        return
    with _lock:
        data = _load()
        data[key] = record
        _write(data)


def get_all() -> dict[str, Any]:
    """Every saved thesis, keyed by scope."""
    if use_db():
        with session_scope() as db:
            return ThesisRepository(db, DEFAULT_USER_ID).get_all()
    with _lock:
        return _load()
