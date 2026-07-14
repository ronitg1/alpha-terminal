"""On-demand pre-scan (`prescan_runner.run_now_for_user`).

Pins that a manual run scans each of the user's scheduled timeframes, stores the
result, and routes through the same Telegram alert hook as the automatic runner —
returning a per-timeframe summary the UI reports back.
"""
from __future__ import annotations

import asyncio

import pytest

from app.backend.services import prescan_runner


@pytest.fixture(autouse=True)
def _no_auth(monkeypatch: pytest.MonkeyPatch):
    # File-backend / auth-off path: no per-user key binding.
    monkeypatch.setattr(prescan_runner, "auth_enabled", lambda: False)
    monkeypatch.setattr(prescan_runner, "_user_tickers", lambda: ["NVDA", "AMD"])
    yield


def test_run_now_scans_each_scheduled_timeframe_and_alerts(monkeypatch: pytest.MonkeyPatch):
    # Two enabled schedules for the user (day + 1h) and one for someone else.
    monkeypatch.setattr(
        prescan_runner.scan_schedule_service, "all_enabled_schedules",
        lambda: [
            {"user_id": "u1", "timeframe": "day", "lookback_days": 180},
            {"user_id": "u1", "timeframe": "1h", "lookback_days": 30},
            {"user_id": "other", "timeframe": "week", "lookback_days": 365},
        ],
    )
    stored: list[tuple] = []
    monkeypatch.setattr(
        prescan_runner.scan_schedule_service, "set_prescan_for",
        lambda uid, results, tf, n: stored.append((uid, tf, len(results), n)),
    )

    seen_tf: list[str] = []

    async def _fake_scan(tickers, detectors, timeframe, lookback):
        seen_tf.append(timeframe)
        return [{"ticker": "NVDA", "pattern": "Ascending Triangle", "confidence": 95, "bullish": True}]

    async def _fake_notify(user_id, timeframe, results):
        return 1  # one alert per timeframe

    monkeypatch.setattr("app.backend.routes.patterns.run_pattern_scan", _fake_scan)
    monkeypatch.setattr("app.backend.services.telegram_alerts.maybe_notify", _fake_notify)

    out = asyncio.run(prescan_runner.run_now_for_user("u1"))

    # Only u1's timeframes were scanned (not "other"'s week schedule).
    assert set(seen_tf) == {"day", "1h"}
    assert out["tickers"] == 2
    assert out["timeframes"]["day"] == {"signals": 1, "alerts_sent": 1}
    assert out["timeframes"]["1h"] == {"signals": 1, "alerts_sent": 1}
    assert {s[1] for s in stored} == {"day", "1h"}


def test_run_now_defaults_to_daily_when_no_schedules(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(prescan_runner.scan_schedule_service, "all_enabled_schedules", lambda: [])
    monkeypatch.setattr(prescan_runner.scan_schedule_service, "set_prescan_for", lambda *a: None)

    async def _fake_scan(tickers, detectors, timeframe, lookback):
        assert timeframe == "day"
        return []

    async def _fake_notify(user_id, timeframe, results):
        return 0

    monkeypatch.setattr("app.backend.routes.patterns.run_pattern_scan", _fake_scan)
    monkeypatch.setattr("app.backend.services.telegram_alerts.maybe_notify", _fake_notify)

    out = asyncio.run(prescan_runner.run_now_for_user("u1"))
    assert list(out["timeframes"].keys()) == ["day"]


def test_run_now_no_tickers(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(prescan_runner, "_user_tickers", lambda: [])
    monkeypatch.setattr(prescan_runner.scan_schedule_service, "all_enabled_schedules", lambda: [])
    out = asyncio.run(prescan_runner.run_now_for_user("u1"))
    assert out["error"] == "no_watchlist_tickers"
