"""Tests for the agentic chat stack: model factory, tool registry, and runner.

No network: the model factory tests monkeypatch the preference + key lookup,
and the runner test drives ``stream_agent`` with a fake tool-calling chat model
so the full LangGraph ReAct loop runs in-process.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import tool

from app.backend.services import agent_chat
from app.backend.services import llm_preferences
from app.backend.services.agent_chat import stream_agent
from app.backend.services.agent_chat_model import (
    AGENT_MAX_TOKENS,
    AGENT_TEMPERATURE,
    agent_model_label,
    create_agent_chat_model,
)
from app.backend.services.agent_tools import build_agent_tools
from app.backend.services.llm_preferences import ModelPreference


# ─── create_agent_chat_model ────────────────────────────────────────────────


def _patch_preference(monkeypatch: pytest.MonkeyPatch, provider: str, model: str) -> None:
    monkeypatch.setattr(
        llm_preferences,
        "get_model_preference",
        lambda: ModelPreference(model_provider=provider, model_name=model, preference_saved=True),
    )
    monkeypatch.setattr(llm_preferences, "require_key", lambda _provider: "sk-test-key")


def test_agent_model_swaps_r1_to_v3(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_preference(monkeypatch, "DeepSeek", "deepseek-reasoner")
    llm = create_agent_chat_model()
    assert llm.model_name == "deepseek-chat"
    assert llm.streaming is True
    assert llm.temperature == AGENT_TEMPERATURE
    assert llm.max_tokens == AGENT_MAX_TOKENS


def test_agent_model_keeps_non_r1_deepseek(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_preference(monkeypatch, "DeepSeek", "deepseek-v4-pro")
    assert create_agent_chat_model().model_name == "deepseek-v4-pro"


def test_agent_model_passes_openrouter_through(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_preference(monkeypatch, "OpenRouter", "deepseek/deepseek-r1")
    llm = create_agent_chat_model()
    assert llm.model_name == "deepseek/deepseek-r1"
    assert llm.streaming is True


def test_agent_model_label(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_preference(monkeypatch, "DeepSeek", "deepseek-reasoner")
    assert agent_model_label() == "DeepSeek V3"


# ─── build_agent_tools ──────────────────────────────────────────────────────


def test_agent_tools_unique_names_and_valid_schemas() -> None:
    tools = build_agent_tools()
    assert len(tools) >= 10
    names = [t.name for t in tools]
    assert len(names) == len(set(names)), f"duplicate tool names: {names}"
    for t in tools:
        assert t.description, f"tool {t.name} has no description"
        args = t.args  # raises if the args schema is malformed
        assert isinstance(args, dict)
        # Every declared arg must be a flat JSON-schema property with a type.
        for arg_name, spec in args.items():
            assert isinstance(spec, dict), f"{t.name}.{arg_name} schema is not a dict"


def test_agent_tools_expected_names_present() -> None:
    names = {t.name for t in build_agent_tools()}
    expected = {
        "get_quotes",
        "scan_patterns",
        "get_signal_win_rate",
        "get_trade_plan",
        "get_market_movers",
        "get_market_snapshot",
        "get_catalyst_calendar",
        "get_ticker_news",
        "get_portfolio_overview",
        "get_portfolio_stats",
        "get_ownership",
        "get_valuation",
    }
    assert expected <= names


# ─── stream_agent with a fake tool-calling model ────────────────────────────


@tool
def fake_lookup(ticker: str) -> dict:
    """Look up a fake fact about a ticker (test fixture tool)."""
    return {"ticker": ticker, "fact": 42}


class _FakeToolCallingModel(BaseChatModel):
    """Emits one tool call on the first turn, then a streamed final answer.

    The turn is inferred from the presence of a ToolMessage in the prompt, so
    the model needs no mutable state.
    """

    @property
    def _llm_type(self) -> str:
        return "fake-tool-calling"

    def bind_tools(self, tools: Any, **kwargs: Any) -> _FakeToolCallingModel:
        return self

    def _is_second_turn(self, messages: list[Any]) -> bool:
        return any(isinstance(m, ToolMessage) for m in messages)

    def _generate(self, messages: list[Any], stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        if self._is_second_turn(messages):
            msg = AIMessage(content="The answer is 42.")
        else:
            msg = AIMessage(
                content="",
                tool_calls=[{"name": "fake_lookup", "args": {"ticker": "NVDA"}, "id": "call_1"}],
            )
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _stream(self, messages: list[Any], stop: Any = None, run_manager: Any = None, **kwargs: Any):
        if self._is_second_turn(messages):
            for token in ("The answer", " is 42."):
                yield ChatGenerationChunk(message=AIMessageChunk(content=token))
        else:
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": "fake_lookup",
                            "args": json.dumps({"ticker": "NVDA"}),
                            "id": "call_1",
                            "index": 0,
                        }
                    ],
                )
            )


def test_stream_agent_emits_tool_and_text_events(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_chat, "create_agent_chat_model", lambda: _FakeToolCallingModel())
    monkeypatch.setattr(agent_chat, "build_agent_tools", lambda: [fake_lookup])

    async def _collect() -> list[tuple[str, dict]]:
        return [
            event
            async for event in stream_agent(
                [{"role": "user", "content": "What is the answer for NVDA?"}],
                {"section": "market", "selectedTicker": "NVDA"},
            )
        ]

    events = asyncio.run(_collect())
    kinds = [k for k, _ in events]

    assert "tool_call" in kinds
    tool_call = next(d for k, d in events if k == "tool_call")
    assert tool_call["name"] == "fake_lookup"
    assert tool_call["args"] == {"ticker": "NVDA"}

    assert "tool_result" in kinds
    tool_result = next(d for k, d in events if k == "tool_result")
    assert tool_result["name"] == "fake_lookup"
    assert tool_result["ok"] is True

    text = "".join(d["token"] for k, d in events if k == "text_delta")
    assert text == "The answer is 42."

    assert "error" not in kinds
    assert events[-1] == ("end", {})


def test_stream_agent_survives_model_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> BaseChatModel:
        raise RuntimeError("no key configured")

    monkeypatch.setattr(agent_chat, "create_agent_chat_model", _boom)

    async def _collect() -> list[tuple[str, dict]]:
        return [e async for e in stream_agent([{"role": "user", "content": "hi"}], {})]

    events = asyncio.run(_collect())
    kinds = [k for k, _ in events]
    assert kinds[0] == "error"
    assert events[-1] == ("end", {})
