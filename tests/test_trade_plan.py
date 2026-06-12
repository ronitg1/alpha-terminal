"""Pattern-scanner trade-plan math: ATR, vol, and entry/stop/target geometry."""
from __future__ import annotations

import pytest

from src.patterns.trade_plan import (
    RISK_ATR_MULTIPLE,
    annualized_vol,
    build_option_plan,
    build_trade_plan,
    classify_signal,
    compute_atr,
    normalize_risk,
)


def _bars(values: list[float]) -> list[dict]:
    """Synthetic OHLC bars: a fixed 2-wide range around each close."""
    return [{"open": v, "high": v + 1, "low": v - 1, "close": v} for v in values]


def test_compute_atr_simple_range():
    # Every bar spans high-low = 2; prev-close gaps are small, so ATR ~ 2.
    atr = compute_atr(_bars([100 + i for i in range(20)]), period=14)
    assert atr == pytest.approx(2.0, abs=0.3)


def test_compute_atr_needs_history():
    assert compute_atr(_bars([100, 101, 102]), period=14) is None


def test_annualized_vol_positive():
    closes = _bars([100, 102, 101, 104, 103, 106, 105, 108, 107, 110, 109, 112])
    vol = annualized_vol(closes, lookback=10)
    assert vol is not None and vol > 0


def test_normalize_risk_defaults():
    assert normalize_risk("AGGRESSIVE") == "aggressive"
    assert normalize_risk("nonsense") == "moderate"
    assert normalize_risk(None) == "moderate"


def test_risk_tolerance_widens_the_stop():
    kl = {"channel_high": 100.0, "channel_low": 92.0, "pole_high": 110.0}
    common = dict(pattern="Bullish Flag", key_levels=kl, current_price=101.0, atr=2.0, bullish=True)
    cons = build_trade_plan(**common, risk="conservative")
    mod = build_trade_plan(**common, risk="moderate")
    agg = build_trade_plan(**common, risk="aggressive")
    # Entry is the breakout (channel_high). Wider tolerance -> lower stop.
    assert cons["entry"] == 100.0
    assert agg["stop"] < mod["stop"] < cons["stop"] < cons["entry"]
    # Stop distance == multiple * ATR.
    assert cons["risk_per_share"] == pytest.approx(RISK_ATR_MULTIPLE["conservative"] * 2.0)
    assert agg["risk_per_share"] == pytest.approx(RISK_ATR_MULTIPLE["aggressive"] * 2.0)


def test_bullish_flag_measured_move_and_rr():
    kl = {"channel_high": 100.0, "channel_low": 92.0, "pole_high": 110.0}
    plan = build_trade_plan(
        pattern="Bullish Flag", key_levels=kl, current_price=101.0,
        atr=2.0, bullish=True, risk="moderate",
    )
    # Entry 100, stop = 100 - 1.5*2 = 97, target = breakout + (pole_high-channel_low)=100+18=118
    assert plan["entry"] == 100.0
    assert plan["stop"] == 97.0
    assert plan["target"] == 118.0
    assert plan["direction"] == "long"
    assert plan["risk_reward"] == pytest.approx((118 - 100) / (100 - 97), abs=0.01)
    assert plan["already_triggered"] is True  # current 101 >= entry 100


def test_bearish_pattern_inverts_sides():
    kl = {"neckline": 50.0, "top_1": 56.0, "top_2": 55.0}
    plan = build_trade_plan(
        pattern="Double Top", key_levels=kl, current_price=49.0,
        atr=1.0, bullish=False, risk="moderate",
    )
    assert plan["direction"] == "short"
    # Entry 50 (neckline), stop above entry, target below.
    assert plan["stop"] > plan["entry"]
    assert plan["target"] < plan["entry"]
    assert plan["risk_reward"] is not None and plan["risk_reward"] > 0


