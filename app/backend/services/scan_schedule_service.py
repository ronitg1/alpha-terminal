"""Scheduled background pre-scan storage (file + DB backends).

Per-user CRUD for the times a user wants an automatic pattern scan, plus reading
their latest pre-computed results. Mirrors the storage-seam pattern of the other
services (see :mod:`app.backend.services._storage`): ``db`` dispatches to
:class:`ScheduleRepository`; ``file`` keeps small JSON files so local dev and the
cutover tests work. Both return identical dict shapes.

Validation raises ``ValueError`` (→ 400) for bad input; the cross-user scheduler
orchestration lives in :mod:`app.backend.services.prescan_runner`.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from app.backend.repositories.schedule_repository import ScheduleRepository
from app.backend.services._storage import current_user_id, session_scope, use_db

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")  # 24-hour HH:MM
_MAX_SCHEDULES_PER_USER = 6

# app/data/*.json  (parents[2] == the app/ dir)
_SCHED_PATH = Path(__file__).resolve().parents[2] / "data" / "scan_schedules.json"
_PRESCAN_PATH = Path(__file__).resolve().parents[2] / "data" / "prescan_results.json"


# ─── validation ──────────────────────────────────────────────────────────────

def validate_time(time_of_day: str) -> str:
    t = (time_of_day or "").strip()
    if not _TIME_RE.match(t):
        raise ValueError("Time must be in 24-hour HH:MM format (e.g. 08:00 or 15:30).")
    return t


def _valid_tz(timezone: str) -> str:
    tz = (timezone or "").strip() or "America/New_York"
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(tz)
    except Exception:
        raise ValueError(f"Unknown timezone '{tz}'.")
    return tz


def validate_timeframe_lookback(timeframe: str, lookback_days: int) -> tuple[str, int]:
    """Validate a schedule's timeframe and clamp its lookback to that timeframe's
    server-side max, mirroring the live scanner. Lazy import of the timeframe
    registry avoids a routes->services->routes import cycle."""
    from app.backend.routes.patterns import _TIMEFRAMES

    tf = (timeframe or "day").strip()
    if tf not in _TIMEFRAMES:
        raise ValueError(f"Unknown timeframe '{tf}'. Use one of: {', '.join(_TIMEFRAMES)}.")
    try:
        days = int(lookback_days)
    except (TypeError, ValueError):
        raise ValueError("lookback_days must be a whole number of days.")
    if days < 1:
        raise ValueError("lookback_days must be positive.")
    return tf, min(days, int(_TIMEFRAMES[tf]["max_lookback_days"]))


def _normalize_user_prescans(entry: Any) -> dict[str, dict]:
    """A user's stored pre-scans as ``{timeframe: prescan-dict}``, tolerating the
    old single-slot shape (a flat dict with a top-level ``results`` key)."""
    if not isinstance(entry, dict):
        return {}
    if "results" in entry:  # legacy flat shape → one slot keyed by its timeframe
        return {entry.get("timeframe") or "day": entry}
    return {k: v for k, v in entry.items() if isinstance(v, dict)}


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


def _next_id(store: dict) -> int:
    ids = [s["id"] for lst in store.values() for s in lst if isinstance(s, dict) and "id" in s]
    return (max(ids) + 1) if ids else 1


_VALID_INTERVALS = {60, 120, 240}  # 1h / 2h / 4h


def validate_interval(interval_minutes: int | None) -> int | None:
    """Validate a recurring-interval choice. None = classic daily schedule."""
    if interval_minutes in (None, 0):
        return None
    try:
        n = int(interval_minutes)
    except (TypeError, ValueError):
        raise ValueError("interval_minutes must be a whole number of minutes.")
    if n not in _VALID_INTERVALS:
        raise ValueError(f"interval_minutes must be one of {sorted(_VALID_INTERVALS)} (1h/2h/4h).")
    return n


def _ensure_cfg(row: dict[str, Any]) -> dict[str, Any]:
    """Backfill newer fields on a file-backend row that predates them, so the API
    shape is uniform (the DB backend gets them via the migration defaults)."""
    row.setdefault("timeframe", "day")
    row.setdefault("lookback_days", 180)
    row.setdefault("interval_minutes", None)
    row.setdefault("last_run_at", None)
    return row


# ─── per-user schedule CRUD ──────────────────────────────────────────────────

def list_schedules() -> list[dict[str, Any]]:
    if use_db():
        with session_scope() as db:
            return ScheduleRepository(db, current_user_id()).list_schedules()
    store = _read_json(_SCHED_PATH)
    return [_ensure_cfg(s) for s in sorted(store.get(current_user_id(), []), key=lambda s: s["time_of_day"])]


def add_schedule(
    time_of_day: str, timezone: str, timeframe: str = "day", lookback_days: int = 180,
    interval_minutes: int | None = None,
) -> dict[str, Any]:
    t = validate_time(time_of_day)
    tz = _valid_tz(timezone)
    tf, lookback = validate_timeframe_lookback(timeframe, lookback_days)
    interval = validate_interval(interval_minutes)
    if use_db():
        with session_scope() as db:
            return ScheduleRepository(db, current_user_id()).add_schedule(t, tz, tf, lookback, interval)
    store = _read_json(_SCHED_PATH)
    uid = current_user_id()
    mine = store.setdefault(uid, [])
    if len(mine) >= _MAX_SCHEDULES_PER_USER:
        raise ValueError(f"At most {_MAX_SCHEDULES_PER_USER} scheduled times allowed.")
    if any(s["time_of_day"] == t for s in mine):
        raise ValueError(f"A scan is already scheduled at {t}.")
    row = {
        "id": _next_id(store), "time_of_day": t, "timezone": tz, "enabled": True,
        "last_run_on": None, "timeframe": tf, "lookback_days": lookback,
        "interval_minutes": interval, "last_run_at": None,
    }
    mine.append(row)
    _write_json(_SCHED_PATH, store)
    return row


def update_schedule(
    schedule_id: int, timeframe: str, lookback_days: int, interval_minutes: int | None = None
) -> dict[str, Any]:
    """Change a schedule's timeframe + lookback (validated together), and its
    recurring interval (None = daily-at-time)."""
    tf, lookback = validate_timeframe_lookback(timeframe, lookback_days)
    interval = validate_interval(interval_minutes)
    if use_db():
        with session_scope() as db:
            return ScheduleRepository(db, current_user_id()).update_schedule(schedule_id, tf, lookback, interval)
    store = _read_json(_SCHED_PATH)
    for s in store.get(current_user_id(), []):
        if s["id"] == schedule_id:
            s["timeframe"], s["lookback_days"], s["interval_minutes"] = tf, lookback, interval
            _write_json(_SCHED_PATH, store)
            return _ensure_cfg(s)
    raise LookupError(f"No schedule {schedule_id}.")


def set_schedule_enabled(schedule_id: int, enabled: bool) -> dict[str, Any]:
    if use_db():
        with session_scope() as db:
            return ScheduleRepository(db, current_user_id()).set_enabled(schedule_id, enabled)
    store = _read_json(_SCHED_PATH)
    for s in store.get(current_user_id(), []):
        if s["id"] == schedule_id:
            s["enabled"] = enabled
            _write_json(_SCHED_PATH, store)
            return _ensure_cfg(s)
    raise LookupError(f"No schedule {schedule_id}.")


def delete_schedule(schedule_id: int) -> None:
    if use_db():
        with session_scope() as db:
            ScheduleRepository(db, current_user_id()).delete_schedule(schedule_id)
            return
    store = _read_json(_SCHED_PATH)
    uid = current_user_id()
    mine = store.get(uid, [])
    new = [s for s in mine if s["id"] != schedule_id]
    if len(new) == len(mine):
        raise LookupError(f"No schedule {schedule_id}.")
    store[uid] = new
    _write_json(_SCHED_PATH, store)


def get_prescan(timeframe: str | None = None) -> dict[str, Any] | None:
    """The user's pre-scan for ``timeframe``, or — when ``timeframe`` is None — the
    most recently computed one across all timeframes (initial-load default)."""
    if use_db():
        with session_scope() as db:
            return ScheduleRepository(db, current_user_id()).get_prescan(timeframe)
    prescans = _normalize_user_prescans(_read_json(_PRESCAN_PATH).get(current_user_id()))
    if not prescans:
        return None
    if timeframe:
        return prescans.get(timeframe)
    return max(prescans.values(), key=lambda p: p.get("computed_at") or "")


# ─── cross-user helpers for the scheduler (used by prescan_runner) ────────────

def all_enabled_schedules() -> list[dict[str, Any]]:
    if use_db():
        with session_scope() as db:
            return ScheduleRepository(db).all_enabled_schedules()
    store = _read_json(_SCHED_PATH)
    out: list[dict] = []
    for uid, lst in store.items():
        for s in lst:
            if s.get("enabled", True):
                out.append({
                    "id": s["id"],
                    "user_id": uid,
                    "time_of_day": s["time_of_day"],
                    "timezone": s["timezone"],
                    "last_run_on": s.get("last_run_on"),
                    "timeframe": s.get("timeframe", "day"),
                    "lookback_days": s.get("lookback_days", 180),
                    "interval_minutes": s.get("interval_minutes"),
                    "last_run_at": s.get("last_run_at"),
                })
    return out


def mark_run(schedule_id: int, user_id: str, ran_on: str) -> None:
    """Record that a schedule fired: stamp last_run_on (daily dedupe) AND
    last_run_at (interval gating). Both are harmless to the other mode."""
    import datetime

    ran_at = datetime.datetime.now(datetime.timezone.utc)
    if use_db():
        with session_scope() as db:
            ScheduleRepository(db).mark_run(schedule_id, ran_on, ran_at)
            return
    store = _read_json(_SCHED_PATH)
    for s in store.get(user_id, []):
        if s["id"] == schedule_id:
            s["last_run_on"] = ran_on
            s["last_run_at"] = ran_at.isoformat()
            _write_json(_SCHED_PATH, store)
            return


def set_prescan_for(user_id: str, results: list[dict], timeframe: str, ticker_count: int) -> None:
    if use_db():
        with session_scope() as db:
            ScheduleRepository(db).set_prescan_for(user_id, results, timeframe, ticker_count)
            return
    import datetime

    store = _read_json(_PRESCAN_PATH)
    prescans = _normalize_user_prescans(store.get(user_id))
    prescans[timeframe] = {
        "results": results,
        "timeframe": timeframe,
        "ticker_count": ticker_count,
        # file backend has no DB timestamp; stamp here for shape parity
        "computed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    store[user_id] = prescans
    _write_json(_PRESCAN_PATH, store)
