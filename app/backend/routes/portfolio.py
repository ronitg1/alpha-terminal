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
async def get_overview() -> dict[str, Any]:
    """The current user's portfolio across all connected brokerages."""
    return await portfolio_overview.build_overview()


@router.get("/earnings")
async def get_earnings(tickers: str = "", days: int = 30) -> dict[str, Any]:
    """Upcoming earnings dates for the given holdings over the next ``days`` (via
    Finnhub). The frontend passes the underlyings it already has. Best-effort:
    returns an empty list rather than erroring when Finnhub is unavailable."""
    import asyncio
    import datetime

    from src.tools.finnhub.client import FinnhubClient, is_finnhub_configured

    syms = {t.strip().upper() for t in tickers.split(",") if t.strip()}
    if not syms or not is_finnhub_configured():
        return {"earnings": []}
    days = max(1, min(int(days), 120))
    today = datetime.date.today()
    end = today + datetime.timedelta(days=days)

    def _fetch() -> list[dict[str, Any]]:
        # Query per-symbol. Two reasons: (1) the shared-class alias problem — asking
        # for GOOG returns Finnhub symbol GOOGL, so a whole-calendar scan filtered by
        # the user's exact ticker silently drops it; querying by the held ticker and
        # labeling the row with *that* ticker keeps the attribution the user expects.
        # (2) the free tier's full calendar is ~1,500 rows; per-symbol is cheaper.
        client = FinnhubClient()
        out: list[dict[str, Any]] = []
        for sym in sorted(syms)[:40]:
            try:
                data = client.earnings_calendar(
                    start_date=today.isoformat(), end_date=end.isoformat(), ticker=sym
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Earnings calendar (%s) failed: %s", sym, type(exc).__name__)
                continue
            rows = data.get("earningsCalendar") if isinstance(data, dict) else None
            if not rows:
                continue
            # Nearest upcoming date for this holding.
            r = min(rows, key=lambda x: x.get("date") or "9999")
            out.append({
                "ticker": sym,  # the held ticker, not Finnhub's alias
                "date": r.get("date"),
                "hour": r.get("hour"),  # bmo | amc | dmh
                "eps_estimate": r.get("epsEstimate"),
                "revenue_estimate": r.get("revenueEstimate"),
            })
        out.sort(key=lambda e: (e.get("date") or "9999", e.get("ticker")))
        return out

    try:
        # Per-symbol scan makes up to len(syms) sequential Finnhub calls, so give it
        # more headroom than the old single-call budget.
        earnings = await asyncio.wait_for(asyncio.to_thread(_fetch), timeout=25.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Earnings fetch timed out/failed: %s", type(exc).__name__)
        earnings = []
    return {"earnings": earnings}