def test_missing_levels_fall_back_to_atr_and_2R():
    plan = build_trade_plan(
        pattern="Bull Pennant", key_levels={}, current_price=200.0,
        atr=4.0, bullish=True, risk="moderate",
    )
    # No breakout level -> entry = current price; stop = 200 - 1.5*4 = 194.
    assert plan["entry"] == 200.0
    assert plan["stop"] == 194.0
    # 2R default target = entry + 2*risk = 200 + 12 = 212.
    assert plan["target"] == 212.0
    assert plan["risk_reward"] == pytest.approx(2.0)


def test_no_atr_uses_percent_fallback():
    plan = build_trade_plan(
        pattern="Cup and Handle",
        key_levels={"cup_lip": 50.0, "cup_bottom": 40.0, "handle_low": 47.0},
        current_price=50.0, atr=None, bullish=True, risk="moderate",
    )
    # atr None -> 2% of entry = 1.0; stop = 50 - 1.5*1.0 = 48.5
    assert plan["stop"] == 48.5
    assert plan["structural_invalidation"] == 47.0


# ─── Option-premium translation ──────────────────────────────────────────────


def _bull_plan() -> dict:
    """Underlying long plan: entry 100, stop 97, target 118."""
    return build_trade_plan(
        pattern="Bullish Flag",
        key_levels={"channel_high": 100.0, "channel_low": 92.0, "pole_high": 110.0},
        current_price=99.0, atr=2.0, bullish=True, risk="moderate",
    )


def _call_contract(**overrides) -> dict:
    base = {
        "ticker": "O:TEST260717C00100000", "type": "call", "strike": 100.0,
        "expiration": "2026-07-17", "dte": 30, "mid": 4.20, "iv": 0.40, "delta": 0.52,
    }
    base.update(overrides)
    return base


def test_option_plan_bullish_call_orders_premiums():
    plan = _bull_plan()
    opt = build_option_plan(underlying_plan=plan, spot=99.0, contract=_call_contract())
    assert opt is not None
    # Long premium: stop premium < entry premium < target premium.
    assert opt["stop_premium"] < opt["entry_premium"] < opt["target_premium"]
    assert opt["risk_per_contract"] > 0
    assert opt["max_loss_per_contract"] == pytest.approx(opt["entry_premium"] * 100)
    assert "Black-Scholes" in opt["pricing_basis"]
    assert opt["risk_reward"] is not None and opt["risk_reward"] > 0


def test_option_plan_bearish_put_still_long_premium():
    plan = build_trade_plan(
        pattern="Double Top", key_levels={"neckline": 50.0, "top_1": 56.0, "top_2": 55.0},
        current_price=51.0, atr=1.0, bullish=False, risk="moderate",
    )
    put = _call_contract(type="put", strike=50.0, mid=2.10, delta=-0.48)
    opt = build_option_plan(underlying_plan=plan, spot=51.0, contract=put)
    assert opt is not None
    # Underlying falls toward target -> put premium rises; stop (underlying up) -> premium falls.
    assert opt["stop_premium"] < opt["entry_premium"] < opt["target_premium"]


def test_option_plan_delta_fallback_without_iv():
    plan = _bull_plan()
    opt = build_option_plan(
        underlying_plan=plan, spot=99.0, contract=_call_contract(iv=None)
    )
    assert opt is not None
    assert "delta approximation" in opt["pricing_basis"]
    # Linear: entry = 4.20 + 0.52*(100-99) = 4.72; stop = 4.20 + 0.52*(97-99) = 3.16
    assert opt["entry_premium"] == pytest.approx(4.72, abs=0.01)
    assert opt["stop_premium"] == pytest.approx(3.16, abs=0.01)


def test_option_plan_unusable_contract_returns_none():
    plan = _bull_plan()
    assert build_option_plan(underlying_plan=plan, spot=99.0, contract=_call_contract(mid=0)) is None
    assert build_option_plan(underlying_plan=plan, spot=99.0, contract=_call_contract(iv=None, delta=None)) is None


