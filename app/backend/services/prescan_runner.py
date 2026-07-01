"""Cross-user orchestration for scheduled background pre-scans.

Driven by an external scheduler (a GitHub Actions cron) that POSTs
``/scheduled/run-due`` every ~15 minutes. For each enabled schedule that's due
(its local time has passed today and it hasn't run yet today), this runs that
user's pattern scan with *their* resolved API keys and watchlists, then stores
the results so the Pattern Scanner loads instantly.

Each user's scan runs under that user's identity + provider keys, bound on the
context vars for the duration and reset afterward (mirroring how a normal request
is scoped), so the data clients pick up the right keys without a request.
"""
from __future__ import annotations

import datetime
import logging
from zoneinfo import ZoneInfo

from app.backend import context
from app.backend.auth import auth_enabled
from app.backend.database.app_models import User
from app.backend.services import key_resolver, scan_schedule_service, watchlists_service
from app.backend.services._storage import session_scope, use_db
from src.tools import key_context

logger = logging.getLogger(__name__)

_TIMEFRAME = "day"
_LOOKBACK_DAYS = 180
_MAX_SCHEDULES_PER_RUN = 25   # safety cap so one trigger can't run unbounded work
_MAX_TICKERS = 200            # cap per user's scan


def _user_email(user_id: str) -> str | None:
    if not use_db():
        return None
    try:
        with session_scope() as db:
            u = db.get(User, user_id)
            return u.email if u else None
    except Exception:  # noqa: BLE001 — email is best-effort for key approval
        return None


def _is_due(sched: dict, now_utc: datetime.datetime) -> tuple[bool, str]:
    """Return (due, today_local_date). Due = the local time has reached the
    scheduled time today and it hasn't already run today (once-per-day)."""
    tz = ZoneInfo(sched.get("timezone") or "America/New_York")
    now_local = now_utc.astimezone(tz)
    today = now_local.date().isoformat()
    if sched.get("last_run_on") == today:
        return False, today
    hh, mm = sched["time_of_day"].split(":")
    sched_minutes = int(hh) * 60 + int(mm)
    now_minutes = now_local.hour * 60 + now_local.minute
    return now_minutes >= sched_minutes, today


def _user_tickers() -> list[str]:
    """Unique tickers across the current user's watchlists (context-scoped)."""
    seen: set[str] = set()
    out: list[str] = []
    for wl in watchlists_service.get_all():
        for entry in wl.get("tickers", []):
            t = (entry.get("ticker") if isinstance(entry, dict) else str(entry)).upper().strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
    return out[:_MAX_TICKERS]


async def _run_for_user(sched: dict, today: str) -> None:
    user_id = sched["user_id"]
    email = _user_email(user_id)
    # Bind this user's identity for the scan. Per-user market-data keys are only
    # bound when auth is on (multi-tenant); with auth off the app uses the shared
    # env keys via the unset context, so we must NOT bind (empty) resolved keys.
    id_tokens = context.set_current_user_identity(user_id, email, True)
    key_tokens = None
    if auth_enabled():
        massive, finnhub, fds = key_resolver.provider_keys_for_request(user_id, email, True)
        key_tokens = key_context.set_provider_keys(
            massive=massive, finnhub=finnhub, financial_datasets=fds
        )
    try:
        tickers = _user_tickers()
        if tickers:
            # Imported here to avoid any import cycle with the routes module.
            from app.backend.routes.patterns import PATTERN_DETECTORS, run_pattern_scan

            results = await run_pattern_scan(
                tickers, list(PATTERN_DETECTORS.keys()), _TIMEFRAME, _LOOKBACK_DAYS
            )
            scan_schedule_service.set_prescan_for(user_id, results, _TIMEFRAME, len(tickers))
            logger.info("Pre-scan for %s: %d tickers -> %d signals", user_id, len(tickers), len(results))
        else:
            logger.info("Pre-scan for %s skipped: no watchlist tickers", user_id)

        # Warm the portfolio overview cache on the same schedule so the Portfolio
        # tab is instant when the user opens the app (the cron runs in-process, so
        # this populates the very cache the app serves). Best-effort.
        try:
            from app.backend.services import portfolio_overview

            await portfolio_overview.build_overview(force=True)
        except Exception as exc:  # noqa: BLE001 — warming is best-effort
            logger.warning("Pre-scan overview warm failed for %s: %s", user_id, type(exc).__name__)

        scan_schedule_service.mark_run(sched["id"], user_id, today)
    finally:
        if key_tokens is not None:
            key_context.reset_provider_keys(key_tokens)
        context.reset_current_user_identity(id_tokens)


async def run_due(now_utc: datetime.datetime | None = None) -> dict:
    """Run every due schedule. Returns a small summary for the caller/logs."""
    now_utc = now_utc or datetime.datetime.now(datetime.timezone.utc)
    schedules = scan_schedule_service.all_enabled_schedules()
    ran = errors = processed = 0
    for sched in schedules:
        if processed >= _MAX_SCHEDULES_PER_RUN:
            logger.warning("run-due cap (%d) reached; remaining schedules deferred to next trigger", _MAX_SCHEDULES_PER_RUN)
            break
        try:
            due, today = _is_due(sched, now_utc)
        except Exception as exc:  # noqa: BLE001 — a bad row shouldn't stop the batch
            logger.warning("Skipping malformed schedule %s: %s", sched.get("id"), exc)
            continue
        if not due:
            continue
        processed += 1
        try:
            await _run_for_user(sched, today)
            ran += 1
        except Exception as exc:  # noqa: BLE001 — one user's failure shouldn't stop others
            errors += 1
            logger.warning("Pre-scan failed for user %s: %s", sched.get("user_id"), exc)
    return {"checked": len(schedules), "ran": ran, "errors": errors}
