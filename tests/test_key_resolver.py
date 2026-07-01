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
from app.backend.repositories.portfolio_repository import PortfolioRepository
from app.backend.repositories.api_key_repository import ApiKeyRepository
from app.backend.services import _storage
from app.backend.services import key_resolver


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DEEPSEEK_API_KEY", "env-deepseek")
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-openrouter")
    monkeypatch.setenv("MASSIVE_API_KEY", "env-massive")
    monkeypatch.setenv("FINNHUB_API_KEY", "env-finnhub")
    monkeypatch.setenv("ROBINHOOD_MCP_BEARER_TOKEN", "env-robinhood")
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    monkeypatch.delenv("OWNER_USER_ID", raising=False)
    monkeypatch.delenv("OWNER_EMAIL", raising=False)
    monkeypatch.delenv("SHARED_DATA_EMAILS", raising=False)
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

    def _set(uid, email=None, email_verified=False):
        tokens.append(ctx.set_current_user_identity(uid, email, email_verified))

    yield _set
    for t in reversed(tokens):
        ctx.reset_current_user_identity(t)


# ─── auth OFF: always the shared env key (dormant, no DB) ─────────────────────


def test_auth_off_returns_env_for_all_providers():
    assert key_resolver.resolve_key("deepseek") == "env-deepseek"
    assert key_resolver.resolve_key("openrouter") == "env-openrouter"
    assert key_resolver.resolve_key("robinhood") == "env-robinhood"
    assert key_resolver.resolve_key("massive") == "env-massive"
    assert key_resolver.resolve_key("finnhub") == "env-finnhub"


def test_key_context_strips_whitespace_from_env_key(monkeypatch):
    """A stray leading/trailing space in an env key (easy to paste into a hosting
    dashboard) must not reach the client and 401 — getters return it stripped."""
    from src.tools import key_context

    monkeypatch.setenv("FINNHUB_API_KEY", "  spaced-key  ")
    assert key_context.finnhub_api_key() == "spaced-key"


# ─── auth ON ─────────────────────────────────────────────────────────────────


def test_auth_on_deepseek_requires_user_key(monkeypatch, db, as_user):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    as_user("user_a")
    # No stored key -> no env fallback for deepseek.
    assert key_resolver.resolve_key("deepseek") is None
    with pytest.raises(key_resolver.MissingUserKey):
        key_resolver.require_key("deepseek")


def test_auth_on_openrouter_requires_user_key(monkeypatch, db, as_user):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    as_user("user_a")
    assert key_resolver.resolve_key("openrouter") is None
    with pytest.raises(key_resolver.MissingUserKey):
        key_resolver.require_key("openrouter")


def test_auth_on_deepseek_uses_stored_user_key(monkeypatch, db, as_user):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    with db() as s:
        ApiKeyRepository(s, "user_a").set_key("deepseek", "alice-deepseek")
    as_user("user_a")
    assert key_resolver.resolve_key("deepseek") == "alice-deepseek"


def test_auth_on_openrouter_uses_stored_user_key(monkeypatch, db, as_user):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    with db() as s:
        ApiKeyRepository(s, "user_a").set_key("openrouter", "alice-openrouter")
    as_user("user_a")
    assert key_resolver.resolve_key("openrouter") == "alice-openrouter"


def test_auth_on_robinhood_requires_user_key(monkeypatch, db, as_user):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    as_user("user_a")
    assert key_resolver.resolve_key("robinhood") is None
    with pytest.raises(key_resolver.MissingUserKey):
        key_resolver.require_key("robinhood")


def test_auth_on_robinhood_uses_stored_user_key(monkeypatch, db, as_user):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    with db() as s:
        ApiKeyRepository(s, "user_a").set_key("robinhood", "alice-robinhood")
    as_user("user_a")
    assert key_resolver.resolve_key("robinhood") == "alice-robinhood"


def test_auth_on_massive_no_fallback_for_unapproved(monkeypatch, db, as_user):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    as_user("user_a", email="nobody@example.com", email_verified=True)
    # Not on the allowlist -> NO shared fallback; must bring their own.
    assert key_resolver.resolve_key("massive") is None
    assert key_resolver.resolve_key("finnhub") is None


