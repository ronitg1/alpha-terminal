"""Approximate portfolio risk stats (Sharpe ratio) for the Portfolio summary.

The brokerage feeds give us *current* holdings but no account-value history, so a
true realized Sharpe is impossible. Instead we reconstruct a daily-return series
by applying the CURRENT stock weights to each holding's past year of daily
returns — a constant-weight approximation that ignores trades, deposits, options,
and cash drag. Good enough to answer "how risk-efficient is this book as held
today?", and labeled approximate everywhere it is shown.

Sharpe = mean(daily return − daily rf) / stdev(daily return) × √252, with the
risk-free rate defaulting to 4.5% annual. Options are excluded from the weights
(no clean daily return series per contract); the payload reports what share of
the account the stats actually cover.

Caching mirrors ``portfolio_overview``: per-symbol daily closes cache for hours
(they only change once a trading day) and the finished stats payload caches per
user so re-opening the tab doesn't refetch a year of bars per holding.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import math
import statistics
import time
from typing import Any

from app.backend.services import portfolio_overview

logger = logging.getLogger(__name__)

__all__ = ["build_stats", "blend_daily_returns", "sharpe_from_daily_returns"]

RISK_FREE_ANNUAL = 0.045
TRADING_DAYS_PER_YEAR = 252
HISTORY_CALENDAR_DAYS = 380  # ~1y of trading days plus holiday slack
MIN_RETURN_DAYS = 60  # below this the annualized number is noise
# A blended daily return is only used when holdings covering at least this share
# of the stock weights have a bar that day (new listings/halts drop out cleanly).
MIN_DAILY_COVERAGE = 0.6


# ─── Pure math (unit-tested directly) ─────────────────────────────────────────


def blend_daily_returns(
    returns_by_symbol: dict[str, dict[str, float]],
    weights: dict[str, float],
    *,
    min_coverage: float = MIN_DAILY_COVERAGE,
) -> list[float]:
    """Weighted daily portfolio returns from per-symbol {date: return} series.

    For each date (chronological), blends the returns of the symbols that have a
    bar that day, re-normalized to the weight actually present; dates where the
    covered weight falls below ``min_coverage`` of the total are skipped rather
    than pretending the missing names returned 0%.
    """
    total_weight = sum(w for w in weights.values() if w > 0)
    if total_weight <= 0:
        return []
    dates = sorted({d for series in returns_by_symbol.values() for d in series})
    blended: list[float] = []
    for day in dates:
        covered = 0.0
        acc = 0.0
        for symbol, weight in weights.items():
            if weight <= 0:
                continue
            r = returns_by_symbol.get(symbol, {}).get(day)
            if r is not None:
                covered += weight
                acc += weight * r
        if covered / total_weight >= min_coverage:
            blended.append(acc / covered)
    return blended


def sharpe_from_daily_returns(
    daily_returns: list[float],
    *,
    rf_annual: float = RISK_FREE_ANNUAL,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
    min_days: int = MIN_RETURN_DAYS,
) -> dict[str, float] | None:
    """Annualized Sharpe (plus return/vol) from a daily return series.

    None when the series is too short or has no variance — an infinite/NaN
    Sharpe would be a bug dressed as a number.
    """
    if len(daily_returns) < min_days:
        return None
    stdev = statistics.stdev(daily_returns)
    if stdev == 0 or not math.isfinite(stdev):
        return None
    rf_daily = rf_annual / periods_per_year
    mean = statistics.fmean(daily_returns)
    sharpe = (mean - rf_daily) / stdev * math.sqrt(periods_per_year)
    return {
        "sharpe": round(sharpe, 2),
        "annualized_return_pct": round(((1 + mean) ** periods_per_year - 1) * 100, 2),
        "annualized_vol_pct": round(stdev * math.sqrt(periods_per_year) * 100, 2),
    }


def _daily_returns_from_closes(closes: dict[str, float]) -> dict[str, float]:
    """{date: close-to-close return} from {date: close}, in date order."""
    out: dict[str, float] = {}
    prev: float | None = None
    for day in sorted(closes):
        close = closes[day]
        if prev is not None and prev > 0:
            out[day] = close / prev - 1
        prev = close
    return out


# ─── Data fetch (cached) ──────────────────────────────────────────────────────

# symbol -> (fetched_at_monotonic, {date: close} | None). Daily closes only move
# once a trading day, so hours-long caching is safe and keeps a 20-name book from
# re-pulling 20 years-of-bars requests per visit.
_CLOSES_TTL = 6 * 3600.0
_closes_cache: dict[str, tuple[float, dict[str, float] | None]] = {}


def _fetch_daily_closes(client: Any, symbol: str) -> dict[str, float] | None:
    """~1 year of {iso-date: close} for one symbol, or None on failure."""
    today = datetime.date.today()
    start = (today - datetime.timedelta(days=HISTORY_CALENDAR_DAYS)).isoformat()
    try:
        data = client.get_daily_aggregates(symbol, start, today.isoformat())
    except Exception as exc:  # noqa: BLE001 — best-effort per symbol
        logger.debug("Daily closes fetch failed for %s: %s", symbol, type(exc).__name__)
        return None
    results = data.get("results") if isinstance(data, dict) else None
    closes: dict[str, float] = {}
    for r in results or []:
        if not isinstance(r, dict) or r.get("c") is None or r.get("t") is None:
            continue
        day = datetime.datetime.fromtimestamp(r["t"] / 1000, tz=datetime.timezone.utc).date().isoformat()
        closes[day] = float(r["c"])
    return closes or None


async def _closes_for_symbols(symbols: list[str]) -> dict[str, dict[str, float]]:
    """Cached, concurrent daily-close fetch for many symbols (best-effort each)."""
    client = portfolio_overview._shared_massive_client()  # noqa: SLF001 — same package seam

    async def _one(sym: str) -> tuple[str, dict[str, float] | None]:
        cached = _closes_cache.get(sym)
        if cached and (time.monotonic() - cached[0]) < _CLOSES_TTL:
            return sym, cached[1]
        res = await asyncio.to_thread(_fetch_daily_closes, client, sym)
        # Cache successes only — negative-caching a transient provider failure
        # would blank the stat for hours (the 30-min stats cache already bounds
        # how often a persistently failing symbol is retried).
        if res is not None:
            _closes_cache[sym] = (time.monotonic(), res)
        return sym, res

    resolved = await asyncio.gather(*[_one(s) for s in symbols])
    return {sym: closes for sym, closes in resolved if closes}


# ─── Public entry (cached per user) ──────────────────────────────────────────

_STATS_TTL = 1800.0  # a half-hour: weights drift slowly, closes change daily
_stats_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def invalidate_stats_cache(user_id: str | None = None) -> None:
    """Drop cached stats (all users when ``user_id`` is None — used by tests)."""
    if user_id is None:
        _stats_cache.clear()
    else:
        _stats_cache.pop(user_id, None)


async def build_stats(*, force: bool = False) -> dict[str, Any]:
    """Approximate Sharpe payload for the current user's combined portfolio.

    Always returns a dict; ``available: False`` carries a ``reason`` the UI can
    show ("no_brokerage", "insufficient_history", "no_price_data").
    """
    from app.backend.context import current_user_id

    uid = current_user_id()
    now = time.monotonic()
    cached = _stats_cache.get(uid)
    if not force and cached and (now - cached[0]) < _STATS_TTL:
        return cached[1]

    result = await _build_stats_uncached()
    _stats_cache[uid] = (time.monotonic(), result)
    return result


async def _build_stats_uncached() -> dict[str, Any]:
    overview = await portfolio_overview.build_overview()
    if not overview.get("connected"):
        return {"available": False, "reason": "no_brokerage"}
    account = overview.get("combined") or (overview.get("accounts") or [{}])[0]
    positions = account.get("positions") or []

    # Current stock weights by underlying (options carry no clean daily series).
    values: dict[str, float] = {}
    for p in positions:
        if p.get("kind") != "stock" or not p.get("underlying"):
            continue
        value = p.get("current_value")
        if value and value > 0:
            values[p["underlying"]] = values.get(p["underlying"], 0.0) + value
    stock_total = sum(values.values())
    if stock_total <= 0:
        return {"available": False, "reason": "no_price_data"}
    weights = {sym: v / stock_total for sym, v in values.items()}

    closes_by_symbol = await _closes_for_symbols(sorted(weights))
    returns_by_symbol = {sym: _daily_returns_from_closes(closes) for sym, closes in closes_by_symbol.items()}
    blended = blend_daily_returns(returns_by_symbol, weights)
    stats = sharpe_from_daily_returns(blended)
    if stats is None:
        return {"available": False, "reason": "insufficient_history" if blended else "no_price_data"}

    account_total = account.get("total_value")
    return {
        "available": True,
        **stats,
        "rf_pct": round(RISK_FREE_ANNUAL * 100, 2),
        "days": len(blended),
        # Share of the account the stats actually describe (stocks only).
        "coverage_pct": round(stock_total / account_total * 100, 1) if account_total else None,
        "method": "constant-weight",
    }
