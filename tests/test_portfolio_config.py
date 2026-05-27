"""Invariant tests for src/config/portfolio_config.py.

The portfolio config calls ``validate_portfolio()`` at import time, so a
bad edit blows up loudly. These tests pin that behavior plus a few
helpers.
"""
from __future__ import annotations

import pytest

from src.config.portfolio_config import (
    CASH_RESERVE_PCT,
    PORTFOLIO_SLEEVES,
    PortfolioConfigError,
    Sleeve,
    sleeve_for_ticker,
    validate_portfolio,
)


def test_default_config_is_valid() -> None:
    validate_portfolio()


def test_sleeve_allocations_sum_to_100() -> None:
    """Sleeve allocations cover the full notional book. CASH_RESERVE_PCT is a
    runtime floor, not an additive bucket."""
    total = sum(s["allocation_pct"] for s in PORTFOLIO_SLEEVES.values())
    assert total == pytest.approx(100.0)


def test_cash_reserve_pct_is_in_range() -> None:
    assert 0 <= CASH_RESERVE_PCT <= 100


def test_each_sleeve_weights_sum_to_one() -> None:
    for name, sleeve in PORTFOLIO_SLEEVES.items():
        total = sum(sleeve["agent_weights"].values())
        assert total == pytest.approx(1.0), f"sleeve {name} weights sum = {total}"


def test_agent_keys_match_registry() -> None:
    """Every agent referenced by a sleeve must exist in ANALYST_CONFIG."""
    from src.utils.analysts import ANALYST_CONFIG

    valid = set(ANALYST_CONFIG.keys())
    for name, sleeve in PORTFOLIO_SLEEVES.items():
        for agent in sleeve["agents"]:
            assert agent in valid, f"sleeve '{name}': agent '{agent}' not in ANALYST_CONFIG"


def test_validate_catches_allocation_mismatch() -> None:
    bad: dict[str, Sleeve] = {
        "a": {"allocation_pct": 80.0, "agents": ["x"], "agent_weights": {"x": 1.0}, "tickers": []},
        "b": {"allocation_pct": 5.0, "agents": ["x"], "agent_weights": {"x": 1.0}, "tickers": []},
    }
    # 80 + 5 = 85, not 100.
    with pytest.raises(PortfolioConfigError, match="must sum to 100"):
        validate_portfolio(bad, cash_reserve_pct=10.0)


def test_validate_catches_bad_cash_reserve() -> None:
    ok: dict[str, Sleeve] = {
        "a": {"allocation_pct": 100.0, "agents": ["x"], "agent_weights": {"x": 1.0}, "tickers": []},
    }
    with pytest.raises(PortfolioConfigError, match="CASH_RESERVE_PCT"):
        validate_portfolio(ok, cash_reserve_pct=150.0)


def test_validate_catches_weight_mismatch() -> None:
    # allocations valid (100%), weights wrong (sum to 1.1).
    bad: dict[str, Sleeve] = {
        "only": {"allocation_pct": 100.0, "agents": ["x", "y"], "agent_weights": {"x": 0.6, "y": 0.5}, "tickers": []},
    }
    with pytest.raises(PortfolioConfigError, match="weights must sum to 1.0"):
        validate_portfolio(bad, cash_reserve_pct=10.0)


def test_validate_catches_agent_weight_key_skew() -> None:
    # allocations valid (100%), but the weight key doesn't match the agent list.
    bad: dict[str, Sleeve] = {
        "only": {"allocation_pct": 100.0, "agents": ["x"], "agent_weights": {"y": 1.0}, "tickers": []},
    }
    with pytest.raises(PortfolioConfigError, match="does not match agent_weights"):
        validate_portfolio(bad, cash_reserve_pct=10.0)


def test_sleeve_for_ticker_finds_known_name() -> None:
    assert sleeve_for_ticker("NVDA") == "mega_tech"
    assert sleeve_for_ticker("nvda") == "mega_tech"  # case-insensitive
    assert sleeve_for_ticker("FSLR") == "energy_transition"
    assert sleeve_for_ticker("IONQ") == "emerging_tech"


def test_sleeve_for_ticker_returns_none_for_unknown() -> None:
    assert sleeve_for_ticker("ZZZZZ") is None
