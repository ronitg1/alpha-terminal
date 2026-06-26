"""Behavior tests for the pattern-scanner options backtest engine.

Pure-math coverage with hand-built candles (no network, no mocks): signal
replay, delta->strike round-trip, BSM and real-fill pricing, aggregation, and
the optimizer grid. Mirrors the fixture-driven style of test_morning_scan.
"""
from __future__ import annotations

from src.backtesting.options_proxy import bsm_delta, strike_for_delta
from src.backtesting.pattern_options import (
    PatternSignal,
    TradeConfig,
    aggregate,
    build_grid,
    option_type_for,
    price_bsm,
    price_from_series,
    replay_signals,
)


def _daily_candles(closes: list[float]) -> list[dict]:
    """Daily candles labeled 2026-01-01.. with given closes."""
    out = []
    for i, c in enumerate(closes, start=1):
        out.append({"date": f"2026-01-{i:02d}", "open": c, "high": c, "low": c, "close": c, "volume": 1000})
    return out


def test_delta_strike_round_trip():
    for ot in ("call", "put"):
        for td in (0.3, 0.4, 0.5, 0.6):
            k = strike_for_delta(spot=100, target_delta=td, time_to_expiry_years=30 / 365, sigma=0.3, option_type=ot)
            d = bsm_delta(spot=100, strike=k, time_to_expiry_years=30 / 365, sigma=0.3, option_type=ot)
            assert abs(abs(d) - td) < 1e-3
    # A lower-delta call sits further OTM (higher strike); a 0.6-delta call is
    # ITM (strike below spot). So k30 is above spot, k60 below.
    k30 = strike_for_delta(spot=100, target_delta=0.3, time_to_expiry_years=30 / 365, sigma=0.3, option_type="call")
    k60 = strike_for_delta(spot=100, target_delta=0.6, time_to_expiry_years=30 / 365, sigma=0.3, option_type="call")
    assert k30 > 100 > k60


def test_replay_maps_detection_to_fire_bar():
    candles = _daily_candles([100, 101, 102, 103, 104, 105])

    def fake_detector(cs: list[dict]) -> list[dict]:
        return [{"pattern": "Bullish Flag", "end_date": cs[3]["date"], "confidence": 80.0}]

    sigs = replay_signals(
        "AAPL", candles, {"Bullish Flag": fake_detector}, {"Bullish Flag"},
    )
    assert len(sigs) == 1
    s = sigs[0]
    assert s.fire_idx == 3 and s.entry_spot == 103 and s.bullish is True
    assert s.fire_date == "2026-01-04"


def test_replay_confidence_filter_and_unknown_label():
    candles = _daily_candles([100, 101, 102])

    def low_conf(cs):
        return [{"pattern": "P", "end_date": cs[1]["date"], "confidence": 30.0}]

    def bad_label(cs):
        return [{"pattern": "P", "end_date": "1999-01-01", "confidence": 90.0}]

    assert replay_signals("X", candles, {"P": low_conf}, set(), min_confidence=50.0) == []
    assert replay_signals("X", candles, {"P": bad_label}, set()) == []


def test_option_type_for():
    bull = PatternSignal("X", "Bullish Flag", True, 0, "d", 100.0, 80.0)
    bear = PatternSignal("X", "Double Top", False, 0, "d", 100.0, 80.0)
    assert option_type_for(bull, "auto") == "call"
    assert option_type_for(bear, "auto") == "put"
    assert option_type_for(bear, "calls") == "call"
    assert option_type_for(bull, "puts") == "put"


def test_price_bsm_call_profits_on_up_move():
    candles = _daily_candles([100, 101, 102, 103, 104, 105, 106])
    sig = PatternSignal("X", "Bullish Flag", True, 2, candles[2]["date"], 102.0, 80.0)
    cfg = TradeConfig(delta=0.5, dte=30, hold=3)
    trade = price_bsm(sig, candles, sigma=0.3, cfg=cfg, option_type="call", slippage_pct=0.0)
    assert trade is not None
    assert trade.synthetic is True
    # Spot rose 102 -> 105 over the hold, a 0.5-delta call should gain.
    assert trade.pnl > 0 and trade.return_pct > 0


def test_price_bsm_insufficient_forward_bars_returns_none():
    candles = _daily_candles([100, 101, 102])
    sig = PatternSignal("X", "P", True, 2, candles[2]["date"], 102.0, 80.0)
    # hold pushes the exit past the last candle.
    assert price_bsm(sig, candles, 0.3, TradeConfig(0.5, 30, 2), "call") is None


def test_price_from_series_uses_real_premiums():
    candles = _daily_candles([100, 101, 102, 103, 104])
    sig = PatternSignal("X", "P", True, 1, candles[1]["date"], 101.0, 80.0)
    series = {candles[1]["date"]: 5.0, candles[3]["date"]: 8.0}
    trade = price_from_series(
        sig, candles, TradeConfig(0.4, 30, 2), "call",
        series=series, strike=105.0, contract="O:X", slippage_pct=0.0,
    )
    assert trade is not None and trade.synthetic is False
    assert trade.entry_premium == 5.0 and trade.exit_premium == 8.0
    assert abs(trade.pnl - 3.0) < 1e-9 and abs(trade.return_pct - 0.6) < 1e-9


def test_price_from_series_missing_bar_returns_none():
    candles = _daily_candles([100, 101, 102, 103])
    sig = PatternSignal("X", "P", True, 1, candles[1]["date"], 101.0, 80.0)
    # Entry bar present, exit bar absent and nothing at/after it -> None (fallback).
    series = {candles[1]["date"]: 5.0}
    assert price_from_series(
        sig, candles, TradeConfig(0.4, 30, 2), "call",
        series=series, strike=105.0, contract=None,
    ) is None


def test_aggregate_stats():
    def t(pnl, ret, pattern="P", synth=False):
        from src.backtesting.pattern_options import Trade
        return Trade("X", pattern, "call", 100, "a", "b", 1.0, 1.0 + pnl, pnl, ret, 80.0, synth, None)

    trades = [t(1.0, 0.5), t(-0.5, -0.25), t(2.0, 1.0, synth=True)]
    agg = aggregate(trades)
    assert agg["n_trades"] == 3 and agg["n_wins"] == 2
    assert abs(agg["win_rate"] - 2 / 3) < 1e-9
    assert agg["n_synthetic"] == 1
    assert agg["total_pnl"] == 2.5
    assert "P" in agg["by_pattern"] and agg["by_pattern"]["P"]["n"] == 3


def test_aggregate_empty():
    agg = aggregate([])
    assert agg["n_trades"] == 0 and agg["win_rate"] == 0.0


def test_build_grid_cartesian():
    grid = build_grid([0.3, 0.4], [30, 45], [1, 2, 3])
    assert len(grid) == 2 * 2 * 3
    assert TradeConfig(0.4, 45, 3) in grid
