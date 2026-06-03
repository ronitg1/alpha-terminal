"""Tests for macro-news categorization and earnings-noise stripping.

Pins the first-match-wins priority order and the default 'markets' bucket so a
regex tweak can't silently re-bucket the macro feed.
"""

from __future__ import annotations

import pytest

from app.backend.services.finnhub_news import categorize_macro, is_earnings_noise


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Fed holds rates steady as CPI cools", "monetary"),
        ("Powell signals a possible rate cut", "monetary"),
        ("China and Taiwan tensions escalate", "geopolitics"),
        ("White House proposes new tariff package", "government"),
        ("US GDP grows faster than expected, jobs report strong", "economy"),
        ("OPEC+ weighs crude output cut as Brent slips", "energy"),
        ("S&P 500 drifts higher in quiet trading", "markets"),
    ],
)
def test_categorize_macro(text: str, expected: str) -> None:
    assert categorize_macro(text) == expected


def test_priority_first_match_wins() -> None:
    # Monetary pattern precedes geopolitics, so a Fed+China headline → monetary.
    assert categorize_macro("Fed weighs rates amid China tariff fight") == "monetary"


def test_earnings_noise_detection() -> None:
    assert is_earnings_noise("Apple reports record Q3 earnings, beats estimates") is True
    assert is_earnings_noise("Company issues quarterly guidance") is True
    assert is_earnings_noise("Fed holds rates steady") is False