def test_auth_on_massive_shared_for_allowlisted_email(monkeypatch, db, as_user):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    monkeypatch.setenv("SHARED_DATA_EMAILS", "friend@example.com, other@example.com")
    as_user("user_a", email="friend@example.com", email_verified=True)
    assert key_resolver.resolve_key("massive") == "env-massive"
    assert key_resolver.resolve_key("finnhub") == "env-finnhub"


def test_auth_on_shared_requires_verified_email(monkeypatch, db, as_user):
    # An UNVERIFIED allowlisted email must NOT get the shared key (anti-spoof).
    monkeypatch.setenv("AUTH_ENABLED", "1")
    monkeypatch.setenv("SHARED_DATA_EMAILS", "friend@example.com")
    as_user("user_a", email="friend@example.com", email_verified=False)
    assert key_resolver.resolve_key("massive") is None


def test_auth_on_owner_gets_shared_by_user_id(monkeypatch, db, as_user):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    monkeypatch.setenv("OWNER_USER_ID", "user_owner")
    as_user("user_owner")  # no email needed — sub match
    assert key_resolver.resolve_key("massive") == "env-massive"


def test_auth_on_massive_prefers_user_key(monkeypatch, db, as_user):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    with db() as s:
        ApiKeyRepository(s, "user_a").set_key("massive", "alice-massive")
    as_user("user_a")  # not approved, but has own key -> uses it
    assert key_resolver.resolve_key("massive") == "alice-massive"


def test_auth_on_isolates_users(monkeypatch, db, as_user):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    with db() as s:
        ApiKeyRepository(s, "user_a").set_key("deepseek", "a-key")
        ApiKeyRepository(s, "user_b").set_key("deepseek", "b-key")
    as_user("user_b")
    assert key_resolver.resolve_key("deepseek") == "b-key"


# ─── provider_keys_for_request (the middleware batch helper) ─────────────────


def test_provider_keys_for_request_unapproved_gated(monkeypatch, db):
    # Unapproved users: Massive/FDS are gated (None), but Finnhub is always
    # shared (free-tier public data) so it still returns the env key.
    monkeypatch.setenv("AUTH_ENABLED", "1")
    massive, finnhub, fds = key_resolver.provider_keys_for_request("user_a", "nobody@example.com", True)
    assert massive is None
    assert finnhub == "env-finnhub"
    assert fds is None


def test_provider_keys_for_request_approved_returns_env(monkeypatch, db):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    monkeypatch.setenv("SHARED_DATA_EMAILS", "friend@example.com")
    monkeypatch.setenv("FINANCIAL_DATASETS_API_KEY", "env-fds")
    massive, finnhub, fds = key_resolver.provider_keys_for_request("user_a", "friend@example.com", True)
    assert massive == "env-massive" and finnhub == "env-finnhub" and fds == "env-fds"


def test_provider_keys_for_request_prefers_stored(monkeypatch, db):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    with db() as s:
        ApiKeyRepository(s, "user_a").set_key("massive", "alice-massive")
    massive, finnhub, fds = key_resolver.provider_keys_for_request("user_a", "nobody@example.com", True)
    assert massive == "alice-massive"     # own key used even though unapproved
    assert finnhub == "env-finnhub"       # Finnhub is always shared (free-tier)
    assert fds is None                    # FDS is shared-only; unapproved -> none


def test_key_context_binding_and_default(monkeypatch):
    from src.tools import key_context

    monkeypatch.setenv("MASSIVE_API_KEY", "env-m")
    monkeypatch.setenv("FINNHUB_API_KEY", "env-f")
    # Unset -> env.
    assert key_context.massive_api_key() == "env-m"
    # Bound to a value -> value; bound to None -> "" (explicit no key, no env).
    tokens = key_context.set_provider_keys(massive="user-m", finnhub=None, financial_datasets=None)
    try:
        assert key_context.massive_api_key() == "user-m"
        assert key_context.finnhub_api_key() == ""   # NOT env-f
        assert key_context.financial_datasets_api_key() == ""
    finally:
        key_context.reset_provider_keys(tokens)
    assert key_context.massive_api_key() == "env-m"  # restored


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
    assert d["OPENROUTER_API_KEY"] == "env-openrouter"
    assert d["MASSIVE_API_KEY"] == "env-massive"
    assert d["FINNHUB_API_KEY"] == "env-finnhub"


