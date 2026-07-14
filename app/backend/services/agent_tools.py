"""Read-only LangChain tools for the agentic research assistant.

Each tool wraps an existing backend service or route callable and returns a
COMPACT JSON-serializable dict (big lists truncated) so the tool message stays
cheap in the LLM context. Tool bodies are defensive: any failure is converted
to ``{"error": "..."}`` instead of raising, so a bad call becomes a
self-correctable tool message rather than killing the agent loop.

Imports of the wrapped modules are deliberately lazy (inside tool bodies):
several live in route modules (``sleeves.py``, ``market.py``, ``patterns.py``)
that in turn import agent services — lazy imports break that cycle and keep
``build_agent_tools()`` importable in tests without pulling FastAPI routes.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Any

from langchain_core.tools import BaseTool, tool

logger = logging.getLogger(__name__)

_MAX_LIST = 15

# Canonical detector names, duplicated in docstrings so the LLM passes exact
# values. Cross-checked against PATTERN_DETECTORS in _canonical_pattern().
_PATTERN_NAMES = (
    "Bullish Flag, Bearish Flag, Bull Pennant, Double Bottom, Double Top, "
    "Head and Shoulders, Inverse Head and Shoulders, Ascending Triangle, "
    "Descending Triangle, Cup and Handle, Rising Wedge, Falling Wedge"
)


def _err(exc: BaseException, what: str) -> dict[str, str]:
    """Normalize any exception into a compact error payload for the LLM."""
    detail = getattr(exc, "detail", None) or str(exc) or type(exc).__name__
    logger.warning("Agent tool %s failed: %s: %s", what, type(exc).__name__, detail)
    return {"error": f"{what} failed: {str(detail)[:200]}"}


def _parse_tickers(tickers: str, *, cap: int = _MAX_LIST) -> list[str]:
    """Split a comma/space-separated ticker string into an upper-cased list."""
    raw = tickers.replace(",", " ").split()
    seen: list[str] = []
    for t in raw:
        sym = t.strip().upper()
        if sym and sym not in seen:
            seen.append(sym)
    return seen[:cap]


def _canonical_pattern(pattern: str) -> str | None:
    """Map a (possibly mis-cased) pattern name onto a PATTERN_DETECTORS key."""
    from src.patterns.patterns import PATTERN_DETECTORS

    lowered = pattern.strip().lower()
    for name in PATTERN_DETECTORS:
        if name.lower() == lowered:
            return name
    return None


@tool
async def get_quotes(tickers: str) -> dict[str, Any]:
    """Get live price quotes for one or more stock tickers.

    Args:
        tickers: Comma-separated ticker symbols, e.g. "NVDA,AAPL,TSLA".

    Returns per ticker: last price, previous close, percent change, and name.
    """
    try:
        from app.backend.routes.sleeves import get_quotes as _quotes

        symbols = _parse_tickers(tickers)
        if not symbols:
            return {"error": "No valid tickers given."}
        data = await _quotes(tickers=",".join(symbols))
        compact = {
            sym: {k: q.get(k) for k in ("last", "prev_close", "pct_change", "name")}
            for sym, q in (data.get("quotes") or {}).items()
        }
        return {"quotes": compact}
    except Exception as exc:  # noqa: BLE001 — tool must not raise
        return _err(exc, "get_quotes")


@tool
async def scan_patterns(tickers: str, timeframe: str = "day", lookback_days: int = 180) -> dict[str, Any]:
    """Scan tickers for classic chart patterns (flags, triangles, double tops, etc.).

    Args:
        tickers: Comma-separated ticker symbols, e.g. "NVDA,AMD".
        timeframe: One of "week", "day", "1h", "15m".
        lookback_days: How many calendar days of history to scan (default 180).

    Returns the top detected signals sorted by confidence.
    """
    try:
        from app.backend.routes.patterns import run_pattern_scan
        from src.patterns.patterns import PATTERN_DETECTORS

        symbols = _parse_tickers(tickers, cap=10)
        if not symbols:
            return {"error": "No valid tickers given."}
        results = await run_pattern_scan(symbols, list(PATTERN_DETECTORS), timeframe, int(lookback_days))
        top = [
            {
                "ticker": r.get("ticker"),
                "pattern": r.get("pattern"),
                "confidence": r.get("confidence"),
                "bullish": r.get("bullish"),
                "start_date": r.get("start_date"),
                "end_date": r.get("end_date"),
                "description": str(r.get("description") or "")[:160],
            }
            for r in results[:_MAX_LIST]
        ]
        return {"timeframe": timeframe, "total_signals": len(results), "signals": top}
    except Exception as exc:  # noqa: BLE001
        return _err(exc, "scan_patterns")


@tool
async def get_signal_win_rate(ticker: str, pattern: str, timeframe: str = "day") -> dict[str, Any]:
    """Get the historical win rate of a chart pattern signal on one ticker.

    Args:
        ticker: One ticker symbol, e.g. "NVDA".
        pattern: Exact pattern name, one of: Bullish Flag, Bearish Flag,
            Bull Pennant, Double Bottom, Double Top, Head and Shoulders,
            Inverse Head and Shoulders, Ascending Triangle, Descending
            Triangle, Cup and Handle, Rising Wedge, Falling Wedge.
        timeframe: One of "week", "day", "1h", "15m".

    Returns historical signal count, win rate percent, and average win/loss size.
    """
    try:
        from app.backend.routes.patterns import signal_analysis

        name = _canonical_pattern(pattern)
        if name is None:
            return {"error": f"Unknown pattern '{pattern}'. Valid: {_PATTERN_NAMES}."}
        data = await signal_analysis(ticker.strip().upper(), name, timeframe)
        return {k: v for k, v in data.items() if k != "options"}
    except Exception as exc:  # noqa: BLE001
        return _err(exc, "get_signal_win_rate")


@tool
async def get_trade_plan(ticker: str, pattern: str, timeframe: str = "day") -> dict[str, Any]:
    """Build a concrete trade plan (entry, stop, target, option idea) for a pattern signal.

    Args:
        ticker: One ticker symbol, e.g. "NVDA".
        pattern: Exact pattern name, one of: Bullish Flag, Bearish Flag,
            Bull Pennant, Double Bottom, Double Top, Head and Shoulders,
            Inverse Head and Shoulders, Ascending Triangle, Descending
            Triangle, Cup and Handle, Rising Wedge, Falling Wedge.
        timeframe: One of "week", "day", "1h", "15m".

    Returns entry/stop/target levels with risk-reward, plus an option contract idea.
    """
    try:
        from app.backend.routes.patterns import trade_plan

        name = _canonical_pattern(pattern)
        if name is None:
            return {"error": f"Unknown pattern '{pattern}'. Valid: {_PATTERN_NAMES}."}
        return await trade_plan(ticker.strip().upper(), name, "moderate", timeframe)
    except Exception as exc:  # noqa: BLE001
        return _err(exc, "get_trade_plan")


@tool
async def get_market_movers() -> dict[str, Any]:
    """Get today's top market gainers and losers (US equities).

    Returns two lists (gainers, losers) with ticker, name, price, and percent change.
    """
    try:
        from app.backend.routes.market import get_movers

        return await get_movers()
    except Exception as exc:  # noqa: BLE001
        return _err(exc, "get_market_movers")


@tool
async def get_market_snapshot() -> dict[str, Any]:
    """Get a snapshot of major indices, crypto, and metals (S&P 500, Nasdaq, BTC, gold, etc.).

    Returns last price, previous close, and percent change per instrument.
    """
    try:
        from app.backend.routes.market import get_indices

        data = await get_indices()
        compact = [
            {k: row.get(k) for k in ("label", "symbol", "last", "change", "change_pct")}
            for row in (data.get("indices") or [])
        ]
        return {"indices": compact}
    except Exception as exc:  # noqa: BLE001
        return _err(exc, "get_market_snapshot")


@tool
async def get_catalyst_calendar(tickers: str = "") -> dict[str, Any]:
    """Get upcoming market catalysts: macro events (Fed, CPI, jobs) plus earnings dates.

    Args:
        tickers: Optional comma-separated tickers to include earnings dates for.
            Leave empty for macro events and notable earnings only.

    Returns dated catalyst entries, soonest first.
    """
    try:
        from app.backend.routes.market import get_catalysts

        symbols = _parse_tickers(tickers) if tickers.strip() else []
        data = await get_catalysts(tickers=",".join(symbols), days=60)
        return {
            "as_of": data.get("as_of"),
            "catalysts": (data.get("catalysts") or [])[:_MAX_LIST],
        }
    except Exception as exc:  # noqa: BLE001
        return _err(exc, "get_catalyst_calendar")


@tool
async def get_ticker_news(ticker: str) -> dict[str, Any]:
    """Get recent news headlines for one ticker (last 7 days).

    Args:
        ticker: One ticker symbol, e.g. "NVDA".

    Returns recent headlines with source, date, and a short summary.
    """
    try:
        from app.backend.services.finnhub_news import ticker_feed

        sym = ticker.strip().upper()
        articles = await asyncio.to_thread(ticker_feed, sym)
        compact = []
        for a in articles[:8]:
            ts = a.get("datetime")
            date = (
                datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).date().isoformat()
                if isinstance(ts, (int, float)) and ts > 0
                else None
            )
            compact.append(
                {
                    "headline": a.get("headline"),
                    "source": a.get("source"),
                    "date": date,
                    "summary": str(a.get("summary") or "")[:200],
                }
            )
        return {"ticker": sym, "articles": compact}
    except Exception as exc:  # noqa: BLE001
        return _err(exc, "get_ticker_news")


@tool
async def get_portfolio_overview() -> dict[str, Any]:
    """Get the user's connected brokerage portfolio: accounts, value, gains, and top positions.

    Returns account totals plus the largest positions by current value.
    """
    try:
        from app.backend.services.portfolio_overview import build_overview

        data = await build_overview()
        if not data.get("connected"):
            return {"connected": False, "note": "No brokerage account is connected."}

        def _compact_account(acct: dict[str, Any]) -> dict[str, Any]:
            positions = acct.get("positions") or []
            top = sorted(
                (p for p in positions if isinstance(p, dict)),
                key=lambda p: p.get("current_value") or 0,
                reverse=True,
            )[:_MAX_LIST]
            return {
                **{
                    k: acct.get(k)
                    for k in ("label", "source", "cash", "total_value", "day_change_pct", "total_gain_pct")
                },
                "position_count": len(positions),
                "top_positions": [
                    {
                        k: p.get(k)
                        for k in (
                            "symbol",
                            "quantity",
                            "last_price",
                            "current_value",
                            "pct_of_account",
                            "day_change_pct",
                            "total_gain_pct",
                        )
                    }
                    for p in top
                ],
            }

        combined = data.get("combined")
        accounts = data.get("accounts") or []
        return {
            "connected": True,
            "sources": data.get("sources"),
            "accounts": [_compact_account(a) for a in accounts if isinstance(a, dict)],
            "combined": _compact_account(combined) if isinstance(combined, dict) else None,
        }
    except Exception as exc:  # noqa: BLE001
        return _err(exc, "get_portfolio_overview")


@tool
async def get_portfolio_stats() -> dict[str, Any]:
    """Get risk statistics for the user's portfolio: Sharpe ratio, annualized return and volatility.

    Returns the computed stats, or availability info when history is insufficient.
    """
    try:
        from app.backend.services.portfolio_stats import build_stats

        return await build_stats()
    except Exception as exc:  # noqa: BLE001
        return _err(exc, "get_portfolio_stats")


@tool
async def get_ownership(tickers: str) -> dict[str, Any]:
    """Get institutional ownership changes (13F filings) for tickers from famous funds.

    Args:
        tickers: Comma-separated ticker symbols, e.g. "NVDA,PLTR".

    Returns per ticker: which tracked institutions hold it and whether they
    added, trimmed, opened, or exited the position last quarter.
    """
    try:
        from app.backend.services.ownership_service import build_ownership

        symbols = _parse_tickers(tickers, cap=5)
        if not symbols:
            return {"error": "No valid tickers given."}
        return await asyncio.to_thread(build_ownership, symbols)
    except Exception as exc:  # noqa: BLE001
        return _err(exc, "get_ownership")


@tool
async def get_valuation(ticker: str) -> dict[str, Any]:
    """Get a fair-value estimate for one ticker (P/E, growth, and band-based methods).

    Args:
        ticker: One ticker symbol, e.g. "NVDA".

    Returns current price, blended fair value, upside percent, and per-method bands.
    """
    try:
        from app.backend.services.valuation_service import compute_valuation

        return await asyncio.to_thread(compute_valuation, ticker.strip().upper())
    except Exception as exc:  # noqa: BLE001
        return _err(exc, "get_valuation")


@tool
async def get_watchlists() -> dict[str, Any]:
    """List the user's saved watchlists and the tickers in each.

    Use this whenever the user mentions "my watchlist" or "my watchlists" — it is
    the correct source for the tickers they follow (distinct from their brokerage
    portfolio holdings).
    """
    try:
        from app.backend.services import watchlists_service

        wls = await asyncio.to_thread(watchlists_service.get_all)
        out = []
        for wl in wls[:_MAX_LIST]:
            tickers = [
                (t.get("ticker") if isinstance(t, dict) else str(t))
                for t in (wl.get("tickers") or [])
            ]
            out.append({"name": wl.get("name"), "tickers": [t for t in tickers if t][:60]})
        return {"watchlists": out}
    except Exception as exc:  # noqa: BLE001
        return _err(exc, "get_watchlists")


@tool
async def scan_watchlist(watchlist: str = "", timeframe: str = "day", lookback_days: int = 180) -> dict[str, Any]:
    """Scan the user's watchlist(s) for chart patterns in one call.

    Use this when the user asks "what patterns are on my watchlist(s)". Resolves
    the watchlist tickers and runs the pattern scan — no need to call get_watchlists
    then scan_patterns separately.

    Args:
        watchlist: Name of a specific watchlist, or empty to scan ALL watchlists.
        timeframe: One of "week", "day", "1h", "15m".
        lookback_days: How many calendar days of history to scan (default 180).

    Returns the top detected signals across the watchlist tickers, sorted by confidence.
    """
    try:
        from app.backend.routes.patterns import run_pattern_scan
        from app.backend.services import watchlists_service
        from src.patterns.patterns import PATTERN_DETECTORS

        wls = await asyncio.to_thread(watchlists_service.get_all)
        want = watchlist.strip().lower()
        tickers: list[str] = []
        for wl in wls:
            if want and str(wl.get("name") or "").lower() != want:
                continue
            for t in wl.get("tickers") or []:
                sym = ((t.get("ticker") if isinstance(t, dict) else str(t)) or "").strip().upper()
                if sym and sym not in tickers:
                    tickers.append(sym)
        if not tickers:
            label = f" '{watchlist}'" if watchlist.strip() else "s"
            return {"error": f"No tickers found in watchlist{label}."}
        tickers = tickers[:40]  # bound the scan
        results = await run_pattern_scan(tickers, list(PATTERN_DETECTORS), timeframe, int(lookback_days))
        top = [
            {
                "ticker": r.get("ticker"),
                "pattern": r.get("pattern"),
                "confidence": r.get("confidence"),
                "bullish": r.get("bullish"),
                "end_date": r.get("end_date"),
            }
            for r in results[:_MAX_LIST]
        ]
        return {
            "watchlist": watchlist or "all",
            "tickers_scanned": len(tickers),
            "timeframe": timeframe,
            "total_signals": len(results),
            "signals": top,
        }
    except Exception as exc:  # noqa: BLE001
        return _err(exc, "scan_watchlist")


def _compact_backtest(result: dict[str, Any]) -> dict[str, Any]:
    """Headline metrics + validation + trade count (drop the big equity/trade arrays)."""
    metrics = result.get("metrics") or {}
    keep = (
        "total_return", "annual_return", "sharpe", "sortino", "calmar",
        "max_drawdown", "win_rate", "profit_factor", "total_turnover",
    )
    return {
        "metrics": {k: metrics.get(k) for k in keep if k in metrics},
        "validation": result.get("validation"),
        "trade_count": len(result.get("trades") or []),
    }


@tool
async def backtest_strategy(tickers: str, start_date: str, end_date: str, hold: int = 10) -> dict[str, Any]:
    """Backtest the chart-pattern strategy on tickers over a date range (DAILY bars).

    Simulates entering on each detected chart-pattern signal, holding it for `hold`
    trading days, and reports performance WITH statistical validation (walk-forward
    consistency, Monte-Carlo permutation p-value, bootstrap Sharpe confidence
    interval). Use this to judge whether a pattern strategy actually works.

    Args:
        tickers: Comma-separated tickers, e.g. "NVDA,AMD" (max 8).
        start_date: ISO date "YYYY-MM-DD".
        end_date: ISO date "YYYY-MM-DD".
        hold: Trading days to hold each signal (default 10).

    Returns headline metrics (Sharpe, return, max drawdown, win rate), the
    validation block, and trade count. Daily timeframe only.
    """
    try:
        from src.backtesting.pattern_backtest import run_pattern_backtest

        symbols = _parse_tickers(tickers, cap=8)
        if not symbols:
            return {"error": "No valid tickers given."}
        result = await asyncio.to_thread(
            run_pattern_backtest, symbols, start_date, end_date, timeframe="day", hold=int(hold)
        )
        return {"tickers": symbols, "start_date": start_date, "end_date": end_date,
                "hold_days": int(hold), **_compact_backtest(result)}
    except Exception as exc:  # noqa: BLE001
        return _err(exc, "backtest_strategy")


def _held_stock_tickers(account: dict[str, Any], cap: int) -> list[str]:
    out: list[str] = []
    for p in account.get("positions") or []:
        if not isinstance(p, dict):
            continue
        if p.get("kind") not in (None, "stock", "equity"):
            continue
        sym = (p.get("underlying") or p.get("symbol") or "").strip().upper()
        if sym and sym not in out:
            out.append(sym)
    return out[:cap]


@tool
async def backtest_portfolio(start_date: str, end_date: str, hold: int = 10) -> dict[str, Any]:
    """Backtest the chart-pattern strategy on the user's CURRENT held stock positions.

    Same daily engine + validation as backtest_strategy, but the ticker list comes
    from the connected brokerage portfolio instead of being supplied.

    Args:
        start_date: ISO date "YYYY-MM-DD".
        end_date: ISO date "YYYY-MM-DD".
        hold: Trading days to hold each signal (default 10).
    """
    try:
        from app.backend.services.portfolio_overview import build_overview
        from src.backtesting.pattern_backtest import run_pattern_backtest

        overview = await build_overview()
        if not overview.get("connected"):
            return {"connected": False, "note": "No brokerage account is connected to backtest."}
        account = overview.get("combined") or (overview.get("accounts") or [{}])[0]
        tickers = _held_stock_tickers(account if isinstance(account, dict) else {}, cap=12)
        if not tickers:
            return {"error": "No stock positions found to backtest."}
        result = await asyncio.to_thread(
            run_pattern_backtest, tickers, start_date, end_date, timeframe="day", hold=int(hold)
        )
        return {"tickers": tickers, "start_date": start_date, "end_date": end_date,
                "hold_days": int(hold), **_compact_backtest(result)}
    except Exception as exc:  # noqa: BLE001
        return _err(exc, "backtest_portfolio")


@tool
async def analyze_portfolio() -> dict[str, Any]:
    """Run a full analysis of the user's portfolio in ONE call, for when the user
    asks to "analyze my portfolio".

    Gathers: holdings + weights + sectors, risk stats (Sharpe/vol), 13F
    institutional ownership changes for the top holdings, and a fair-value estimate
    for each top holding. You then synthesize a bull/bear read from the findings.
    """
    try:
        from app.backend.services.ownership_service import build_ownership
        from app.backend.services.portfolio_overview import build_overview
        from app.backend.services.portfolio_stats import build_stats
        from app.backend.services.valuation_service import compute_valuation

        overview = await build_overview()
        if not overview.get("connected"):
            return {"connected": False, "note": "No brokerage account is connected."}
        account = overview.get("combined") or (overview.get("accounts") or [{}])[0]
        account = account if isinstance(account, dict) else {}
        positions = [p for p in (account.get("positions") or []) if isinstance(p, dict)]
        top = sorted(positions, key=lambda p: p.get("current_value") or 0, reverse=True)[:8]
        top_syms: list[str] = []
        for p in top:
            sym = (p.get("underlying") or p.get("symbol") or "").strip().upper()
            if sym and sym not in top_syms:
                top_syms.append(sym)

        stats = await build_stats()
        ownership = await asyncio.to_thread(build_ownership, top_syms[:5]) if top_syms else {}
        valuations: dict[str, Any] = {}
        for sym in top_syms[:5]:
            valuations[sym] = await asyncio.to_thread(compute_valuation, sym)

        return {
            "connected": True,
            "totals": {
                k: account.get(k)
                for k in ("label", "total_value", "cash", "day_change_pct", "total_gain_pct")
            },
            "top_holdings": [
                {k: p.get(k) for k in ("symbol", "current_value", "pct_of_account", "sector", "total_gain_pct")}
                for p in top
            ],
            "stats": stats,
            "ownership": ownership,
            "valuations": valuations,
        }
    except Exception as exc:  # noqa: BLE001
        return _err(exc, "analyze_portfolio")


def build_agent_tools() -> list[BaseTool]:
    """All read-only tools exposed to the agent loop, in stable order."""
    return [
        get_quotes,
        get_watchlists,
        scan_watchlist,
        scan_patterns,
        get_signal_win_rate,
        get_trade_plan,
        get_market_movers,
        get_market_snapshot,
        get_catalyst_calendar,
        get_ticker_news,
        get_portfolio_overview,
        get_portfolio_stats,
        get_ownership,
        get_valuation,
        backtest_strategy,
        backtest_portfolio,
        analyze_portfolio,
    ]
