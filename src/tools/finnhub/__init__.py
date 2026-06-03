"""Finnhub integration — a free-tier backup data source for the gaps in Massive.

Massive (Polygon) doesn't publish bulk insider (Form 4) data and its /ratios
endpoint omits growth/turnover/DSO fields. Finnhub's free tier covers both,
plus analyst recommendation trends, earnings beat/miss history, and company
news. This package is *additive*: every consumer falls back to its existing
behavior when ``FINNHUB_API_KEY`` is unset, so the app runs unchanged without it.
"""

from __future__ import annotations

from src.tools.finnhub.client import (
    FinnhubClient,
    FinnhubError,
    get_finnhub_client,
    is_finnhub_configured,
)

__all__ = [
    "FinnhubClient",
    "FinnhubError",
    "get_finnhub_client",
    "is_finnhub_configured",
]
