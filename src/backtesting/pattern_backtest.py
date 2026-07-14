"""Pattern-scanner backtest service (callable, no route).

Wires the ported Vibe-Trading engine (:mod:`src.backtesting.vibe_engine`)
to our chart-pattern detectors: fetch daily OHLCV from Massive, replay every
historical pattern detection as a signed-confidence signal, execute
next-bar-open through ``GlobalEquityEngine``, and return metrics plus
statistical validation.

Synchronous by design so a route can call it via ``asyncio.to_thread`` later.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.backtesting.vibe_engine.equity import GlobalEquityEngine
from src.backtesting.vibe_engine.loaders import MassiveLoader
from src.backtesting.vibe_engine.signals import PatternSignalEngine
from src.patterns.patterns import PATTERN_DETECTORS

logger = logging.getLogger(__name__)

# Defensive bounds — a UI or LLM caller should not be able to queue an
# unbounded scan through this service.
MAX_TICKERS = 50
MAX_HOLD_BARS = 100
MAX_RANGE_DAYS = 366 * 10  # ten years of daily bars

_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

# Where run artifacts (equity.csv, trades.csv, run_card.json, ...) land.
_RUNS_ROOT = Path("outputs") / "backtests"


def run_pattern_backtest(
    tickers: list[str],
    start_date: str,
    end_date: str,
    *,
    timeframe: str = "day",
    hold: int = 10,
    patterns: list[str] | None = None,
    initial_capital: float = 100_000,
) -> dict[str, Any]:
    """Backtest the chart-pattern scanner's signals over a date range.

    Args:
        tickers: US equity symbols (1..50).
        start_date: Inclusive start, ``YYYY-MM-DD``.
        end_date: Inclusive end, ``YYYY-MM-DD``.
        timeframe: Bar size. Only ``"day"`` is supported (the Massive client
            has no stock intraday aggregates method).
        hold: Bars a detection's signal stays active (1..100).
        patterns: Pattern names to trade; ``None`` = all twelve detectors.
        initial_capital: Starting cash (> 0).

    Returns:
        Dict with ``metrics`` (scalar performance stats + per-symbol /
        per-exit-reason breakdowns), ``validation`` (Monte Carlo, bootstrap
        Sharpe CI, walk-forward), ``equity_curve`` (list of
        ``{date, equity}``), ``trades`` (list of round-trip trade dicts), and
        ``config`` (the effective engine configuration).

    Raises:
        ValueError: On invalid tickers, dates, hold, capital, or patterns.
        NotImplementedError: For non-daily timeframes.
    """
    tickers = _validate_tickers(tickers)
    start_date, end_date = _validate_dates(start_date, end_date)
    if str(timeframe).lower() not in ("day", "1d", "d", "daily"):
        raise NotImplementedError(
            f"timeframe={timeframe!r} is not supported — daily bars only "
            "(Massive stock aggregates are daily)."
        )
    hold = int(hold)
    if not 1 <= hold <= MAX_HOLD_BARS:
        raise ValueError(f"hold must be in [1, {MAX_HOLD_BARS}], got {hold}")
    initial_capital = float(initial_capital)
    if initial_capital <= 0:
        raise ValueError(f"initial_capital must be > 0, got {initial_capital}")

    # PatternSignalEngine validates pattern names itself.
    signal_engine = PatternSignalEngine(patterns=patterns, hold=hold)
    loader = MassiveLoader()

    config: dict[str, Any] = {
        "codes": tickers,
        "start_date": start_date,
        "end_date": end_date,
        "interval": "1D",
        "initial_cash": initial_capital,
        "source": "massive",
        "engine": "global_equity_us",
        "patterns": sorted(signal_engine.patterns),
        "hold": hold,
        "validation": {"monte_carlo": {}, "bootstrap": {}, "walk_forward": {}},
    }

    run_dir = _make_run_dir()
    engine = GlobalEquityEngine(config, market="us")
    metrics = engine.run_backtest(config, loader, signal_engine, run_dir, bars_per_year=252)

    validation = metrics.pop("validation", {})
    equity_curve = [
        {"date": snap.timestamp.strftime("%Y-%m-%d"), "equity": round(float(snap.equity), 2)}
        for snap in engine.equity_snapshots
    ]
    trades = [_trade_to_dict(t) for t in engine.trades]

    logger.info(
        "Pattern backtest done: %d tickers, %d trades, final=%.2f (artifacts: %s)",
        len(tickers), len(trades), metrics.get("final_value", 0.0), run_dir,
    )
    return {
        "metrics": metrics,
        "validation": validation,
        "equity_curve": equity_curve,
        "trades": trades,
        "config": config,
    }


def _validate_tickers(tickers: list[str]) -> list[str]:
    """Uppercase, dedupe (order-preserving), and bound the ticker list."""
    if not tickers:
        raise ValueError("tickers must be a non-empty list")
    cleaned: list[str] = []
    for raw in tickers:
        t = str(raw).strip().upper()
        if not _TICKER_RE.match(t):
            raise ValueError(f"Invalid ticker: {raw!r}")
        if t not in cleaned:
            cleaned.append(t)
    if len(cleaned) > MAX_TICKERS:
        raise ValueError(f"Too many tickers: {len(cleaned)} (max {MAX_TICKERS})")
    return cleaned


def _validate_dates(start_date: str, end_date: str) -> tuple[str, str]:
    """Parse and bound the date range; returns normalized YYYY-MM-DD strings."""
    try:
        start = datetime.strptime(str(start_date)[:10], "%Y-%m-%d").date()
        end = datetime.strptime(str(end_date)[:10], "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"Dates must be YYYY-MM-DD: {exc}") from exc
    if start >= end:
        raise ValueError(f"start_date {start} must be before end_date {end}")
    if (end - start).days > MAX_RANGE_DAYS:
        raise ValueError(f"Date range too large: {(end - start).days} days (max {MAX_RANGE_DAYS})")
    return start.isoformat(), end.isoformat()


def _make_run_dir() -> Path:
    """Create a timestamped artifacts directory under ``outputs/backtests``."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S.%fZ")
    run_dir = _RUNS_ROOT / f"pattern_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _trade_to_dict(t: Any) -> dict[str, Any]:
    """Serialize a TradeRecord to a JSON-friendly dict."""
    return {
        "symbol": t.symbol,
        "direction": int(t.direction),
        "entry_date": t.entry_time.strftime("%Y-%m-%d"),
        "exit_date": t.exit_time.strftime("%Y-%m-%d"),
        "entry_price": round(float(t.entry_price), 4),
        "exit_price": round(float(t.exit_price), 4),
        "size": round(float(t.size), 6),
        "pnl": round(float(t.pnl), 2),
        "pnl_pct": round(float(t.pnl_pct), 2),
        "exit_reason": t.exit_reason,
        "holding_bars": int(t.holding_bars),
        "commission": round(float(t.commission), 4),
    }
