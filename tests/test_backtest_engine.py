"""Tests for the ported Vibe-Trading backtest engine (src/backtesting/vibe_engine).

All data here is synthetic pandas — no network, no Massive key needed.
Covers: metrics math, statistical validation keys, look-ahead safety of the
execution loop (signals fill at the NEXT bar's open), the pattern-signal
pulse semantics, the Massive payload converter, and an end-to-end two-symbol
run through GlobalEquityEngine.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.backtesting.vibe_engine.equity import GlobalEquityEngine
from src.backtesting.vibe_engine.loaders import MassiveLoader, _frame_from_aggregates
from src.backtesting.vibe_engine.metrics import calc_metrics
from src.backtesting.vibe_engine.models import TradeRecord
from src.backtesting.vibe_engine.signals import PatternSignalEngine
from src.backtesting.vibe_engine.validation import (
    bootstrap_sharpe_ci,
    monte_carlo_test,
    walk_forward_analysis,
)
from src.backtesting.pattern_backtest import _validate_dates, _validate_tickers


# ─── Synthetic data helpers ────────────────────────────────────────────────────

def _make_ohlcv(n: int = 60, start: str = "2024-01-02", base: float = 100.0) -> pd.DataFrame:
    """Deterministic OHLCV frame with distinct open/close per bar."""
    dates = pd.bdate_range(start, periods=n)
    close = base + 0.5 * np.arange(n)
    open_ = close - 0.2
    return pd.DataFrame(
        {
            "open": open_,
            "high": close + 1.0,
            "low": open_ - 1.0,
            "close": close,
            "volume": np.full(n, 1_000_000.0),
        },
        index=dates,
    )


def _make_trades(equity_index: pd.DatetimeIndex, n: int = 30) -> list[TradeRecord]:
    """Alternating win/loss trades spread across the equity curve's dates."""
    trades = []
    step = max(1, len(equity_index) // (n + 2))
    for i in range(n):
        pos = min(i * step, len(equity_index) - 2)
        pnl = 250.0 if i % 3 != 0 else -180.0
        trades.append(
            TradeRecord(
                symbol="SYN",
                direction=1,
                entry_price=100.0,
                exit_price=100.0 + pnl / 100.0,
                entry_time=equity_index[pos],
                exit_time=equity_index[pos + 1],
                size=100.0,
                leverage=1.0,
                pnl=pnl,
                pnl_pct=pnl / 10_000.0 * 100,
                exit_reason="signal",
                holding_bars=1,
                commission=0.0,
            )
        )
    return trades


class SpikeSignalEngine:
    """Emit +1.0 on exactly one bar (by position) per symbol, else 0."""

    def __init__(self, spike_pos: int):
        self.spike_pos = spike_pos

    def generate(self, data_map: dict[str, pd.DataFrame]) -> dict[str, pd.Series]:
        out = {}
        for sym, df in data_map.items():
            sig = pd.Series(0.0, index=df.index)
            sig.iloc[self.spike_pos] = 1.0
            out[sym] = sig
        return out


class FakeLoader:
    """Loader-contract stub returning pre-built frames."""

    name = "fake"

    def __init__(self, frames: dict[str, pd.DataFrame]):
        self.frames = frames

    def fetch(self, codes, start_date, end_date, *, interval="1D", fields=None):
        return {c: self.frames[c] for c in codes if c in self.frames}


def _engine_config(**overrides) -> dict:
    cfg = {
        "codes": ["AAA"],
        "start_date": "2024-01-02",
        "end_date": "2024-12-31",
        "interval": "1D",
        "initial_cash": 100_000.0,
        "slippage_us": 0.0,  # exact-price assertions
    }
    cfg.update(overrides)
    return cfg


# ─── (a) metrics ───────────────────────────────────────────────────────────────

def test_calc_metrics_on_handmade_curve():
    dates = pd.bdate_range("2024-01-02", periods=6)
    equity = pd.Series([100_000, 105_000, 110_000, 99_000, 108_000, 120_000], index=dates, dtype=float)
    m = calc_metrics(equity, trades=[], initial_cash=100_000.0, bars_per_year=252)

    assert m["final_value"] == pytest.approx(120_000.0)
    assert m["total_return"] == pytest.approx(0.2)
    # Peak 110k -> trough 99k = -10% drawdown
    assert m["max_drawdown"] == pytest.approx(-0.1)
    # Rising curve -> positive, finite sharpe
    assert np.isfinite(m["sharpe"]) and m["sharpe"] > 0
    assert m["trade_count"] == 0


def test_calc_metrics_empty_curve_returns_zeros():
    m = calc_metrics(pd.Series(dtype=float), trades=[], initial_cash=50_000.0)
    assert m["final_value"] == 50_000.0
    assert m["total_return"] == 0


# ─── (b) validation ────────────────────────────────────────────────────────────

def test_validation_tools_return_documented_keys():
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2023-01-02", periods=120)
    returns = rng.normal(0.001, 0.01, size=120)
    equity = pd.Series(100_000.0 * np.cumprod(1 + returns), index=dates)
    trades = _make_trades(dates)

    mc = monte_carlo_test(trades, initial_capital=100_000.0, n_simulations=200)
    for key in (
        "actual_sharpe", "p_value_sharpe", "actual_max_dd", "p_value_max_dd",
        "simulated_sharpe_mean", "simulated_sharpe_std",
        "simulated_sharpe_p5", "simulated_sharpe_p95", "n_simulations", "n_trades",
    ):
        assert key in mc, f"monte_carlo_test missing {key}"
    assert mc["n_trades"] == len(trades)
    assert 0.0 <= mc["p_value_sharpe"] <= 1.0

    bs = bootstrap_sharpe_ci(equity, n_bootstrap=200)
    for key in (
        "observed_sharpe", "ci_lower", "ci_upper", "median_sharpe",
        "prob_positive", "confidence", "n_bootstrap",
    ):
        assert key in bs, f"bootstrap_sharpe_ci missing {key}"
    assert bs["ci_lower"] <= bs["median_sharpe"] <= bs["ci_upper"]

    wf = walk_forward_analysis(equity, trades, n_windows=4)
    for key in (
        "n_windows", "windows", "profitable_windows", "consistency_rate",
        "return_mean", "return_std", "sharpe_mean", "sharpe_std",
    ):
        assert key in wf, f"walk_forward_analysis missing {key}"
    assert len(wf["windows"]) == 4
    for w in wf["windows"]:
        assert {"window", "start", "end", "return", "sharpe", "max_dd", "trades", "win_rate"} <= set(w)


def test_validation_degenerate_inputs():
    assert "error" in monte_carlo_test([], 100_000.0)
    assert "error" in bootstrap_sharpe_ci(pd.Series([100.0, 101.0]))
    tiny = pd.Series([100.0, 101.0], index=pd.bdate_range("2024-01-02", periods=2))
    assert "error" in walk_forward_analysis(tiny, [], n_windows=5)


# ─── (c) look-ahead safety ─────────────────────────────────────────────────────

@pytest.mark.parametrize("spike_pos", [10, 17])
def test_signal_executes_at_next_bar_open(tmp_path, spike_pos):
    """A signal on bar k must fill at bar k+1's open — never bar k."""
    df = _make_ohlcv(n=40)
    cfg = _engine_config()
    engine = GlobalEquityEngine(cfg, market="us")
    engine.run_backtest(
        cfg, FakeLoader({"AAA": df}), SpikeSignalEngine(spike_pos),
        tmp_path / f"run_{spike_pos}", bars_per_year=252,
    )

    assert len(engine.trades) == 1
    trade = engine.trades[0]
    expected_entry_ts = df.index[spike_pos + 1]
    assert trade.entry_time == expected_entry_ts
    assert trade.entry_price == pytest.approx(float(df["open"].iloc[spike_pos + 1]))
    # Signal reverts to 0 after the spike -> closed at the bar after entry.
    assert trade.exit_time == df.index[spike_pos + 2]
    assert trade.exit_price == pytest.approx(float(df["open"].iloc[spike_pos + 2]))


def test_shifting_signal_by_n_bars_shifts_execution_by_n(tmp_path):
    """Moving the signal N bars later moves the fill N bars later — no peeking."""
    df = _make_ohlcv(n=40)
    entries = {}
    for spike_pos in (8, 8 + 5):
        cfg = _engine_config()
        engine = GlobalEquityEngine(cfg, market="us")
        engine.run_backtest(
            cfg, FakeLoader({"AAA": df}), SpikeSignalEngine(spike_pos),
            tmp_path / f"shift_{spike_pos}", bars_per_year=252,
        )
        assert len(engine.trades) == 1
        entries[spike_pos] = engine.trades[0].entry_time

    pos_a = df.index.get_loc(entries[8])
    pos_b = df.index.get_loc(entries[13])
    assert pos_b - pos_a == 5
    assert pos_a == 9  # spike at 8 -> fill at 9


# ─── PatternSignalEngine pulse semantics ───────────────────────────────────────

def test_pattern_signal_pulse_and_overlap():
    df = _make_ohlcv(n=30)
    engine = PatternSignalEngine(patterns=["Bullish Flag"], hold=5)

    bull_end = df.index[10].strftime("%Y-%m-%d")
    bear_end = df.index[12].strftime("%Y-%m-%d")

    def fake_detector(candles, config=None):
        return [
            {"pattern": "Bullish Flag", "end_date": bull_end, "confidence": 80.0},
            {"pattern": "Double Top", "end_date": bear_end, "confidence": 90.0},
        ]

    engine.patterns = {"Bullish Flag": fake_detector}
    sig = engine.generate({"AAA": df})["AAA"]

    assert sig.iloc[9] == 0.0                          # nothing before the pulse
    assert sig.iloc[10] == pytest.approx(0.8)          # bullish pulse start
    assert sig.iloc[11] == pytest.approx(0.8)
    # Overlap at bars 12..14: bearish -0.9 has larger magnitude -> wins
    assert sig.iloc[12] == pytest.approx(-0.9)
    assert sig.iloc[14] == pytest.approx(-0.9)
    assert sig.iloc[16] == pytest.approx(-0.9)         # bearish hold runs to bar 16
    assert sig.iloc[17] == 0.0                         # pulse over
    assert sig.abs().max() <= 1.0


def test_pattern_signal_engine_rejects_unknown_pattern():
    with pytest.raises(ValueError, match="Unknown pattern"):
        PatternSignalEngine(patterns=["Not A Pattern"])


def test_pattern_signal_engine_runs_real_detectors_on_synthetic_data():
    """Real detectors on featureless synthetic data: no crash, bounded output."""
    engine = PatternSignalEngine(hold=10)
    sig_map = engine.generate({"AAA": _make_ohlcv(n=80)})
    assert set(sig_map) == {"AAA"}
    sig = sig_map["AAA"]
    assert isinstance(sig, pd.Series)
    assert len(sig) == 80
    assert float(sig.abs().max()) <= 1.0


# ─── MassiveLoader payload conversion (no network) ─────────────────────────────

def test_frame_from_aggregates_maps_polygon_payload():
    day_ms = 86_400_000
    t0 = int(pd.Timestamp("2024-03-01 05:00").value // 1_000_000)  # midnight-ET-ish epoch
    payload = {
        "results": [
            {"t": t0 + i * day_ms, "o": 10.0 + i, "h": 11.0 + i, "l": 9.0 + i, "c": 10.5 + i, "v": 1000 + i}
            for i in range(3)
        ]
    }
    df = _frame_from_aggregates(payload)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 3
    assert df.index[0] == pd.Timestamp("2024-03-01")  # normalized to session date
    assert df["close"].iloc[1] == pytest.approx(11.5)
    assert df.index.is_monotonic_increasing
    assert all(df[c].dtype == float for c in df.columns)


def test_frame_from_aggregates_empty_payload():
    assert _frame_from_aggregates({}) is None
    assert _frame_from_aggregates({"results": []}) is None


def test_massive_loader_rejects_intraday():
    loader = MassiveLoader(client=object())  # never touched before the interval check
    with pytest.raises(NotImplementedError):
        loader.fetch(["NVDA"], "2024-01-01", "2024-06-01", interval="1h")


# ─── (d) end-to-end run ────────────────────────────────────────────────────────

def test_end_to_end_two_symbols(tmp_path):
    frames = {
        "AAA": _make_ohlcv(n=50, base=100.0),
        "BBB": _make_ohlcv(n=50, base=50.0),
    }

    class AlternatingSignals:
        """Long AAA for 10 bars mid-history; short BBB for 5 bars."""

        def generate(self, data_map):
            out = {}
            for sym, df in data_map.items():
                sig = pd.Series(0.0, index=df.index)
                if sym == "AAA":
                    sig.iloc[10:20] = 0.7
                else:
                    sig.iloc[25:30] = -0.5
                out[sym] = sig
            return out

    cfg = _engine_config(
        codes=["AAA", "BBB"],
        validation={"monte_carlo": {"n_simulations": 100}, "bootstrap": {"n_bootstrap": 100}, "walk_forward": {"n_windows": 3}},
    )
    engine = GlobalEquityEngine(cfg, market="us")
    run_dir = tmp_path / "e2e"
    metrics = engine.run_backtest(cfg, FakeLoader(frames), AlternatingSignals(), run_dir, bars_per_year=252)

    for key in ("final_value", "total_return", "sharpe", "max_drawdown", "win_rate", "trade_count"):
        assert key in metrics
    assert metrics["trade_count"] == len(engine.trades) >= 2
    assert {t.symbol for t in engine.trades} == {"AAA", "BBB"}
    assert "validation" in metrics and set(metrics["validation"]) <= {"monte_carlo", "bootstrap", "walk_forward"}

    # Equity curve covers every unified bar and starts near initial capital
    assert len(engine.equity_snapshots) == 50
    assert engine.equity_snapshots[0].equity == pytest.approx(100_000.0, rel=1e-6)

    # Artifacts + run card written
    assert (run_dir / "artifacts" / "equity.csv").exists()
    assert (run_dir / "artifacts" / "trades.csv").exists()
    assert (run_dir / "run_card.json").exists()


# ─── Service input bounds ──────────────────────────────────────────────────────

def test_service_input_validation():
    assert _validate_tickers(["nvda", "NVDA", " amd "]) == ["NVDA", "AMD"]
    with pytest.raises(ValueError):
        _validate_tickers([])
    with pytest.raises(ValueError):
        _validate_tickers(["BAD TICKER!"])
    with pytest.raises(ValueError):
        _validate_tickers([f"T{i}" for i in range(60)])
    assert _validate_dates("2024-01-01", "2024-06-30") == ("2024-01-01", "2024-06-30")
    with pytest.raises(ValueError):
        _validate_dates("2024-06-30", "2024-01-01")
    with pytest.raises(ValueError):
        _validate_dates("not-a-date", "2024-01-01")


def test_run_pattern_backtest_rejects_intraday_timeframe():
    from src.backtesting.pattern_backtest import run_pattern_backtest

    with pytest.raises(NotImplementedError):
        run_pattern_backtest(["NVDA"], "2024-01-01", "2024-06-30", timeframe="1h")
