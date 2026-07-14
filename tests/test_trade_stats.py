"""Rigorous-stats adapter: metrics + validation from realized trade PnLs."""
from __future__ import annotations

from src.backtesting.trade_stats import rigorous_stats


def test_too_few_trades_unavailable():
    out = rigorous_stats([100.0, -50.0], initial_capital=10_000)
    assert out["available"] is False
    assert "3" in out["reason"]


def test_winning_series_positive_sharpe_and_validation_keys():
    pnls = [120, 90, -40, 110, 80, -30, 140, 60, -20, 100, 70, -25]
    out = rigorous_stats(pnls, initial_capital=10_000)
    assert out["available"] is True
    m = out["metrics"]
    assert m["sharpe"] > 0
    assert m["n_trades"] == 12
    assert 0 <= m["win_rate"] <= 1
    v = out["validation"]
    assert "p_value_sharpe" in v["monte_carlo"]
    assert "ci_lower" in v["bootstrap"] and "ci_upper" in v["bootstrap"]
    assert "consistency_rate" in v["walk_forward"]


def test_losing_series_negative_sharpe():
    pnls = [-100, -90, 20, -110, -80, 15, -140, -60, 10, -100]
    out = rigorous_stats(pnls, initial_capital=10_000)
    assert out["available"] is True
    assert out["metrics"]["sharpe"] < 0


def test_dates_are_respected_for_windows():
    pnls = [10, 20, -5, 30, 15, -10, 25, 5]
    dates = [f"2026-01-{d:02d}" for d in range(1, 9)]
    out = rigorous_stats(pnls, initial_capital=5_000, dates=dates)
    assert out["available"] is True
    assert out["validation"]["walk_forward"].get("n_windows", 0) >= 2
