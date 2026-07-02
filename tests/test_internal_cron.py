"""In-process scheduled-scan cron: enable decision + interval parsing.

The timer loop itself is thin asyncio plumbing around prescan_runner.run_due
(covered elsewhere); these pin the config logic that decides whether/how often it
runs, since that's what gates real scans firing on the cloud deploy.
"""
from __future__ import annotations

import pytest

from app.backend import main
from app.backend.services import _storage


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch):
    for var in ("ENABLE_INTERNAL_CRON", "DISABLE_INTERNAL_CRON", "INTERNAL_CRON_MINUTES"):
        monkeypatch.delenv(var, raising=False)
    yield


def test_enabled_on_db_backend(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_storage, "use_db", lambda: True)
    assert main._internal_cron_enabled() is True


def test_off_on_file_backend_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_storage, "use_db", lambda: False)
    assert main._internal_cron_enabled() is False


def test_enable_flag_forces_on_even_on_file_backend(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_storage, "use_db", lambda: False)
    monkeypatch.setenv("ENABLE_INTERNAL_CRON", "1")
    assert main._internal_cron_enabled() is True


def test_disable_flag_wins_over_db_backend(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_storage, "use_db", lambda: True)
    monkeypatch.setenv("DISABLE_INTERNAL_CRON", "true")
    assert main._internal_cron_enabled() is False


def test_disable_flag_wins_over_enable_flag(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_storage, "use_db", lambda: False)
    monkeypatch.setenv("ENABLE_INTERNAL_CRON", "1")
    monkeypatch.setenv("DISABLE_INTERNAL_CRON", "1")
    assert main._internal_cron_enabled() is False


def test_interval_defaults_and_overrides(monkeypatch: pytest.MonkeyPatch):
    assert main._internal_cron_minutes() == 15
    monkeypatch.setenv("INTERNAL_CRON_MINUTES", "5")
    assert main._internal_cron_minutes() == 5
    monkeypatch.setenv("INTERNAL_CRON_MINUTES", "0")  # clamped to a 1-min floor
    assert main._internal_cron_minutes() == 1
    monkeypatch.setenv("INTERNAL_CRON_MINUTES", "garbage")  # falls back to default
    assert main._internal_cron_minutes() == 15
