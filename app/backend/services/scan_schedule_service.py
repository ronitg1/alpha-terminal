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


# ─── per-user schedule CRUD ──────────────────────────────────────────────────

def list_schedules() -> list[dict[str, Any]]:
    if use_db():
        with session_scope() as db:
            return ScheduleRepository(db, current_user_id()).list_schedules()
    store = _read_json(_SCHED_PATH)
    return sorted(store.get(current_user_id(), []), key=lambda s: s["time_of_day"])


def add_schedule(time_of_day: str, timezone: str) -> dict[str, Any]:
    t = validate_time(time_of_day)
    tz = _valid_tz(timezone)
    if use_db():
        with session_scope() as db:
            return ScheduleRepository(db, current_user_id()).add_schedule(t, tz)
    store = _read_json(_SCHED_PATH)
    uid = current_user_id()
    mine = store.setdefault(uid, [])
    if len(mine) >= _MAX_SCHEDULES_PER_USER:
        raise ValueError(f"At most {_MAX_SCHEDULES_PER_USER} scheduled times allowed.")
    if any(s["time_of_day"] == t for s in mine):
        raise ValueError(f"A scan is already scheduled at {t}.")
    row = {"id": _next_id(store), "time_of_day": t, "timezone": tz, "enabled": True, "last_run_on": None}
    mine.append(row)
    _write_json(_SCHED_PATH, store)
    return row


def set_schedule_enabled(schedule_id: int, enabled: bool) -> dict[str, Any]:
    if use_db():
        with session_scope() as db:
            return ScheduleRepository(db, current_user_id()).set_enabled(schedule_id, enabled)
    store = _read_json(_SCHED_PATH)
    for s in store.get(current_user_id(), []):
        if s["id"] == schedule_id:
            s["enabled"] = enabled
            _write_json(_SCHED_PATH, store)
            return s
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


def get_prescan() -> dict[str, Any] | None:
    if use_db():
        with session_scope() as db:
            return ScheduleRepository(db, current_user_id()).get_prescan()
    return _read_json(_PRESCAN_PATH).get(current_user_id())


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
                out.append({**{k: s[k] for k in ("id", "time_of_day", "timezone", "last_run_on")}, "user_id": uid})
    return out


def mark_run(schedule_id: int, user_id: str, ran_on: str) -> None:
    if use_db():
        with session_scope() as db:
            ScheduleRepository(db).mark_run(schedule_id, ran_on)
            return
    store = _read_json(_SCHED_PATH)
    for s in store.get(user_id, []):
        if s["id"] == schedule_id:
            s["last_run_on"] = ran_on
            _write_json(_SCHED_PATH, store)
            return


def set_prescan_for(user_id: str, results: list[dict], timeframe: str, ticker_count: int) -> None:
    if use_db():
        with session_scope() as db:
            ScheduleRepository(db).set_prescan_for(user_id, results, timeframe, ticker_count)
            return
    import datetime

    store = _read_json(_PRESCAN_PATH)
    store[user_id] = {
        "results": results,
        "timeframe": timeframe,
        "ticker_count": ticker_count,
        # file backend has no DB timestamp; stamp here for shape parity
        "computed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    _write_json(_PRESCAN_PATH, store)
