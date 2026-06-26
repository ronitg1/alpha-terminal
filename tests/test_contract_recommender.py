"""Tests for the pattern-scanner contract recommender (_choose_best_contract).

Pure selection logic over synthetic chain candidates — no network. Pins the
band rules (0.40-0.50 delta, 25-30 DTE) and the in-band-vs-fallback ordering.
"""
from __future__ import annotations

from app.backend.routes.patterns import (
    _REC_DELTA_HI,
    _REC_DELTA_LO,
    _REC_DTE_HI,
    _REC_DTE_LO,
    _choose_best_contract,
)

# A bullish underlying plan: enter 100, stop 95, measured-move target 110.
PLAN = {"entry": 100.0, "stop": 95.0, "target": 110.0}


def _c(delta, dte, strike, mid=3.0, iv=0.40, typ="call"):
    return {
        "ticker": f"O:TEST{strike}",
        "type": typ,
        "strike": float(strike),
        "expiration": "2026-07-24",
        "dte": dte,
        "mid": mid,
        "iv": iv,
        "delta": delta,
    }


def test_picks_in_band_contract():
    cands = [
        _c(0.20, 28, 108),   # delta too low — out
        _c(0.70, 28, 92),    # delta too high — out
        _c(0.45, 40, 101),   # DTE too long — out
        _c(0.45, 28, 101),   # in-band
        _c(0.42, 26, 103),   # in-band
    ]
    plan = _choose_best_contract(cands, underlying_plan=PLAN, spot=100.0, hold_days=10.0)
    assert plan is not None
    assert _REC_DELTA_LO <= abs(plan["delta"]) <= _REC_DELTA_HI
    assert _REC_DTE_LO <= plan["dte"] <= _REC_DTE_HI
    assert "best payoff-per-dollar" in plan["recommendation_basis"]


def test_falls_back_to_nearest_when_none_in_band():
    # All out of band (delta too high), but priceable — recommend the closest.
    cands = [_c(0.70, 28, 92), _c(0.65, 45, 90)]
    plan = _choose_best_contract(cands, underlying_plan=PLAN, spot=100.0, hold_days=10.0)
    assert plan is not None
    assert "closest available" in plan["recommendation_basis"]
    # Nearest to DTE 27 should win over the 45-DTE one.
    assert plan["dte"] == 28


def test_empty_candidates_returns_none():
    assert _choose_best_contract([], underlying_plan=PLAN, spot=100.0, hold_days=10.0) is None


def test_candidates_without_delta_ignored():
    cands = [_c(None, 28, 101)]
    assert _choose_best_contract(cands, underlying_plan=PLAN, spot=100.0, hold_days=10.0) is None
