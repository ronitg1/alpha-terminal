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


def test_sleeve_allocations_within_bounds() -> None:
    """Sleeve allocations are informational (real allocation is the per-ticker
    overlay), so they're optional — each must be in [0, 100] and the total
    must not over-allocate the book beyond 100%. (0 is valid: a user may
    create portfolios without setting allocation weights.)"""
    total = sum(s["allocation_pct"] for s in PORTFOLIO_SLEEVES.values())
    assert 0 <= total <= 100.0 + 1e-6
    for s in PORTFOLIO_SLEEVES.values():
        assert 0 <= s["allocation_pct"] <= 100


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


def test_validate_allows_under_allocation() -> None:
    """Allocations summing to < 100% are valid now (remainder is implicit cash);
    this is what lets you add/delete sleeves freely from the dashboard."""
    under: dict[str, Sleeve] = {
        "a": {"allocation_pct": 50.0, "agents": ["x"], "agent_weights": {"x": 1.0}, "tickers": []},
        "b": {"allocation_pct": 20.0, "agents": ["x"], "agent_weights": {"x": 1.0}, "tickers": []},
    }
    # 50 + 20 = 70 < 100 — must NOT raise.
    validate_portfolio(under, cash_reserve_pct=10.0)


def test_validate_catches_over_allocation() -> None:
    """Over-allocating the book beyond 100% is still rejected."""
    over: dict[str, Sleeve] = {
        "a": {"allocation_pct": 80.0, "agents": ["x"], "agent_weights": {"x": 1.0}, "tickers": []},
        "b": {"allocation_pct": 40.0, "agents": ["x"], "agent_weights": {"x": 1.0}, "tickers": []},
    }
    # 80 + 40 = 120 > 100.
    with pytest.raises(PortfolioConfigError, match="cannot exceed 100"):
        validate_portfolio(over, cash_reserve_pct=10.0)


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
    """Config-agnostic: pick a real (sleeve, ticker) from whatever the live
    config holds and assert the reverse lookup + case-insensitivity. Avoids
    hard-coding sleeve names, which are user-editable (portfolio_config.py is
    both the shipped default and the live user store)."""
    sample = next(
        (
            (name, sleeve["tickers"][0])
            for name, sleeve in PORTFOLIO_SLEEVES.items()
            if sleeve.get("tickers")
        ),
        None,
    )
    assert sample is not None, "no configured portfolio has any tickers"
    name, ticker = sample
    assert sleeve_for_ticker(ticker) == name
    assert sleeve_for_ticker(ticker.lower()) == name  # case-insensitive


def test_sleeve_for_ticker_returns_none_for_unknown() -> None:
    assert sleeve_for_ticker("ZZZZZ") is None
