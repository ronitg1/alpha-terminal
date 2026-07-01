from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_openai import ChatOpenAI

from app.backend.database.app_models import DEFAULT_LLM_MODEL_NAME, DEFAULT_LLM_MODEL_PROVIDER
from app.backend.repositories.portfolio_repository import PortfolioRepository
from app.backend.services._storage import current_user_id, session_scope, use_db
from app.backend.services.api_key_validation import DEEPSEEK, OPENROUTER
from app.backend.services.key_resolver import MissingUserKey, require_key, resolved_api_keys

DEEPSEEK_PROVIDER = "DeepSeek"
OPENROUTER_PROVIDER = "OpenRouter"
LLM_USER_ERROR = "LLM request failed. Check your selected model and API key, then retry."
ALLOWED_MODEL_PROVIDERS = frozenset({DEEPSEEK_PROVIDER, OPENROUTER_PROVIDER})
DEEPSEEK_MODELS = frozenset({"deepseek-reasoner", "deepseek-chat", "deepseek-v4-pro"})

_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "llm_preferences.json"
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}$")


@dataclass(frozen=True)
class ModelPreference:
    model_provider: str
    model_name: str
    preference_saved: bool = False


@dataclass(frozen=True)
class LlmRuntimeConfig:
    api_keys: dict[str, str]
    model_name: str | None = None
    model_provider: str | None = None


def _read_file_map() -> dict[str, dict[str, Any]]:
    if not _DATA_PATH.exists():
        return {}
    try:
        with _DATA_PATH.open(encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_file_map(data: dict[str, dict[str, Any]]) -> None:
    _DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".llm-preferences.", suffix=".tmp", dir=str(_DATA_PATH.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, _DATA_PATH)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _normalize(model_provider: str, model_name: str, *, saved: bool) -> ModelPreference:
    provider = str(model_provider or "").strip()
    model = str(model_name or "").strip()
    if provider not in ALLOWED_MODEL_PROVIDERS:
        raise ValueError("Model provider must be DeepSeek or OpenRouter.")
    if not model or not _MODEL_RE.match(model):
        raise ValueError("Model name must be a valid provider model id.")
    if provider == DEEPSEEK_PROVIDER and model not in DEEPSEEK_MODELS:
        raise ValueError("DeepSeek model must be one of the built-in DeepSeek models.")
    return ModelPreference(model_provider=provider, model_name=model, preference_saved=saved)


def llm_exception_summary(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        return f"{type(exc).__name__} status={status_code}"
    return type(exc).__name__


def _ensure_selectable(pref: ModelPreference) -> None:
    if pref.model_provider != OPENROUTER_PROVIDER:
        return
    try:
        require_key(OPENROUTER)
    except MissingUserKey as exc:
        raise ValueError("Add and verify an OpenRouter API key before selecting OpenRouter models.") from exc


def _default_preference() -> ModelPreference:
    return ModelPreference(
        model_provider=DEFAULT_LLM_MODEL_PROVIDER,
        model_name=DEFAULT_LLM_MODEL_NAME,
        preference_saved=False,
    )


def get_model_preference() -> ModelPreference:
    if use_db():
        with session_scope() as db:
            raw = PortfolioRepository(db, current_user_id()).get_llm_preference()
        try:
            return _normalize(
                str(raw.get("model_provider")),
                str(raw.get("model_name")),
                saved=bool(raw.get("preference_saved")),
            )
        except ValueError:
            return _default_preference()

    raw = _read_file_map().get(current_user_id())
    if not isinstance(raw, dict):
        return _default_preference()
    try:
        return _normalize(
            str(raw.get("model_provider")),
            str(raw.get("model_name")),
            saved=bool(raw.get("preference_saved")),
        )
    except ValueError:
        return _default_preference()


def set_model_preference(model_provider: str, model_name: str) -> ModelPreference:
    pref = _normalize(model_provider, model_name, saved=True)
    _ensure_selectable(pref)
    if use_db():
        with session_scope() as db:
            PortfolioRepository(db, current_user_id()).set_llm_preference(
                pref.model_provider, pref.model_name
            )
        return pref

    data = _read_file_map()
    data[current_user_id()] = {
        "model_provider": pref.model_provider,
        "model_name": pref.model_name,
        "preference_saved": True,
    }
    _write_file_map(data)
    return pref


def runtime_config_for_scan() -> tuple[LlmRuntimeConfig | None, str | None]:
    pref = get_model_preference()
    try:
        if pref.preference_saved and pref.model_provider == OPENROUTER_PROVIDER:
            require_key(OPENROUTER)
        else:
            require_key(DEEPSEEK)
    except MissingUserKey:
        if pref.preference_saved and pref.model_provider == OPENROUTER_PROVIDER:
            return None, "An OpenRouter API key is required for the selected model. Add yours in Settings."
        return None, "A DeepSeek API key is required to run a scan. Add yours in Settings."

    model_name = pref.model_name if pref.preference_saved else None
    model_provider = pref.model_provider if pref.preference_saved else None
    return LlmRuntimeConfig(
        api_keys=resolved_api_keys(),
        model_name=model_name,
        model_provider=model_provider,
    ), None


def state_for_selected_model() -> dict[str, Any]:
    pref = get_model_preference()
    metadata: dict[str, Any] = {"api_keys": resolved_api_keys()}
    if pref.preference_saved:
        metadata["model_name"] = pref.model_name
        metadata["model_provider"] = pref.model_provider
    return {"messages": [], "data": {}, "metadata": metadata}


def openrouter_headers() -> dict[str, str]:
    site_url = (
        os.getenv("OPENROUTER_SITE_URL")
        or os.getenv("YOUR_SITE_URL")
        or "https://github.com/ronitg1/alpha-terminal-cloud"
    )
    site_name = os.getenv("OPENROUTER_APP_NAME") or os.getenv("YOUR_SITE_NAME") or "Alpha Terminal"
    return {"HTTP-Referer": site_url, "X-OpenRouter-Title": site_name}


def create_selected_chat_model(
    *,
    temperature: float,
    max_tokens: int,
    streaming: bool = False,
    default_deepseek_model: str = "deepseek-chat",
):
    pref = get_model_preference()
    provider = pref.model_provider if pref.preference_saved else DEEPSEEK_PROVIDER
    model = pref.model_name if pref.preference_saved else default_deepseek_model

    if provider == OPENROUTER_PROVIDER:
        return ChatOpenAI(
            model=model,
            openai_api_key=require_key(OPENROUTER),
            openai_api_base="https://openrouter.ai/api/v1",
            temperature=temperature,
            max_tokens=max_tokens,
            streaming=streaming,
            model_kwargs={"extra_headers": openrouter_headers()},
        )

    return ChatOpenAI(
        model=model,
        openai_api_key=require_key(DEEPSEEK),
        openai_api_base="https://api.deepseek.com/v1",
        temperature=temperature,
        max_tokens=max_tokens,
        streaming=streaming,
    )
