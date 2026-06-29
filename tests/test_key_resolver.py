"""Tests for the per-user-with-shared-fallback key resolver (Phase 3, step 4) and
the scan's per-user key injection into the LLM call path.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.backend import context as ctx
from app.backend.database.connection import Base
import app.backend.database.app_models  # noqa: F401
import app.backend.database.models  # noqa: F401  (api_keys table)
from app.backend.repositories.api_key_repository import ApiKeyRepository
from app.backend.services import _storage
from app.backend.services import key_resolver


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DEEPSEEK_API_KEY", "env-deepseek")
    monkeypatch.setenv("MASSIVE_API_KEY", "env-massive")
    monkeypatch.setenv("FINNHUB_API_KEY", "env-finnhub")
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    yield


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr(_storage, "SessionLocal", TestSession)
    monkeypatch.setenv("STORAGE_BACKEND", "db")
    yield TestSession
    engine.dispose()


@pytest.fixture()
def as_user():
    """Bind a current user for the duration of a test."""
    tokens = []

    def _set(uid):
        tokens.append(ctx.set_current_user_id(uid))

    yield _set
    for t in reversed(tokens):
        ctx.reset_current_user_id(t)


# ─── auth OFF: always the shared env key (dormant, no DB) ─────────────────────


def test_auth_off_returns_env_for_all_providers():
    assert key_resolver.resolve_key("deepseek") == "env-deepseek"
    assert key_resolver.resolve_key("massive") == "env-massive"
    assert key_resolver.resolve_key("finnhub") == "env-finnhub"


# ─── auth ON ─────────────────────────────────────────────────────────────────


def test_auth_on_deepseek_requires_user_key(monkeypatch, db, as_user):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    as_user("user_a")
    # No stored key -> no env fallback for deepseek.
    assert key_resolver.resolve_key("deepseek") is None
    with pytest.raises(key_resolver.MissingUserKey):
        key_resolver.require_key("deepseek")


def test_auth_on_deepseek_uses_stored_user_key(monkeypatch, db, as_user):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    with db() as s:
        ApiKeyRepository(s, "user_a").set_key("deepseek", "alice-deepseek")
    as_user("user_a")
    assert key_resolver.resolve_key("deepseek") == "alice-deepseek"


def test_auth_on_massive_falls_back_to_shared_env(monkeypatch, db, as_user):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    as_user("user_a")
    # No stored massive key -> shared env fallback is allowed.
    assert key_resolver.resolve_key("massive") == "env-massive"
    assert key_resolver.resolve_key("finnhub") == "env-finnhub"


def test_auth_on_massive_prefers_user_key(monkeypatch, db, as_user):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    with db() as s:
        ApiKeyRepository(s, "user_a").set_key("massive", "alice-massive")
    as_user("user_a")
    assert key_resolver.resolve_key("massive") == "alice-massive"


def test_auth_on_isolates_users(monkeypatch, db, as_user):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    with db() as s:
        ApiKeyRepository(s, "user_a").set_key("deepseek", "a-key")
        ApiKeyRepository(s, "user_b").set_key("deepseek", "b-key")
    as_user("user_b")
    assert key_resolver.resolve_key("deepseek") == "b-key"


# ─── scan injection: metadata api_keys reach get_model via call_llm ───────────


class _Tiny(BaseModel):
    ok: bool = True


def test_call_llm_uses_metadata_api_keys(monkeypatch):
    from src.utils import llm as llm_mod

    captured: dict = {}

    def _fake_get_model(model_name, model_provider, api_keys=None):
        captured["api_keys"] = api_keys
        raise RuntimeError("stop-after-capture")

    monkeypatch.setattr(llm_mod, "get_model", _fake_get_model)
    state = {"data": {}, "metadata": {"api_keys": {"DEEPSEEK_API_KEY": "user-key"}}}

    with pytest.raises(RuntimeError):
        llm_mod.call_llm("hi", _Tiny, state=state)

    assert captured["api_keys"] == {"DEEPSEEK_API_KEY": "user-key"}


def test_resolved_api_keys_auth_off_uses_env():
    d = key_resolver.resolved_api_keys()
    assert d["DEEPSEEK_API_KEY"] == "env-deepseek"
    assert d["MASSIVE_API_KEY"] == "env-massive"
    assert d["FINNHUB_API_KEY"] == "env-finnhub"


def test_resolved_api_keys_auth_on_missing_deepseek_is_blank(monkeypatch, db, as_user):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    as_user("user_a")
    d = key_resolver.resolved_api_keys()
    assert d["DEEPSEEK_API_KEY"] == ""           # fail-closed: get_model won't env-fallback
    assert d["MASSIVE_API_KEY"] == "env-massive"  # shared fallback still applies


def test_get_model_dict_is_authoritative_no_env_fallback(monkeypatch):
    """The owner-billing leak guard: an explicit api_keys dict without a DeepSeek
    key must NOT fall back to the shared env key."""
    from src.llm import models as m

    monkeypatch.setenv("DEEPSEEK_API_KEY", "OWNER-ENV-KEY")

    # Dict supplied but DeepSeek missing/blank -> raise, do not use env.
    with pytest.raises(ValueError):
        m.get_model("deepseek-chat", m.ModelProvider.DEEPSEEK, api_keys={"DEEPSEEK_API_KEY": ""})
    with pytest.raises(ValueError):
        m.get_model("deepseek-chat", m.ModelProvider.DEEPSEEK, api_keys={})

    # CLI / legacy path (api_keys is None) still uses env.
    model = m.get_model("deepseek-chat", m.ModelProvider.DEEPSEEK, api_keys=None)
    assert model is not None

    # A supplied user key is honored.
    user_model = m.get_model("deepseek-chat", m.ModelProvider.DEEPSEEK, api_keys={"DEEPSEEK_API_KEY": "USER-KEY"})
    assert user_model is not None


def test_run_sleeve_threads_api_keys_into_state(monkeypatch):
    """run_sleeve must place the api_keys override into the agent state metadata."""
    import src.run_morning_scan as rms

    seen: dict = {}

    def _fake_agent(state, agent_id=None):
        seen["api_keys"] = state["metadata"].get("api_keys")

    monkeypatch.setitem(rms.ANALYST_CONFIG, "alpha_seeker", {"agent_func": _fake_agent, "display_name": "x"})
    sleeve = {"tickers": ["NVDA"], "agents": ["alpha_seeker"], "agent_weights": {"alpha_seeker": 1.0}}

    rms.run_sleeve("test", sleeve, "2026-06-27", api_keys={"DEEPSEEK_API_KEY": "k"})
    assert seen["api_keys"] == {"DEEPSEEK_API_KEY": "k"}
