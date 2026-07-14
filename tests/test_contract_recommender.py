"""Tests for the pattern-scanner contract recommender (_choose_best_contract).

Pure selection logic over synthetic chain candidates — no network. Pins that the
pick is the listed contract closest to the ~0.40 delta / ~30 DTE target.
"""
from __future__ import annotations

from app.backend.routes.patterns import (
    _REC_DELTA_TARGET,
    _REC_DTE_TARGET,
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


def test_target_constants_are_040_and_30():
    assert _REC_DELTA_TARGET == 0.40
    assert _REC_DTE_TARGET == 30


def test_picks_contract_nearest_target():
    cands = [
        _c(0.20, 28, 108),   # delta far from 0.40
        _c(0.70, 28, 92),    # delta far from 0.40
        _c(0.45, 40, 101),   # DTE far from 30
        _c(0.45, 28, 101),   # close
        _c(0.42, 30, 103),   # nearest to 0.40Δ / 30 DTE
    ]
    plan = _choose_best_contract(cands, underlying_plan=PLAN, spot=100.0, hold_days=10.0)
    assert plan is not None
    assert abs(plan["delta"]) == 0.42 and plan["dte"] == 30
    assert "0.40" in plan["recommendation_basis"] and "30-DTE" in plan["recommendation_basis"]


def test_prefers_040_delta_at_equal_dte():
    cands = [_c(0.55, 30, 98), _c(0.40, 30, 102)]
    plan = _choose_best_contract(cands, underlying_plan=PLAN, spot=100.0, hold_days=10.0)
    assert abs(plan["delta"]) == 0.40


def test_prefers_30_dte_at_equal_delta():
    cands = [_c(0.40, 20, 102), _c(0.40, 30, 102), _c(0.40, 45, 102)]
    plan = _choose_best_contract(cands, underlying_plan=PLAN, spot=100.0, hold_days=10.0)
    assert plan["dte"] == 30


def test_picks_nearest_even_when_all_far():
    # Both far from target, but priceable — recommend the closer one.
    cands = [_c(0.70, 28, 92), _c(0.65, 45, 90)]
    plan = _choose_best_contract(cands, underlying_plan=PLAN, spot=100.0, hold_days=10.0)
    assert plan is not None
    assert plan["dte"] == 28  # closer to 0.40Δ / 30 DTE than the 0.65Δ/45-DTE one


def test_empty_candidates_returns_none():
    assert _choose_best_contract([], underlying_plan=PLAN, spot=100.0, hold_days=10.0) is None


def test_candidates_without_delta_ignored():
    # No delta -> can't be delta-targeted -> not recommended.
    cands = [_c(None, 28, 101)]
    assert _choose_best_contract(cands, underlying_plan=PLAN, spot=100.0, hold_days=10.0) is None
