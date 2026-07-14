"""Chat-model factory for the agentic (tool-calling) research assistant.

Why a separate factory from ``create_selected_chat_model``: the agent loop
requires reliable OpenAI-style tool calling. DeepSeek R1 (``deepseek-reasoner``)
is slow and unreliable at tool calls, so when the user's saved preference is R1
we silently substitute V3 (``deepseek-chat``) for the agent loop only. Plain
(non-agent) chat and scans keep honoring the saved preference. OpenRouter
models pass through unchanged — the user picked them explicitly and OpenRouter
model ids are namespaced, so they never collide with the R1 id.
"""
from __future__ import annotations

from langchain_openai import ChatOpenAI

from app.backend.services.llm_preferences import create_selected_chat_model

AGENT_TEMPERATURE = 0.3
AGENT_MAX_TOKENS = 1200

_R1_MODEL = "deepseek-reasoner"
_TOOL_SAFE_DEEPSEEK = "deepseek-chat"

# Display labels for the models we may end up running the loop on.
_MODEL_LABELS = {
    "deepseek-chat": "DeepSeek V3",
    "deepseek-v4-pro": "DeepSeek V4 Pro",
}


def create_agent_chat_model() -> ChatOpenAI:
    """Build the streaming chat model used by the tool-calling agent loop.

    Reuses ``create_selected_chat_model`` (per-user BYOK preference, DeepSeek or
    OpenRouter) and then applies the one agent-specific rule: if the resolved
    model is DeepSeek R1, swap to DeepSeek V3. Only the bare DeepSeek id
    ``deepseek-reasoner`` can match — OpenRouter ids are ``vendor/model``
    namespaced — so the swap never touches OpenRouter selections.
    """
    llm = create_selected_chat_model(
        temperature=AGENT_TEMPERATURE,
        max_tokens=AGENT_MAX_TOKENS,
        streaming=True,
        default_deepseek_model=_TOOL_SAFE_DEEPSEEK,
    )
    if getattr(llm, "model_name", "") == _R1_MODEL:
        llm.model_name = _TOOL_SAFE_DEEPSEEK
    return llm


def agent_model_label(llm: ChatOpenAI | None = None) -> str:
    """Human-readable label for the model the agent loop will use.

    Pass an already-built model to avoid re-reading the preference; otherwise
    the preference is resolved the same way ``create_agent_chat_model`` does.
    """
    if llm is None:
        llm = create_agent_chat_model()
    name = getattr(llm, "model_name", "") or ""
    return _MODEL_LABELS.get(name, name or "unknown model")
