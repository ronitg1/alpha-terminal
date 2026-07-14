"""Agentic chat runner — a LangGraph ReAct loop over the read-only tools.

``stream_agent`` is the single entry point: it builds a fresh ReAct agent per
request (the system prompt embeds per-request frontend context, and the model
honors the user's current preference), then maps LangChain's ``astream_events``
v2 stream onto a small typed event vocabulary the SSE route can forward as-is:

    ("text_delta", {"token": str})            — assistant token
    ("tool_call", {"name": str, "args": {}})  — a tool started
    ("tool_result", {"name": str, "ok": bool})— a tool finished
    ("error", {"message": str})               — loop failed (user-safe text)
    ("end", {})                               — always the final event

The plain non-agent chat route (``/sleeves/chat/stream``) stays untouched as a
fallback.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.backend.services.agent_chat_model import create_agent_chat_model
from app.backend.services.agent_tools import build_agent_tools
from app.backend.services.llm_preferences import LLM_USER_ERROR, llm_exception_summary

logger = logging.getLogger(__name__)

# Hard cap on graph steps: each tool round-trip costs 2 steps (agent -> tools),
# so 16 allows ~7 tool calls plus the final answer before the loop bails out.
RECURSION_LIMIT = 16

_AGENT_SYSTEM = """\
You are Alpha Terminal's research assistant — an AI copilot inside a retail \
alpha-generation platform. You have tools that read live quotes, chart-pattern \
scans, signal win rates, trade plans, market movers, catalysts, news, the \
user's brokerage portfolio, institutional ownership, and valuation models.

Rules:
- Ground answers in tool data. Call tools when the question involves current \
prices, signals, news, the portfolio, or anything time-sensitive; answer \
directly from knowledge only for general concepts.
- Be direct and specific; cite tickers and numbers from tool results.
- If a tool returns an error, try a corrected call once, then say what failed.
- Signals only: you are not a licensed advisor and never give personalised \
investment advice or place trades. Describe what the data shows, not what the \
user should do.
- Keep answers under 4 short paragraphs unless asked for more.
"""


def _context_block(context: dict[str, Any]) -> str:
    """Deterministic context lines injected into the system prompt."""
    lines: list[str] = []
    section = context.get("section")
    if section:
        lines.append(f"Current page: {section}")
    ticker = context.get("selectedTicker")
    if ticker:
        lines.append(f"Active ticker: {ticker} (assume questions refer to it unless stated otherwise)")
    return "\n".join(lines)


def _to_lc_messages(messages: list[dict[str, Any]]) -> list[HumanMessage | AIMessage]:
    """Convert the wire-format chat history to LangChain messages."""
    out: list[HumanMessage | AIMessage] = []
    for m in messages:
        content = str(m.get("content") or "")
        if not content:
            continue
        if m.get("role") == "user":
            out.append(HumanMessage(content=content))
        else:
            out.append(AIMessage(content=content))
    return out


def _system_prompt(context: dict[str, Any]) -> str:
    block = _context_block(context)
    if block:
        return _AGENT_SYSTEM + "\n## Current context\n" + block
    return _AGENT_SYSTEM


def _tool_result_ok(output: Any) -> bool:
    """Best-effort success flag for a finished tool call.

    ``astream_events`` may surface the raw tool return (dict) or a wrapping
    ``ToolMessage`` depending on version; treat a payload that looks like our
    ``{"error": ...}`` convention as a failure either way.
    """
    payload = getattr(output, "content", output)
    if isinstance(payload, dict):
        return "error" not in payload
    if isinstance(payload, str):
        return '"error"' not in payload and not payload.startswith("Error:")
    return True


async def stream_agent(
    messages: list[dict[str, Any]],
    context: dict[str, Any],
) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
    """Run the tool-calling agent and yield typed (event_type, data) tuples.

    Never raises: model/tool/graph failures are emitted as an ``error`` event,
    and an ``end`` event always terminates the stream.
    """
    from langgraph.prebuilt import create_react_agent

    try:
        agent = create_react_agent(
            create_agent_chat_model(),
            build_agent_tools(),
            state_modifier=_system_prompt(context),
        )
        stream = agent.astream_events(
            {"messages": _to_lc_messages(messages)},
            version="v2",
            config={"recursion_limit": RECURSION_LIMIT},
        )
        async for ev in stream:
            kind = ev.get("event")
            if kind == "on_chat_model_stream":
                chunk = (ev.get("data") or {}).get("chunk")
                token = getattr(chunk, "content", None)
                if isinstance(token, str) and token:
                    yield ("text_delta", {"token": token})
            elif kind == "on_tool_start":
                args = (ev.get("data") or {}).get("input")
                yield (
                    "tool_call",
                    {"name": ev.get("name") or "tool", "args": args if isinstance(args, dict) else {}},
                )
            elif kind == "on_tool_end":
                output = (ev.get("data") or {}).get("output")
                yield ("tool_result", {"name": ev.get("name") or "tool", "ok": _tool_result_ok(output)})
    except Exception as exc:  # noqa: BLE001 — surface as a user-safe SSE error
        logger.warning("Agent chat stream error: %s", llm_exception_summary(exc))
        yield ("error", {"message": LLM_USER_ERROR})
    yield ("end", {})


def _flatten_content(content: Any) -> str:
    """Collapse a LangChain message ``content`` (str, or a list of content parts)
    into one plain string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict) and isinstance(p.get("text"), str):
                parts.append(p["text"])
        return "".join(parts)
    return str(content or "")


async def answer_once(
    messages: list[dict[str, Any]],
    context: dict[str, Any],
) -> str:
    """Run the tool-calling agent to completion and return ONE plain-text reply.

    The non-streaming sibling of :func:`stream_agent`, for callers that can't
    consume an SSE stream (the Telegram remote poller). Reuses the same model,
    tools, and system prompt, then concatenates the final answer into a string.
    Never raises: any model/tool/graph failure returns the user-safe error text.
    """
    from langgraph.prebuilt import create_react_agent

    try:
        agent = create_react_agent(
            create_agent_chat_model(),
            build_agent_tools(),
            state_modifier=_system_prompt(context),
        )
        result = await agent.ainvoke(
            {"messages": _to_lc_messages(messages)},
            config={"recursion_limit": RECURSION_LIMIT},
        )
        for msg in reversed(result.get("messages") or []):
            if isinstance(msg, AIMessage):
                text = _flatten_content(msg.content).strip()
                if text:
                    return text
        return "I couldn't produce an answer for that — try rephrasing?"
    except Exception as exc:  # noqa: BLE001 — best-effort; return user-safe text
        logger.warning("Agent answer_once error: %s", llm_exception_summary(exc))
        return LLM_USER_ERROR