def test_option_plan_market_anchoring():
    """Model premiums scale to the live mid: doubling the quoted mid doubles
    the (calibrated) entry premium, BSM shape unchanged."""
    plan = _bull_plan()
    cheap = build_option_plan(underlying_plan=plan, spot=99.0, contract=_call_contract(mid=4.20))
    rich = build_option_plan(underlying_plan=plan, spot=99.0, contract=_call_contract(mid=8.40))
    assert rich["entry_premium"] == pytest.approx(cheap["entry_premium"] * 2, rel=0.01)


def test_option_plan_viability_flag():
    """A tiny measured move on a short-dated, high-IV contract is theta-negative
    (viable=False); the same move on a longer expiry with a shorter hold clears."""
    # Underlying plan with a barely-there target: entry 100 -> target 101.
    plan = build_trade_plan(
        pattern="Bull Pennant", key_levels={}, current_price=100.0,
        atr=0.33, bullish=True, risk="moderate",
    )
    plan["target"] = 101.0  # force a small move (engine 2R default would be 1.0 anyway)

    short_dated = _call_contract(dte=7, iv=0.80, mid=4.0)
    bad = build_option_plan(underlying_plan=plan, spot=100.0, contract=short_dated, hold_days=5.0)
    assert bad is not None and bad["viable"] is False
    assert bad["reward_per_contract"] <= 0

    long_dated = _call_contract(dte=60, iv=0.80, mid=10.0)
    good = build_option_plan(underlying_plan=plan, spot=100.0, contract=long_dated, hold_days=2.0)
    assert good is not None and good["viable"] is True
    assert good["target_premium"] > good["entry_premium"]


def test_option_plan_healthy_play_is_viable():
    plan = _bull_plan()  # entry 100 -> target 118: a real move
    opt = build_option_plan(underlying_plan=plan, spot=99.0, contract=_call_contract())
    assert opt["viable"] is True


# ─── Signal actionability ────────────────────────────────────────────────────

_GEOM = dict(bullish=True, entry=100.0, stop=95.0, target=115.0, atr=2.0)


def test_classify_target_reached_is_stale():
    status, reason = classify_signal(**_GEOM, spot=116.0, age_bars=2)
    assert status == "stale" and "played out" in reason


def test_classify_stop_breached_is_stale():
    # The META/AVGO case: price collapsed far below the bullish setup's stop.
    status, reason = classify_signal(**_GEOM, spot=88.0, age_bars=2)
    assert status == "stale" and "invalidated" in reason


def test_classify_bearish_mirror():
    bear = dict(bullish=False, entry=100.0, stop=105.0, target=85.0, atr=2.0)
    assert classify_signal(**bear, spot=84.0, age_bars=1)[0] == "stale"   # target hit
    assert classify_signal(**bear, spot=106.0, age_bars=1)[0] == "stale"  # invalidated
    assert classify_signal(**bear, spot=99.0, age_bars=1)[0] == "live"    # triggered, in progress


def test_classify_triggered_in_progress_is_live():
    status, reason = classify_signal(**_GEOM, spot=105.0, age_bars=3)
    assert status == "live" and "33%" in reason  # 5 of 15 points to target


def test_classify_far_untriggered_is_watch():
    # Above the stop (still valid) but outside the 2-ATR striking distance
    # of the entry: a setup to watch, not to price premiums on.
    status, reason = classify_signal(**_GEOM, spot=95.5, age_bars=2)
    assert status == "watch"
    assert "not actionable" in reason


def test_classify_old_untriggered_is_stale():
    status, _ = classify_signal(**_GEOM, spot=99.0, age_bars=30)
    assert status == "stale"


def test_classify_near_trigger_is_live():
    status, _ = classify_signal(**_GEOM, spot=99.0, age_bars=2)
    assert status == "live"
