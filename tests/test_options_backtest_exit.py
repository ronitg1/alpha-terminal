"""Unit tests for the options-backtest exit resolver.

``_resolve_option_exit`` walks a per-day option premium series and returns the
first exit trigger that fires. These tests pin the trigger priority and the
conservative stop-before-target convention so a refactor can't silently change
how trades close.
"""

from __future__ import annotations

import datetime

from app.backend.routes.sleeves import OptionsBacktestRequest, TradeRecord, _resolve_option_exit

D0 = datetime.date(2026, 1, 5)


def _apply_slippage(entry: float, exit_: float, slip: float) -> tuple[float, float]:
    """Mirror the backtest's inline slippage haircut: cross half the spread each side."""
    return entry * (1.0 + slip / 2.0), exit_ * (1.0 - slip / 2.0)


def test_request_defaults_are_percentage_and_realistic() -> None:
    r = OptionsBacktestRequest(start_date="2026-01-01", end_date="2026-02-01")
    assert r.min_conviction_pct == 40.0   # %-based gate, not the legacy 0-3 count
    assert r.slippage_pct == 0.05         # realistic transaction cost on by default
    assert r.profit_target_pct == 0.50
    assert r.stop_loss_pct == 0.50
    assert r.dte_exit == 21
    assert "conviction_pct" in TradeRecord.model_fields


def test_slippage_turns_marginal_winner_into_loser() -> None:
    # A frictionless +0.5% gross move is a "win" on raw marks...
    entry, exit_ = 1.00, 1.005
    assert exit_ - entry > 0
    # ...but crossing a 5% spread makes it a real-world loss.
    e_fill, x_fill = _apply_slippage(entry, exit_, 0.05)
    assert e_fill > entry            # bought above the mark
    assert x_fill < exit_            # sold below the mark
    assert x_fill - e_fill < 0       # net loser after costs


def test_slippage_off_is_frictionless() -> None:
    e_fill, x_fill = _apply_slippage(2.00, 3.00, 0.0)
    assert e_fill == 2.00 and x_fill == 3.00


def _series(premiums: list[float]) -> list[tuple[datetime.date, float]]:
    """Build a daily (date, premium) series starting at D0, one calendar day apart."""
    return [(D0 + datetime.timedelta(days=i), p) for i, p in enumerate(premiums)]


def test_profit_target_fires() -> None:
    # Entry 1.00 → 1.60 on day 2 is +60%, past the +50% target.
    series = _series([1.00, 1.20, 1.60, 2.00])
    prem, date, reason = _resolve_option_exit(
        series=series, entry_premium=1.00, expiry=None,
        profit_target_pct=0.50, stop_loss_pct=0.50, dte_exit=None,
    )
    assert reason == "target"
    assert prem == 1.60
    assert date == D0 + datetime.timedelta(days=2)


def test_stop_loss_fires() -> None:
    # Drops to 0.40 (−60%) on day 2, past the −50% stop.
    series = _series([1.00, 0.80, 0.40, 0.90])
    prem, date, reason = _resolve_option_exit(
        series=series, entry_premium=1.00, expiry=None,
        profit_target_pct=0.50, stop_loss_pct=0.50, dte_exit=None,
    )
    assert reason == "stop"
    assert prem == 0.40


def test_stop_checked_before_target_same_day() -> None:
    # A single mark that satisfies both the +50% target and −50% stop is
    # impossible, but a day that qualifies for the stop must win when both are
    # configured — verify by a day that is exactly at the stop threshold while a
    # later day would hit the target.
    series = _series([1.00, 0.50, 1.60])  # day1 = −50% (stop), day2 = +60% (target)
    _prem, _date, reason = _resolve_option_exit(
        series=series, entry_premium=1.00, expiry=None,
        profit_target_pct=0.50, stop_loss_pct=0.50, dte_exit=None,
    )
    assert reason == "stop"  # stop hit first chronologically


def test_dte_exit_fires() -> None:
    # No target/stop hit; expiry is 5 days after entry, dte_exit=3 → close when
    # days-to-expiry <= 3, i.e. on day 2 (3 days remain).
    series = _series([1.00, 1.05, 1.10, 1.12, 1.15])
    expiry = D0 + datetime.timedelta(days=5)
    prem, date, reason = _resolve_option_exit(
        series=series, entry_premium=1.00, expiry=expiry,
        profit_target_pct=0.50, stop_loss_pct=0.50, dte_exit=3,
    )
    assert reason == "dte"
    assert date == D0 + datetime.timedelta(days=2)


def test_expiry_settles_when_no_other_trigger() -> None:
    # dte_exit off; the series reaches the expiry date itself → 'expiry'.
    series = _series([1.00, 1.05, 1.10])
    expiry = D0 + datetime.timedelta(days=2)
    _prem, date, reason = _resolve_option_exit(
        series=series, entry_premium=1.00, expiry=expiry,
        profit_target_pct=0.50, stop_loss_pct=0.50, dte_exit=None,
    )
    assert reason == "expiry"
    assert date == expiry


def test_time_backstop_when_nothing_fires() -> None:
    # Gentle drift, no trigger met → falls through to the last point as 'time'.
    series = _series([1.00, 1.05, 1.10, 1.12])
    prem, date, reason = _resolve_option_exit(
        series=series, entry_premium=1.00, expiry=None,
        profit_target_pct=0.50, stop_loss_pct=0.50, dte_exit=None,
    )
    assert reason == "time"
    assert prem == 1.12
    assert date == series[-1][0]


def test_disabled_triggers_are_ignored() -> None:
    # With target/stop/dte all None, only the backstop applies even on a big move.
    series = _series([1.00, 5.00, 0.01])
    _prem, _date, reason = _resolve_option_exit(
        series=series, entry_premium=1.00, expiry=None,
        profit_target_pct=None, stop_loss_pct=None, dte_exit=None,
    )
    assert reason == "time"


def test_nonpositive_entry_premium_returns_time() -> None:
    series = _series([0.0, 0.0, 0.0])
    prem, date, reason = _resolve_option_exit(
        series=series, entry_premium=0.0, expiry=None,
        profit_target_pct=0.50, stop_loss_pct=0.50, dte_exit=None,
    )
    assert reason == "time"
    assert date == series[-1][0]
