"""Two-way Telegram remote control — command routing, the chat_id gate, and the
``remote_enabled`` settings round-trip under both storage backends.

Network I/O (getUpdates / sendMessage) and the LLM agent are stubbed; these pin
the parts that matter for correctness and security: which commands run, that ONLY
the paired chat is ever obeyed, and that the offset still advances past ignored
chats so a stranger can't wedge the queue.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.backend.database.connection import Base
import app.backend.database.app_models  # noqa: F401  (register tables on Base)
from app.backend.services import _storage, telegram_alerts, telegram_notify, telegram_remote


# ─── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def file_settings(monkeypatch, tmp_path):
    """File backend pointed at throwaway alert settings/secrets stores."""
    monkeypatch.setenv("STORAGE_BACKEND", "file")
    monkeypatch.setattr(telegram_alerts, "_SETTINGS_PATH", tmp_path / "alert_settings.json")
    monkeypatch.setattr(telegram_alerts, "_SECRETS_PATH", tmp_path / "alert_secrets.json")
    # Remote control runs bound to the owning user but never needs BYOK keys here.
    monkeypatch.setattr(telegram_remote, "auth_enabled", lambda: False)
    return tmp_path


@pytest.fixture
def db_settings(monkeypatch):
    """DB backend on an isolated in-memory SQLite engine."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_storage, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setenv("STORAGE_BACKEND", "db")
    try:
        yield
    finally:
        engine.dispose()


# ─── command routing ──────────────────────────────────────────────────────────

def test_help_and_start_return_help(file_settings):
    assert "/scan" in asyncio.run(telegram_remote.process_text("u1", "/help"))
    assert "/scan" in asyncio.run(telegram_remote.process_text("u1", "/start"))
    # An empty message falls back to help, not the agent.
    assert "/scan" in asyncio.run(telegram_remote.process_text("u1", "   "))


def test_plaintext_routes_to_agent(file_settings, monkeypatch):
    seen: list[list[dict]] = []

    async def _fake_answer(messages, context):
        seen.append(messages)
        return "NVDA looks strong."

    monkeypatch.setattr("app.backend.services.agent_chat.answer_once", _fake_answer)
    out = asyncio.run(telegram_remote.process_text("u1", "how is NVDA doing?"))
    assert out == "NVDA looks strong."
    assert seen and seen[0][0]["content"] == "how is NVDA doing?"


def test_scan_command_routes_and_formats(file_settings, monkeypatch):
    async def _fake_scan(tickers, detectors, timeframe, lookback):
        assert tickers == ["NVDA", "AMD"]
        return [
            {"ticker": "NVDA", "pattern": "Bull Flag", "confidence": 91, "bullish": True},
            {"ticker": "AMD", "pattern": "Double Top", "confidence": 88, "bullish": False},
        ]

    monkeypatch.setattr("app.backend.routes.patterns.run_pattern_scan", _fake_scan)
    out = asyncio.run(telegram_remote.process_text("u1", "/scan NVDA amd"))
    assert "NVDA" in out and "Bull Flag" in out and "91%" in out
    assert "AMD" in out and "Double Top" in out


def test_scan_without_tickers_shows_usage(file_settings):
    out = asyncio.run(telegram_remote.process_text("u1", "/scan"))
    assert "Usage" in out


def test_stop_disables_remote(file_settings):
    telegram_alerts._save_settings(
        "u1", {"chat_id": "555", "enabled": True, "min_confidence": 90,
               "timeframes": ["day"], "remote_enabled": True}
    )
    out = asyncio.run(telegram_remote.process_text("u1", "/stop"))
    assert "disabled" in out.lower()
    assert telegram_alerts._get_settings("u1")["remote_enabled"] is False


def test_dispatch_error_is_caught(file_settings, monkeypatch):
    async def _boom(messages, context):
        raise RuntimeError("model down")

    monkeypatch.setattr("app.backend.services.agent_chat.answer_once", _boom)
    out = asyncio.run(telegram_remote.process_text("u1", "tell me about TSLA"))
    assert "went wrong" in out.lower()  # never propagates the exception


# ─── the chat_id gate ─────────────────────────────────────────────────────────

