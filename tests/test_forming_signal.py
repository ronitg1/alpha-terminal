"""`is_forming_signal` — flags a signal whose completion bar is the current,
not-yet-closed period (early/unconfirmed) vs one on a closed candle.
"""
from __future__ import annotations

import datetime

from app.backend.routes.patterns import is_forming_signal

UTC = datetime.timezone.utc
# 2026-07-15 (Wed) 18:00 UTC == 14:00 ET (EDT), mid-session, before the 16:00 close.
MIDDAY = datetime.datetime(2026, 7, 15, 18, 0, tzinfo=UTC)
# 21:00 UTC == 17:00 ET, after the daily close.
AFTER_CLOSE = datetime.datetime(2026, 7, 15, 21, 0, tzinfo=UTC)


def test_daily_today_midsession_is_forming():
    assert is_forming_signal("2026-07-15", "day", now=MIDDAY) is True


def test_daily_today_after_close_is_confirmed():
    assert is_forming_signal("2026-07-15", "day", now=AFTER_CLOSE) is False


def test_daily_prior_day_is_confirmed():
    assert is_forming_signal("2026-07-14", "day", now=MIDDAY) is False


def test_hourly_current_hour_is_forming():
    # 14:00 ET bar during the 14:00 ET hour.
    assert is_forming_signal("2026-07-15 14:00", "1h", now=MIDDAY) is True


def test_hourly_earlier_bar_is_confirmed():
    assert is_forming_signal("2026-07-15 10:00", "1h", now=MIDDAY) is False


def test_bad_date_defaults_to_confirmed():
    assert is_forming_signal("not-a-date", "day", now=MIDDAY) is False
    assert is_forming_signal("", "1h", now=MIDDAY) is False
