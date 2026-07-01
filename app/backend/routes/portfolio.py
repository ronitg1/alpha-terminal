"""Unified cross-brokerage portfolio overview for the Portfolio tab.

  GET /portfolio/overview — merged accounts (SnapTrade + Robinhood) + an
  "All combined" aggregate, enriched with quotes and display metrics.

Returns ``connected: False`` (and empty accounts) when the user has no brokerage
connected, so the frontend can show a connect prompt instead of an error.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

from app.backend.services import portfolio_overview

router = APIRouter(prefix="/portfolio", tags=["portfolio"])
logger = logging.getLogger(__name__)


@router.get("/overview")
async def get_overview(refresh: bool = False) -> dict[str, Any]:
    """The current user's portfolio across all connected brokerages.

    Served from a per-user cache (stale-while-revalidate). Pass ``?refresh=true``
    (the manual Refresh button) to force a rebuild."""
    return await portfolio_overview.build_overview(force=refresh)


@router.get("/stats")
async def get_stats(refresh: bool = False) -> dict[str, Any]:
    """Approximate portfolio risk stats (annualized Sharpe from current holdings'
    weights applied to ~1y of daily returns). Cached per user; ``?refresh=true``
    forces a rebuild. Never errors — ``available: False`` carries a reason."""
    from app.backend.services import portfolio_stats

    try:
        return await portfolio_stats.build_stats(force=refresh)
    except Exception as exc:  # noqa: BLE001 — a stats hiccup must not break the tab
        logger.warning("Portfolio stats build failed: %s", type(exc).__name__)
        return {"available": False, "reason": "error"}


# Per-symbol earnings results cached for the day so re-opening the Portfolio tab
# doesn't re-hit Finnhub. Keyed (symbol, iso-date) -> row|None; date bounds the TTL.
_earnings_cache: dict[tuple[str, str], dict[str, Any] | None] = {}


@router.get("/ownership")
async def get_ownership(tickers: str = "") -> dict[str, Any]:
    """13F ownership/flow: which tracked funds hold your names and last quarter's
    change (new/added/trimmed/exited). SEC EDGAR, cached a day; time-boxed."""
    import asyncio

    from app.backend.services import ownership_service

    syms = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if not syms:
        return {"names": [], "institutions": []}
    try:
        return await asyncio.wait_for(asyncio.to_thread(ownership_service.build_ownership, syms), timeout=55.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ownership build failed/timed out: %s", type(exc).__name__)
        return {"names": [], "institutions": []}


@router.get("/earnings")
async def get_earnings(tickers: str = "", days: int = 30) -> dict[str, Any]:
    """Upcoming earnings dates for the given holdings over the next ``days`` (via
    Finnhub). The frontend passes the underlyings it already has. Best-effort:
    returns an empty list rather than erroring when Finnhub is unavailable.

    Queries per-symbol (the shared-class alias problem: asking for GOOG returns
    Finnhub symbol GOOGL, so a whole-calendar scan filtered by the user's exact
    ticker silently drops it). Calls run CONCURRENTLY with bounded fan-out — the
    old sequential loop took ~0.5s/holding, so a 20-name book stalled ~10s and
    looked broken. Results are cached per (symbol, day)."""
    import asyncio
    import datetime

    from src.tools.finnhub.client import FinnhubClient, is_finnhub_configured

    syms = sorted({t.strip().upper() for t in tickers.split(",") if t.strip()})[:40]
    if not syms or not is_finnhub_configured():
        return {"earnings": []}
    days = max(1, min(int(days), 120))
    today = datetime.date.today()
    today_iso = today.isoformat()
    end = today + datetime.timedelta(days=days)
    end_iso = end.isoformat()

    def _fetch_all() -> list[dict[str, Any]]:
        # SEQUENTIAL on purpose. Finnhub's free tier rate-limits hard; firing these
        # concurrently triggers a 429 storm with exponential backoff that runs slower
        # than doing them one at a time. The per-(symbol, day) cache means the whole
        # loop only pays this cost once per day — every later Portfolio load is a
        # dict lookup. Uncached symbols are the only ones that hit the network, so a
        # returning user with a warm cache gets an instant response.
        client = FinnhubClient()
        out: list[dict[str, Any]] = []
        for sym in syms:
            cache_key = (sym, today_iso)
            if cache_key in _earnings_cache:
                row = _earnings_cache[cache_key]
                if row:
                    out.append(row)
                continue
            try:
                data = client.earnings_calendar(start_date=today_iso, end_date=end_iso, ticker=sym)
            except Exception as exc:  # noqa: BLE001 — transient; don't cache the failure
                logger.warning("Earnings calendar (%s) failed: %s", sym, type(exc).__name__)
                continue
            rows = data.get("earningsCalendar") if isinstance(data, dict) else None
            row = None
            if rows:
                r = min(rows, key=lambda x: x.get("date") or "9999")  # nearest upcoming
                row = {
                    "ticker": sym,  # the held ticker, not Finnhub's alias (GOOG vs GOOGL)
                    "date": r.get("date"),
                    "hour": r.get("hour"),  # bmo | amc | dmh
                    "eps_estimate": r.get("epsEstimate"),
                    "revenue_estimate": r.get("revenueEstimate"),
                }
            _earnings_cache[cache_key] = row  # cache both hits and confirmed "no earnings"
            if row:
                out.append(row)
        out.sort(key=lambda e: (e.get("date") or "9999", e.get("ticker")))
        return out

    try:
        earnings = await asyncio.wait_for(asyncio.to_thread(_fetch_all), timeout=25.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Earnings fetch timed out/failed: %s", type(exc).__name__)
        earnings = []
    return {"earnings": earnings}
