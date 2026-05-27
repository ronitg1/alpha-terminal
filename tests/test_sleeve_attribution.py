"""Unit tests for src/backtesting/sleeve_attribution.py."""
from __future__ import annotations

from datetime import date, timedelta

from src.backtesting.sleeve_attribution import (
    Trade,
    compute_agent_attribution,
    compute_sleeve_metrics,
    render_attribution_report,
    warn_underperforming_agents,
)


SLEEVES = {
    "energy_transition": {
        "agent_weights": {"energy_transition": 0.5, "aswath_damodaran": 0.3, "michael_burry": 0.2},
    },
    "mega_tech": {
        "agent_weights": {"alpha_seeker": 0.4, "aswath_damodaran": 0.35, "fundamentals_analyst": 0.25},
    },
}


def _t(ticker, sleeve, agent, pnl, hold_days=30, close_date=None):
    close_date = close_date or date(2026, 5, 1)
    return Trade(
        ticker=ticker,
        sleeve=sleeve,
        agent=agent,
        open_date=close_date - timedelta(days=hold_days),
        close_date=close_date,
        side="long",
        pnl=pnl,
        entry_value=10_000.0,
    )


def test_sleeve_metrics_basic() -> None:
    trades = [
        _t("FSLR", "energy_transition", "energy_transition", 500),
        _t("ENPH", "energy_transition", "energy_transition", -300),
        _t("NVDA", "mega_tech", "alpha_seeker", 1200),
        _t("MSFT", "mega_tech", "alpha_seeker", 800),
    ]
    metrics = compute_sleeve_metrics(trades)
    assert set(metrics.keys()) == {"energy_transition", "mega_tech"}
    assert metrics["energy_transition"].n_trades == 2
    assert metrics["energy_transition"].win_rate == 0.5
    assert metrics["mega_tech"].win_rate == 1.0
    assert metrics["mega_tech"].total_pnl == 2000


def test_agent_attribution_uses_specific_agent_when_named() -> None:
    """If a trade names an agent in agent_weights, full P&L attributes to it."""
    trades = [_t("FSLR", "energy_transition", "energy_transition", 1000)]
    attribution = compute_agent_attribution(trades, SLEEVES)
    assert attribution["energy_transition"].total_pnl_attributed == 1000


def test_agent_attribution_distributes_by_weight_when_agent_unknown() -> None:
    """If a trade's agent isn't in the sleeve panel, P&L splits by weights."""
    trades = [_t("FSLR", "energy_transition", "stranger", 1000)]
    attribution = compute_agent_attribution(trades, SLEEVES)
    assert attribution["energy_transition"].total_pnl_attributed == 500
    assert attribution["aswath_damodaran"].total_pnl_attributed == 300
    assert attribution["michael_burry"].total_pnl_attributed == 200


def test_warn_underperforming_agents_triggers_at_low_win_rate() -> None:
    today = date(2026, 5, 27)
    # 6 trades for "weakAgent": 2 wins, 4 losses → 33% win rate.
    trades = [
        _t("X", "mega_tech", "weakAgent", -100, close_date=today - timedelta(days=i))
        for i in range(4)
    ] + [
        _t("X", "mega_tech", "weakAgent", 100, close_date=today - timedelta(days=i + 10))
        for i in range(2)
    ]
    warnings = warn_underperforming_agents(trades, as_of=today, window_days=90, min_trades=5)
    assert len(warnings) == 1
    assert warnings[0].agent == "weakAgent"
    assert "underperforming" in warnings[0].message().lower()


def test_warn_underperforming_skips_when_min_trades_not_met() -> None:
    today = date(2026, 5, 27)
    trades = [_t("X", "mega_tech", "rookie", -100, close_date=today)]
    warnings = warn_underperforming_agents(trades, as_of=today, min_trades=5)
    assert warnings == []


def test_warn_underperforming_respects_window() -> None:
    """Trades older than window_days must be excluded."""
    today = date(2026, 5, 27)
    old_losses = [
        _t("X", "mega_tech", "oldweak", -100, close_date=today - timedelta(days=200))
        for _ in range(6)
    ]
    warnings = warn_underperforming_agents(old_losses, as_of=today, window_days=90, min_trades=5)
    assert warnings == []


def test_render_attribution_report_runs() -> None:
    """Smoke test: the renderer produces a non-empty string."""
    trades = [
        _t("FSLR", "energy_transition", "energy_transition", 500),
        _t("NVDA", "mega_tech", "alpha_seeker", 1200),
    ]
    sm = compute_sleeve_metrics(trades)
    aa = compute_agent_attribution(trades, SLEEVES)
    out = render_attribution_report(sm, aa, [])
    assert "Per-sleeve" in out
    assert "Agent attribution" in out
    assert "FSLR" not in out  # report is sleeve-level, not ticker-level
    assert "energy_transition" in out
