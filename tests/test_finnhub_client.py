"""Tests for the Finnhub client's graceful-degradation contract.

The whole point of the Finnhub integration is that it's *additive*: with no key
configured, callers must transparently fall back to existing behavior. These
tests pin that contract without hitting the network.
"""

from __future__ import annotations

import pytest

from src.tools.finnhub import (
    FinnhubClient,
    FinnhubError,
    get_finnhub_client,
    is_finnhub_configured,
)


def test_not_configured_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    assert is_finnhub_configured() is False
    assert get_finnhub_client() is None


def test_configured_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINNHUB_API_KEY", "test-key-123")
    assert is_finnhub_configured() is True
    client = get_finnhub_client()
    assert isinstance(client, FinnhubClient)
    # Constructed per call (no process-wide singleton) so each request uses its
    # own resolved key — a fresh instance every time.
    assert get_finnhub_client() is not client


def test_client_constructor_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    with pytest.raises(FinnhubError):
        FinnhubClient(api_key="")


def test_bound_empty_context_blocks_shared_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even with the owner's env key set, a request whose context binds an empty
    Finnhub key (a non-approved user) must get NO Finnhub client — no leak."""
    from src.tools import key_context

    monkeypatch.setenv("FINNHUB_API_KEY", "owner-shared-key")
    tokens = key_context.set_provider_keys(massive=None, finnhub=None, financial_datasets=None)
    try:
        assert is_finnhub_configured() is False
        assert get_finnhub_client() is None
    finally:
        key_context.reset_provider_keys(tokens)


def test_token_bucket_bursts_then_throttles() -> None:
    """Capacity-sized burst is allowed, then further calls fail fast (no wait)."""
    from src.tools.finnhub.client import _TokenBucket

    # Effectively no refill during the test so we measure pure burst capacity.
    bucket = _TokenBucket(rate=1e-6, capacity=3)
    assert bucket.acquire(max_wait=0.0) is True
    assert bucket.acquire(max_wait=0.0) is True
    assert bucket.acquire(max_wait=0.0) is True
    assert bucket.acquire(max_wait=0.0) is False  # budget exhausted → fail fast


def test_token_bucket_refills_over_time() -> None:
    """Tokens replenish at the configured rate."""
    import time as _time

    from src.tools.finnhub.client import _TokenBucket

    bucket = _TokenBucket(rate=100.0, capacity=2)  # 100 tokens/sec
    assert bucket.acquire(max_wait=0.0) is True
    assert bucket.acquire(max_wait=0.0) is True
    assert bucket.acquire(max_wait=0.0) is False  # drained
    _time.sleep(0.05)  # ~5 tokens refill
    assert bucket.acquire(max_wait=0.0) is True


def test_client_exposes_expected_endpoints() -> None:
    client = FinnhubClient(api_key="x")
    for method in (
        "basic_financials",
        "insider_transactions",
        "recommendation_trends",
        "earnings_surprises",
        "earnings_calendar",
        "peers",
        "company_profile",
        "company_news",
        "market_news",
        "quote",
    ):
        assert callable(getattr(client, method))
