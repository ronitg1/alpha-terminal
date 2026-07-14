"""Regression guard for signal_context (the /scan + alert enrichment helper).

trade_plan is a FastAPI route function whose `risk`/`timeframe` defaults are
Query() objects, so calling it in-process MUST pass real strings — otherwise
normalize_risk(Query()) throws and the whole contract enrichment silently
returns None (the bug where option contracts never showed in Telegram messages).
"""
from __future__ import annotations

import asyncio

import app.backend.routes.patterns as patterns


def test_signal_context_passes_string_risk_and_returns_option(monkeypatch):
    seen: dict = {}

    async def fake_trade_plan(ticker, pattern, risk=None, timeframe="day"):
        # Record what signal_context passed — must be a plain str, never a Query().
        seen["risk"] = risk
        seen["timeframe"] = timeframe
        return {
            "current_price": 100.0,
            "plan": {"entry": 101.0, "target": 110.0},
            "option": {"type": "call", "strike": 105.0, "expiration": "2026-08-14", "dte": 31},
        }

    monkeypatch.setattr(patterns, "trade_plan", fake_trade_plan)
    ctx = asyncio.run(patterns.signal_context("NVDA", "Ascending Triangle", "day"))

    assert seen["risk"] == "moderate"  # a str, not a FastAPI Query() default
    assert seen["timeframe"] == "day"
    assert ctx is not None
    assert ctx["entry"] == 101.0 and ctx["target"] == 110.0
    assert ctx["option"]["type"] == "call"  # contract flows through
