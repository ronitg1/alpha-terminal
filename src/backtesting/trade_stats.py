"""Rigorous, Vibe-Trading-grade stats from a list of realized trade PnLs.

Our options backtests (chart-pattern replay in ``routes/patterns.py`` and the
sleeve options-strategy backtest in ``routes/sleeves.py``) already model
execution realistically (real option chains + slippage + profit/stop/DTE exits).
What they lacked versus the ported Vibe-Trading engine is *statistical
validation* of the results. This adapter adds exactly that: from the sequence of
realized trade PnLs it builds an equity curve and runs the same walk-forward,
Monte-Carlo permutation, and bootstrap-Sharpe checks (plus risk-adjusted
metrics) the ``vibe_engine`` uses — so a backtest result can say not just "this
made money" but "…and it's unlikely to be luck."

Pure/synchronous and dependency-light (numpy + pandas + the vibe_engine
validators), so it can be called from a route or a background thread and unit
tested with a plain list of floats.
"""
from __future__ import annotations

import datetime
from typing import Any, Sequence

import numpy as np
import pandas as pd

from src.backtesting.vibe_engine import validation as _v
from src.backtesting.vibe_engine.models import TradeRecord


def _to_timestamps(dates: Sequence[Any] | None, n: int) -> list[pd.Timestamp]:
    """N chronological timestamps for the trades. Uses the supplied dates when
    given (parsed leniently); otherwise synthesizes consecutive business days so
    the window/return math still works."""
    if dates and len(dates) == n:
        out: list[pd.Timestamp] = []
        base = pd.Timestamp("2000-01-03")
        for i, d in enumerate(dates):
            try:
                ts = pd.Timestamp(d)
                if pd.isna(ts):
                    raise ValueError
            except Exception:  # noqa: BLE001 — fall back to a synthetic slot
                ts = base + pd.tseries.offsets.BDay(i)
            out.append(ts)
        return out
    base = pd.Timestamp(datetime.date.today()) - pd.tseries.offsets.BDay(n)
    return [base + pd.tseries.offsets.BDay(i) for i in range(n)]


def _trade_records(pnls: Sequence[float], stamps: list[pd.Timestamp]) -> list[TradeRecord]:
    """Minimal TradeRecords the validators need (they read .pnl and .entry_time)."""
    return [
        TradeRecord(
            symbol="", direction=1, entry_price=0.0, exit_price=0.0,
            entry_time=stamps[i], exit_time=stamps[i], size=0.0, leverage=1.0,
            pnl=float(p), pnl_pct=0.0, exit_reason="", holding_bars=0, commission=0.0,
        )
        for i, p in enumerate(pnls)
    ]


def _headline_metrics(equity: pd.Series, pnls: np.ndarray, bars_per_year: int) -> dict[str, Any]:
    rets = equity.pct_change().dropna().to_numpy()
    n = len(rets)
    std = rets.std() if n else 0.0
    sharpe = float(rets.mean() / (std + 1e-10) * np.sqrt(bars_per_year)) if n else 0.0
    downside = rets[rets < 0]
    dstd = downside.std() if len(downside) else 0.0
    sortino = float(rets.mean() / (dstd + 1e-10) * np.sqrt(bars_per_year)) if n else 0.0
    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1) if equity.iloc[0] else 0.0
    ann_return = float((1 + total_return) ** (bars_per_year / max(n, 1)) - 1) if n else 0.0
    peak = equity.cummax()
    dd = (equity - peak) / peak.replace(0, 1)
    max_dd = float(dd.min())
    calmar = float(ann_return / abs(max_dd)) if max_dd < 0 else 0.0
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = len(wins) / len(pnls) if len(pnls) else 0.0
    gross_win, gross_loss = float(sum(wins)), abs(float(sum(losses)))
    profit_factor = float(gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win else 0.0)
    return {
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "calmar": round(calmar, 3),
        "max_drawdown": round(max_dd, 4),
        "total_return": round(total_return, 4),
        "annual_return": round(ann_return, 4),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 3) if np.isfinite(profit_factor) else None,
        "n_trades": len(pnls),
    }


def rigorous_stats(
    trade_pnls: Sequence[float],
    *,
    initial_capital: float,
    dates: Sequence[Any] | None = None,
    bars_per_year: int = 252,
) -> dict[str, Any]:
    """Risk-adjusted metrics + statistical validation for a set of realized trades.

    Args:
        trade_pnls: Realized $ PnL per closed trade, in chronological order.
        initial_capital: Starting capital for the equity path.
        dates: Optional per-trade timestamps (aligned with ``trade_pnls``) used to
            bucket the walk-forward windows; synthesized if absent.
        bars_per_year: Annualisation factor (252 for daily).

    Returns ``{"available": False, "reason": ...}`` when there are too few trades,
    else ``{"available": True, "metrics": {...}, "validation": {...}}`` where
    ``validation`` carries ``monte_carlo`` / ``bootstrap`` / ``walk_forward`` blocks.
    """
    pnls = [float(p) for p in trade_pnls if p is not None and np.isfinite(p)]
    if len(pnls) < 3:
        return {"available": False, "reason": "Need at least 3 closed trades for validation."}

    stamps = _to_timestamps(dates, len(pnls))
    # Sort chronologically by timestamp, keeping pnl<->stamp alignment.
    order = sorted(range(len(pnls)), key=lambda i: stamps[i])
    pnls = [pnls[i] for i in order]
    stamps = [stamps[i] for i in order]

    # Equity path: an initial point then one per realized trade.
    eq_index = [stamps[0] - pd.tseries.offsets.BDay(1)] + stamps
    eq_values = initial_capital + np.cumsum([0.0] + pnls)
    equity = pd.Series(eq_values, index=pd.DatetimeIndex(eq_index))

    trades = _trade_records(pnls, stamps)
    validation = {
        "monte_carlo": _v.monte_carlo_test(trades, initial_capital),
        "bootstrap": _v.bootstrap_sharpe_ci(equity, bars_per_year=bars_per_year),
        "walk_forward": _v.walk_forward_analysis(
            equity, trades, n_windows=min(5, max(2, len(pnls) // 3)), bars_per_year=bars_per_year
        ),
    }
    return {
        "available": True,
        "metrics": _headline_metrics(equity, np.array(pnls), bars_per_year),
        "validation": validation,
    }
