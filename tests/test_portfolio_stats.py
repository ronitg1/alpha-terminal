"""Portfolio risk stats: Sharpe math, return blending, and the /stats pipeline.

The math functions are pure and tested against hand-computed values; the
pipeline tests stub the overview + price-history seams so no network is touched.
"""
from __future__ import annotations

import math
import statistics

import anyio
import pytest

from app.backend.services import portfolio_stats as ps


@pytest.fixture(autouse=True)
def fresh_caches():
    """Stats + closes caches are module-level; isolate every test."""
    ps.invalidate_stats_cache()
    ps._closes_cache.clear()
    yield
    ps.invalidate_stats_cache()
    ps._closes_cache.clear()


# ─── sharpe_from_daily_returns ───────────────────────────────────────────────


def test_sharpe_matches_hand_computed_value():
    returns = [0.01, 0.02, 0.03]
    stats = ps.sharpe_from_daily_returns(returns, rf_annual=0.045, min_days=3)
    assert stats is not None
    mean = statistics.fmean(returns)  # 0.02
    expected = (mean - 0.045 / 252) / statistics.stdev(returns) * math.sqrt(252)
    assert stats["sharpe"] == pytest.approx(expected, abs=0.01)
    assert stats["annualized_vol_pct"] == pytest.approx(0.01 * math.sqrt(252) * 100, abs=0.01)
    assert stats["annualized_return_pct"] == pytest.approx(((1.02**252) - 1) * 100, rel=1e-4)


def test_sharpe_gates_short_and_flat_series():
    assert ps.sharpe_from_daily_returns([0.01] * 59) is None  # default min_days=60
    assert ps.sharpe_from_daily_returns([0.0] * 100) is None  # zero variance
    assert ps.sharpe_from_daily_returns([0.01] * 100) is None  # constant → zero variance


# ─── blend_daily_returns ─────────────────────────────────────────────────────


def test_blend_weights_and_renormalizes():
    returns = {
        "AAA": {"2026-01-02": 0.10, "2026-01-03": 0.10},
        "BBB": {"2026-01-02": -0.10},  # missing on the 3rd
    }
    weights = {"AAA": 0.6, "BBB": 0.4}
    blended = ps.blend_daily_returns(returns, weights, min_coverage=0.6)
    # Day 1: full coverage → 0.6*0.10 + 0.4*(-0.10) = 0.02.
    # Day 2: only AAA (coverage 0.6, at threshold) → renormalized to AAA's 0.10.
    assert blended == [pytest.approx(0.02), pytest.approx(0.10)]


def test_blend_skips_undercovered_days_and_empty_weights():
    returns = {"AAA": {"2026-01-02": 0.05}}
    # AAA is only 40% of the book → the day is dropped, not zero-filled.
    assert ps.blend_daily_returns(returns, {"AAA": 0.4, "BBB": 0.6}, min_coverage=0.6) == []
    assert ps.blend_daily_returns(returns, {}) == []
    assert ps.blend_daily_returns(returns, {"AAA": 0.0}) == []


# ─── build_stats pipeline (seams stubbed) ────────────────────────────────────


def _overview(connected: bool = True, positions: list | None = None) -> dict:
    account = {
        "id": "snaptrade:x",
        "total_value": 10_000.0,
        "positions": positions
        if positions is not None
        else [
            {"kind": "stock", "underlying": "AAA", "current_value": 6_000.0},
            {"kind": "stock", "underlying": "BBB", "current_value": 2_000.0},
            {"kind": "option", "underlying": "AAA", "current_value": 500.0},
        ],
    }
    return {"connected": connected, "accounts": [account], "combined": None}


def test_build_stats_reports_no_brokerage(monkeypatch: pytest.MonkeyPatch):
    async def fake_overview(**_kwargs):
        return _overview(connected=False)

    monkeypatch.setattr(ps.portfolio_overview, "build_overview", fake_overview)
    result = anyio.run(ps.build_stats)
    assert result == {"available": False, "reason": "no_brokerage"}


def test_build_stats_full_pipeline(monkeypatch: pytest.MonkeyPatch):
    async def fake_overview(**_kwargs):
        return _overview()

    # ~130 trading days of deterministic closes: AAA drifts up with a wobble,
    # BBB drifts down — enough variance for a finite Sharpe.
    def closes(base: float, step: float) -> dict[str, float]:
        out = {}
        for i in range(130):
            day = f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}"
            out[day] = base + step * i + (1.5 if i % 2 else 0.0)
        return out

    async def fake_closes(symbols):
        assert symbols == ["AAA", "BBB"]
        return {"AAA": closes(100.0, 0.3), "BBB": closes(50.0, -0.05)}

    monkeypatch.setattr(ps.portfolio_overview, "build_overview", fake_overview)
    monkeypatch.setattr(ps, "_closes_for_symbols", fake_closes)

    result = anyio.run(ps.build_stats)
    assert result["available"] is True
    assert isinstance(result["sharpe"], float)
    assert result["days"] == 129  # first close has no prior day
    assert result["rf_pct"] == 4.5
    # Stocks are 8k of the 10k account; the option is excluded from coverage.
    assert result["coverage_pct"] == 80.0

    # Second call is served from the per-user cache (stub would fail the
    # symbols assertion if refetched with different state).
    assert anyio.run(ps.build_stats) is result


def test_build_stats_insufficient_history(monkeypatch: pytest.MonkeyPatch):
    async def fake_overview(**_kwargs):
        return _overview()

    async def fake_closes(_symbols):
        return {"AAA": {"2026-01-02": 100.0, "2026-01-03": 101.0}, "BBB": {"2026-01-02": 50.0, "2026-01-03": 50.5}}

    monkeypatch.setattr(ps.portfolio_overview, "build_overview", fake_overview)
    monkeypatch.setattr(ps, "_closes_for_symbols", fake_closes)
    result = anyio.run(ps.build_stats)
    assert result == {"available": False, "reason": "insufficient_history"}
