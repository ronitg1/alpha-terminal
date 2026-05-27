"""Unit tests for the aggregation logic in src/run_morning_scan.py.

The agent-running path needs the LLM and live data, so those tests live
in the live verification flow. Here we test the pure functions: signal
combination, highlight assignment, CSV serialization.
"""
from __future__ import annotations

import csv
from pathlib import Path

from src.run_morning_scan import (
    AgentVerdict,
    aggregate_verdicts,
    render_summary,
    render_terminal_table,
    write_csv,
)


def _verdict(agent: str, signal: str, conf: float, raw: dict | None = None) -> AgentVerdict:
    return AgentVerdict(agent_key=agent, signal=signal, confidence=conf, raw=raw or {})


def test_aggregate_all_bullish_high_conf_is_green() -> None:
    verdicts = {
        "alpha_seeker": _verdict("alpha_seeker", "bullish", 80, {"variant_perception": "Consensus is wrong because X.", "has_edge": True}),
        "michael_burry": _verdict("michael_burry", "bullish", 75),
    }
    weights = {"alpha_seeker": 0.6, "michael_burry": 0.4}
    row = aggregate_verdicts("opportunistic", "NVDA", verdicts, weights)
    assert row.consensus == "bullish"
    assert row.highlight == "green"
    assert row.has_variant_perception is True


def test_aggregate_mixed_signals_is_yellow() -> None:
    verdicts = {
        "alpha_seeker": _verdict("alpha_seeker", "bullish", 70),
        "michael_burry": _verdict("michael_burry", "bearish", 80),
    }
    weights = {"alpha_seeker": 0.5, "michael_burry": 0.5}
    row = aggregate_verdicts("opportunistic", "X", verdicts, weights)
    assert row.highlight == "yellow"


def test_aggregate_all_bearish_low_conf_is_neutral_highlight() -> None:
    """All same direction but low confidence → not green/red, just neutral hl."""
    verdicts = {
        "a": _verdict("a", "bearish", 40),
        "b": _verdict("b", "bearish", 45),
    }
    weights = {"a": 0.5, "b": 0.5}
    row = aggregate_verdicts("x", "X", verdicts, weights)
    # Consensus is bearish (weighted score < -35), but no high-conv highlight.
    assert row.consensus == "bearish"
    assert row.highlight == "neutral"


def test_aggregate_missing_agent_does_not_dilute_weight() -> None:
    """If one agent's output is missing, remaining weight should rescale."""
    verdicts = {"alpha_seeker": _verdict("alpha_seeker", "bullish", 80)}
    weights = {"alpha_seeker": 0.6, "michael_burry": 0.4}
    row = aggregate_verdicts("x", "X", verdicts, weights)
    # weighted_score = (0.6 * 1 * 80) / 0.6 = 80 (not 48)
    assert row.weighted_score == 80
    assert row.avg_confidence == 80
    assert row.consensus == "bullish"


def test_aggregate_no_edge_is_not_variant_perception() -> None:
    verdicts = {
        "alpha_seeker": _verdict("alpha_seeker", "neutral", 10, {"variant_perception": "No edge — skip", "has_edge": False}),
    }
    weights = {"alpha_seeker": 1.0}
    row = aggregate_verdicts("x", "X", verdicts, weights)
    assert row.has_variant_perception is False


def test_render_summary_counts() -> None:
    verdicts_green = {"a": _verdict("a", "bullish", 80), "b": _verdict("b", "bullish", 80)}
    verdicts_red = {"a": _verdict("a", "bearish", 80), "b": _verdict("b", "bearish", 80)}
    verdicts_mixed = {"a": _verdict("a", "bullish", 80), "b": _verdict("b", "bearish", 80)}
    weights = {"a": 0.5, "b": 0.5}
    rows = [
        aggregate_verdicts("s", "G1", verdicts_green, weights),
        aggregate_verdicts("s", "G2", verdicts_green, weights),
        aggregate_verdicts("s", "R1", verdicts_red, weights),
        aggregate_verdicts("s", "M1", verdicts_mixed, weights),
    ]
    summary = render_summary(rows)
    assert "2 high-conviction longs" in summary
    assert "1 high-conviction shorts" in summary
    assert "1 mixed" in summary


def test_write_csv_round_trip(tmp_path: Path) -> None:
    verdicts = {"alpha_seeker": _verdict("alpha_seeker", "bullish", 80, {"position_type": "long_calls", "hold_period": "30_90d"})}
    weights = {"alpha_seeker": 1.0}
    row = aggregate_verdicts("opportunistic", "NVDA", verdicts, weights)
    path = write_csv([row], tmp_path, "2026-05-27")
    assert path.exists()
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["ticker"] == "NVDA"
    assert rows[0]["consensus"] == "bullish"
    assert rows[0]["position_type"] == "long_calls"


def test_render_terminal_table_does_not_crash_on_empty() -> None:
    assert render_terminal_table([]) == "(no signals)"
