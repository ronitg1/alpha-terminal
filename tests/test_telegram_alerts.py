"""Telegram high-confidence alert logic — threshold filtering, dedup, gating.

The network send is stubbed; these pin the decision logic that governs whether a
scan's signals turn into a push (the part that matters for correctness/spam).
"""
from __future__ import annotations

import asyncio
import datetime

import pytest

from app.backend.services import telegram_alerts, telegram_notify

_TODAY = datetime.date.today().isoformat()


@pytest.fixture
def file_alerts(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_BACKEND", "file")
    monkeypatch.setattr(telegram_alerts, "_SETTINGS_PATH", tmp_path / "alert_settings.json")
    monkeypatch.setattr(telegram_alerts, "_DEDUP_PATH", tmp_path / "notified_signals.json")
    monkeypatch.setattr(telegram_alerts, "_get_token", lambda uid: "TOKEN")

    # Enrichment + account lookup hit the network / portfolio; stub them off so
    # these tests stay focused on the gating/dedup/format logic.
    async def _no_ctx(ticker, pattern, timeframe="day"):
        return None

    async def _acct():
        return 25000.0

    monkeypatch.setattr("app.backend.routes.patterns.signal_context", _no_ctx)
    monkeypatch.setattr(telegram_alerts, "_account_value", _acct)

    sent: list[dict] = []

    async def _fake_send(token, chat_id, text, **kw):
        sent.append({"chat_id": chat_id, "text": text})
        return True

    monkeypatch.setattr(telegram_notify, "send_message", _fake_send)
    return sent


def _res(ticker, conf, *, bullish=True, pattern="Bull Flag", end=None):
    return {"ticker": ticker, "pattern": pattern, "confidence": conf,
            "bullish": bullish, "end_date": end or _TODAY}


def test_alerts_fire_above_threshold_batched_and_dedup(file_alerts):
    telegram_alerts._save_settings(
        "u1", {"chat_id": "123", "enabled": True, "min_confidence": 90, "timeframes": ["day", "1h"]}
    )
    results = [_res("NVDA", 93), _res("AAPL", 85), _res("TSLA", 91, bullish=False)]
    n = asyncio.run(telegram_alerts.maybe_notify("u1", "day", results))
    assert n == 2  # NVDA(93) + TSLA(91) clear 90; AAPL(85) excluded
    assert len(file_alerts) == 1  # ONE batched message
    text = file_alerts[0]["text"]
    assert "NVDA" in text and "TSLA" in text and "AAPL" not in text
    # Re-run with identical results: all already notified -> nothing sent.
    assert asyncio.run(telegram_alerts.maybe_notify("u1", "day", results)) == 0
    assert len(file_alerts) == 1


def test_new_signal_next_day_still_fires(file_alerts):
    telegram_alerts._save_settings(
        "u1b", {"chat_id": "1", "enabled": True, "min_confidence": 90, "timeframes": ["day"]}
    )
    today = datetime.date.today()
    yday = (today - datetime.timedelta(days=1)).isoformat()
    assert asyncio.run(telegram_alerts.maybe_notify("u1b", "day", [_res("NVDA", 95, end=yday)])) == 1
    # Same ticker/pattern but a NEW breakout date is a distinct signal.
    assert asyncio.run(telegram_alerts.maybe_notify("u1b", "day", [_res("NVDA", 95, end=today.isoformat())])) == 1


def test_alerts_gate_on_timeframe_and_enabled(file_alerts):
    telegram_alerts._save_settings(
        "u2", {"chat_id": "1", "enabled": True, "min_confidence": 90, "timeframes": ["1h"]}
    )
    assert asyncio.run(telegram_alerts.maybe_notify("u2", "day", [_res("NVDA", 99)])) == 0  # day not enabled
    telegram_alerts._save_settings(
        "u3", {"chat_id": "1", "enabled": False, "min_confidence": 90, "timeframes": ["day"]}
    )
    assert asyncio.run(telegram_alerts.maybe_notify("u3", "day", [_res("NVDA", 99)])) == 0  # disabled


def test_alerts_skip_without_chat_or_token(file_alerts, monkeypatch):
    telegram_alerts._save_settings(
        "u4", {"chat_id": None, "enabled": True, "min_confidence": 90, "timeframes": ["day"]}
    )
    assert asyncio.run(telegram_alerts.maybe_notify("u4", "day", [_res("NVDA", 99)])) == 0  # not paired
    telegram_alerts._save_settings(
        "u5", {"chat_id": "1", "enabled": True, "min_confidence": 90, "timeframes": ["day"]}
    )
    monkeypatch.setattr(telegram_alerts, "_get_token", lambda uid: None)
    assert asyncio.run(telegram_alerts.maybe_notify("u5", "day", [_res("NVDA", 99)])) == 0  # no token


def test_clean_timeframes():
    assert telegram_alerts._clean_timeframes(["day", "1h", "bogus"]) == ["1h", "day"]
    assert telegram_alerts._clean_timeframes([]) == ["day", "1h"]
    assert telegram_alerts._clean_timeframes("nonsense") == ["day", "1h"]


def test_save_settings_clamps_confidence(file_alerts, monkeypatch):
    monkeypatch.setattr(telegram_alerts, "current_user_id", lambda: "u7")
    out = telegram_alerts.save_settings(enabled=True, min_confidence=150, timeframes=["day"])
    assert out["min_confidence"] == 100.0
    assert out["has_token"] is True  # from the stubbed _get_token


def test_format_message_shape():
    msg = telegram_alerts._format_message(
        [(_res("NVDA", 93), None), (_res("TSLA", 91, bullish=False), None)], "1h"
    )
    assert "high-confidence 1h" in msg and "this week" in msg
    assert "NVDA" in msg and "93%" in msg and "TSLA" in msg


def test_many_hits_capped_to_stay_under_telegram_limit(file_alerts):
    """A scan with hundreds of qualifying hits must NOT build one giant message
    (Telegram rejects >4096 chars). Cap to the top N, note the rest, mark all
    notified so the overflow isn't re-tried forever."""
    telegram_alerts._save_settings(
        "big", {"chat_id": "9", "enabled": True, "min_confidence": 70, "timeframes": ["day"]}
    )
    results = [_res(f"TK{i:03d}", 70 + (i % 30)) for i in range(300)]
    n = asyncio.run(telegram_alerts.maybe_notify("big", "day", results))
    assert n == telegram_alerts._MAX_ALERT_SIGNALS  # only the top N are shown
    text = file_alerts[0]["text"]
    assert len(text) <= 4096  # under Telegram's hard limit
    assert f"and {300 - telegram_alerts._MAX_ALERT_SIGNALS} more this week" in text
    # Everything fresh was marked notified, so a re-run sends nothing (no re-fail loop).
    assert asyncio.run(telegram_alerts.maybe_notify("big", "day", results)) == 0


def test_week_filter_drops_old_signals(file_alerts):
    """Only this week's breakouts alert; a months-old pattern is dropped."""
    telegram_alerts._save_settings(
        "wk", {"chat_id": "1", "enabled": True, "min_confidence": 70, "timeframes": ["day"]}
    )
    old = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    results = [_res("OLD", 95, end=old), _res("NEW", 95)]
    n = asyncio.run(telegram_alerts.maybe_notify("wk", "day", results))
    assert n == 1
    text = file_alerts[0]["text"]
    assert "NEW" in text and "OLD" not in text


def test_alert_includes_entry_target_contract_and_sizing(file_alerts, monkeypatch):
    """When enrichment is available, the alert carries entry/target + the option
    contract with R/R and the Pattern-Scanner-style position size."""
    telegram_alerts._save_settings(
        "rich", {"chat_id": "1", "enabled": True, "min_confidence": 70, "timeframes": ["day"]}
    )

    async def _ctx(ticker, pattern, timeframe="day"):
        return {
            "current_price": 92.10, "entry": 94.29, "target": 108.58,
            "option": {"type": "call", "strike": 95.0, "expiration": "2026-08-15",
                       "dte": 27, "current_mid": 1.20, "risk_reward": 2.1,
                       "risk_per_contract": 120.0, "entry_premium": 1.20,
                       "max_loss_per_contract": 120.0},
        }

    monkeypatch.setattr("app.backend.routes.patterns.signal_context", _ctx)
    n = asyncio.run(telegram_alerts.maybe_notify("rich", "day", [_res("NVDA", 91)]))
    assert n == 1
    text = file_alerts[0]["text"]
    assert "entry $94.29" in text and "target $108.58" in text
    assert "CALL $95" in text and "exp 2026-08-15" in text and "R/R 2.1" in text
    # $25k account, 1% risk = $250; $250 // $120 risk/contract = 2 contracts.
    assert "size 2 ct" in text