def test_resolved_api_keys_auth_on_unapproved_all_blank(monkeypatch, db, as_user):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    as_user("user_a", email="nobody@example.com", email_verified=True)
    d = key_resolver.resolved_api_keys()
    assert d["DEEPSEEK_API_KEY"] == ""   # never shared
    assert d["OPENROUTER_API_KEY"] == ""
    assert d["MASSIVE_API_KEY"] == ""    # not approved -> no shared fallback
    assert d["FINNHUB_API_KEY"] == ""


def test_resolved_api_keys_auth_on_approved_gets_shared_data(monkeypatch, db, as_user):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    monkeypatch.setenv("OWNER_USER_ID", "user_a")
    as_user("user_a")
    d = key_resolver.resolved_api_keys()
    assert d["DEEPSEEK_API_KEY"] == ""            # DeepSeek still per-user only
    assert d["OPENROUTER_API_KEY"] == ""
    assert d["MASSIVE_API_KEY"] == "env-massive"  # approved -> shared market data
    assert d["FINNHUB_API_KEY"] == "env-finnhub"


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


def test_get_openrouter_model_dict_is_authoritative_no_env_fallback(monkeypatch):
    from src.llm import models as m

    monkeypatch.setenv("OPENROUTER_API_KEY", "OWNER-ENV-KEY")

    with pytest.raises(ValueError):
        m.get_model("openai/gpt-5.2", m.ModelProvider.OPENROUTER, api_keys={"OPENROUTER_API_KEY": ""})
    with pytest.raises(ValueError):
        m.get_model("openai/gpt-5.2", m.ModelProvider.OPENROUTER, api_keys={})

    model = m.get_model("openai/gpt-5.2", m.ModelProvider.OPENROUTER, api_keys=None)
    assert model is not None


def test_openrouter_model_preference_runtime_config(monkeypatch, db, as_user):
    from app.backend.services import llm_preferences

    monkeypatch.setenv("AUTH_ENABLED", "1")
    monkeypatch.setenv("MASSIVE_API_KEY", "env-massive")
    monkeypatch.setenv("OWNER_USER_ID", "user_a")
    as_user("user_a")

    with db() as s:
        PortfolioRepository(s, "user_a").set_llm_preference("OpenRouter", "anthropic/claude-sonnet-4.5")

    config, error = llm_preferences.runtime_config_for_scan()
    assert config is None
    assert error and "OpenRouter API key" in error

    with db() as s:
        ApiKeyRepository(s, "user_a").set_key("openrouter", "alice-openrouter")

    pref = llm_preferences.set_model_preference("OpenRouter", "anthropic/claude-sonnet-4.5")
    assert pref.model_provider == "OpenRouter"
    assert pref.model_name == "anthropic/claude-sonnet-4.5"

    config, error = llm_preferences.runtime_config_for_scan()
    assert error is None
    assert config is not None
    assert config.api_keys["OPENROUTER_API_KEY"] == "alice-openrouter"
    assert config.model_provider == "OpenRouter"
    assert config.model_name == "anthropic/claude-sonnet-4.5"


def test_run_sleeve_threads_api_keys_into_state(monkeypatch):
    """run_sleeve must place the api_keys override into the agent state metadata."""
    import src.run_morning_scan as rms

    seen: dict = {}

    def _fake_agent(state, agent_id=None):
        seen["api_keys"] = state["metadata"].get("api_keys")
        seen["model_name"] = state["metadata"].get("model_name")
        seen["model_provider"] = state["metadata"].get("model_provider")

    monkeypatch.setitem(rms.ANALYST_CONFIG, "alpha_seeker", {"agent_func": _fake_agent, "display_name": "x"})
    sleeve = {"tickers": ["NVDA"], "agents": ["alpha_seeker"], "agent_weights": {"alpha_seeker": 1.0}}

    rms.run_sleeve(
        "test",
        sleeve,
        "2026-06-27",
        api_keys={"OPENROUTER_API_KEY": "k"},
        model_name="anthropic/claude-sonnet-4.5",
        model_provider="OpenRouter",
    )
    assert seen["api_keys"] == {"OPENROUTER_API_KEY": "k"}
    assert seen["model_name"] == "anthropic/claude-sonnet-4.5"
    assert seen["model_provider"] == "OpenRouter"