def test_poll_obeys_only_paired_chat_and_advances_offset(monkeypatch):
    # Two updates: one from the paired chat, one from a stranger.
    updates = [
        {"update_id": 10, "message": {"chat": {"id": 999}, "text": "hi from stranger"}},
        {"update_id": 11, "message": {"chat": {"id": 555}, "text": "hi owner"}},
    ]

    async def _fake_get_updates(token, offset=None, timeout=0):
        return updates

    processed: list[str] = []

    async def _fake_process(user_id, text):
        processed.append(text)
        return f"echo: {text}"

    sent: list[str] = []

    async def _fake_send(token, chat_id, text, **kw):
        sent.append(text)
        return True

    monkeypatch.setattr(telegram_notify, "get_updates", _fake_get_updates)
    monkeypatch.setattr(telegram_remote, "process_text", _fake_process)
    monkeypatch.setattr(telegram_notify, "send_message", _fake_send)

    next_offset = asyncio.run(telegram_remote._poll_user("u1", "TOK", "555", offset=None))

    # Only the paired chat (555) was handled; the stranger (999) was ignored.
    assert processed == ["hi owner"]
    assert sent == ["echo: hi owner"]
    # Offset still advances PAST the stranger's update so it can't wedge the queue.
    assert next_offset == 12


def test_poll_returns_same_offset_when_no_updates(monkeypatch):
    async def _empty(token, offset=None, timeout=0):
        return []

    monkeypatch.setattr(telegram_notify, "get_updates", _empty)
    assert asyncio.run(telegram_remote._poll_user("u1", "TOK", "555", offset=42)) == 42


def test_drain_backlog_skips_without_processing(monkeypatch):
    async def _fake_get_updates(token, offset=None, timeout=0):
        return [
            {"update_id": 7, "message": {"chat": {"id": 555}, "text": "old"}},
            {"update_id": 8, "message": {"chat": {"id": 555}, "text": "older"}},
        ]

    def _boom(*a, **k):  # process_text must NOT be called during a drain
        raise AssertionError("backlog must not be processed")

    monkeypatch.setattr(telegram_notify, "get_updates", _fake_get_updates)
    monkeypatch.setattr(telegram_remote, "process_text", _boom)
    assert asyncio.run(telegram_remote._drain_backlog("TOK")) == 9  # max(8)+1


# ─── long-message chunking ────────────────────────────────────────────────────

def test_chunk_respects_telegram_limit():
    chunks = telegram_remote._chunk("A" * 9000)
    assert all(len(c) <= telegram_remote._TELEGRAM_MAX_CHARS for c in chunks)
    assert "".join(chunks) == "A" * 9000


# ─── remote_enabled settings round-trip (both backends) ───────────────────────

def test_remote_enabled_round_trip_file(file_settings):
    telegram_alerts._save_settings(
        "u9", {"chat_id": "1", "enabled": True, "min_confidence": 90,
               "timeframes": ["day"], "remote_enabled": True}
    )
    assert telegram_alerts._get_settings("u9")["remote_enabled"] is True
    # Default (unset) is False.
    assert telegram_alerts._get_settings("never-seen")["remote_enabled"] is False


def test_remote_enabled_round_trip_db(db_settings):
    telegram_alerts._save_settings(
        "u9", {"chat_id": "1", "enabled": True, "min_confidence": 90,
               "timeframes": ["day"], "remote_enabled": True}
    )
    assert telegram_alerts._get_settings("u9")["remote_enabled"] is True
    assert telegram_alerts._get_settings("absent")["remote_enabled"] is False


def test_all_remote_users_file_lists_only_ready(file_settings):
    # Ready: remote on + chat_id + token.
    telegram_alerts._save_settings(
        "ready", {"chat_id": "111", "enabled": True, "min_confidence": 90,
                  "timeframes": ["day"], "remote_enabled": True}
    )
    telegram_alerts._set_token("ready", "TOKEN-READY")
    # Remote on but no token -> excluded.
    telegram_alerts._save_settings(
        "notoken", {"chat_id": "222", "enabled": True, "min_confidence": 90,
                    "timeframes": ["day"], "remote_enabled": True}
    )
    # Token + chat but remote OFF -> excluded.
    telegram_alerts._save_settings(
        "off", {"chat_id": "333", "enabled": True, "min_confidence": 90,
                "timeframes": ["day"], "remote_enabled": False}
    )
    telegram_alerts._set_token("off", "TOKEN-OFF")

    users = telegram_alerts.all_remote_users()
    ids = {u["user_id"] for u in users}
    assert ids == {"ready"}
    assert users[0] == {"user_id": "ready", "chat_id": "111", "token": "TOKEN-READY"}
