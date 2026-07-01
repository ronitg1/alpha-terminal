"""LLM-powered thesis synthesis for the Sleeves dashboard.

Three scopes, same shape: each call returns a ``ThesisOutput`` with a
``condensed`` (1-3 sentences) and ``full`` (multi-paragraph PM memo) view.

Voice: PM memo — "we see", "our view", first-person plural. Tone is
declarative + risk-aware, never breathless. Cites specific tickers and
quantified signals from the scan.

Caching: keyed by (scope, scan_date, signature). The signature is a hash
of the rows feeding the synthesis, so re-running the same scan produces
the same thesis without burning LLM credits. A new scan invalidates.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from app.backend.services.llm_preferences import state_for_selected_model
from src.utils.llm import call_llm

logger = logging.getLogger(__name__)


# ─── Output schema ──────────────────────────────────────────────────────────


class ThesisOutput(BaseModel):
    """Synthesised thesis at any scope (portfolio / sleeve / ticker)."""

    condensed: str = Field(
        ...,
        description=(
            "1-3 sentence top-line read suitable for a glance — what is "
            "actionable, what is the dominant theme, where is the conviction."
        ),
    )
    full: str = Field(
        ...,
        description=(
            "Multi-paragraph PM memo, plain prose, no bullets. First "
            "paragraph: the directional view. Middle: cites specific "
            "tickers and signals. Final: risks, what would invalidate the "
            "view, and a watchlist call-out."
        ),
    )
    bias: str = Field(
        ...,
        description="One of: bullish / bearish / mixed / neutral.",
    )
    top_long: str | None = Field(
        None, description="Best-conviction long ticker if any."
    )
    top_short: str | None = Field(
        None, description="Best-conviction short ticker if any."
    )


# ─── Cache ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _CacheKey:
    scope: str  # 'portfolio' | f'sleeve:{name}' | f'ticker:{symbol}'
    scan_date: str
    signature: str


_cache: dict[_CacheKey, dict[str, Any]] = {}
_cache_lock = threading.Lock()


def _signature(rows: list[dict]) -> str:
    """Stable hash of the rows feeding the synthesis. Sort keys so dict
    ordering doesn't perturb the hash."""
    payload = json.dumps(rows, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _cache_get(key: _CacheKey) -> dict[str, Any] | None:
    with _cache_lock:
        return _cache.get(key)


def _cache_put(key: _CacheKey, value: dict[str, Any]) -> None:
    with _cache_lock:
        _cache[key] = value


# ─── Prompt builders ───────────────────────────────────────────────────────


_PORTFOLIO_SYSTEM = """You are the PM of a long/short equity hedge fund writing
a same-day thesis memo from your dashboard scan. The book is organized into
named portfolios; refer to those groups as "portfolios", never "sleeves".

Voice rules:
- First-person plural ("we see", "our view", "the book").
- Declarative, risk-aware. No hedge words like "potentially" "may"
  "could be." Either we have conviction or we abstain.
- Quote specific tickers and the conviction numbers, do not generalize.
- Plain prose, no bullets, no headings inside paragraphs. The frontend
  separates condensed vs full views.

Framework for the full memo (3 paragraphs):
  Para 1 — directional view across the book + the dominant theme.
  Para 2 — top 2-4 names by conviction with one-sentence reasoning each.
  Para 3 — what would invalidate the view (risk flags), and the
            watchlist queue.

If the scan is genuinely mixed/flat, say so plainly and explain why
abstention is the right call — never invent conviction.
"""


_SLEEVE_SYSTEM = """You are the sector PM writing a thesis on one portfolio
within the book, post-scan. Same voice rules as the overall PM:

- First-person plural.
- Specific tickers + numbers.
- Plain prose, no bullets.
- Refer to the group as a "portfolio" (never a "sleeve").

Framework for the full memo (2-3 paragraphs):
  Para 1 — directional view on this portfolio, dominant theme.
  Para 2 — top long + top short with one-sentence reasoning each. If
            the portfolio is one-sided (e.g. all bullish), say so.
  Para 3 — risk to the view + any structural callouts (e.g. for an
            energy-transition portfolio: IRA / FEOC exposure across it).

The condensed view is one sentence: "We are [bias] on [portfolio name],
led by [ticker]."
"""


_TICKER_SYSTEM = """You are an analyst writing a one-page thesis on a single
ticker, given the per-agent panel verdicts plus the price / fundamentals
context. Voice:

- Declarative, first-person plural.
- No bullets — plain prose.
- Always quote at least one specific number from the data.

Framework for the full memo:
  Para 1 — directional view + variant perception (if any).
  Para 2 — what the agent panel agrees / disagrees on.
  Para 3 — kill switch + position sizing implication.

Condensed view: one sentence stating the view + conviction level.
"""


def _build_portfolio_prompt(
    scan_date: str,
    portfolio_rollup: dict[str, Any],
    per_sleeve: list[dict[str, Any]],
    high_conviction: list[dict[str, Any]],
) -> Any:
    template = ChatPromptTemplate.from_messages(
        [
            ("system", _PORTFOLIO_SYSTEM),
            (
                "human",
                """Scan date: {scan_date}

                Portfolio rollup:
                {portfolio_rollup}

                Per-portfolio readouts (allocation, bias, conviction, signal mix):
                {per_sleeve}

                High-conviction signals (ticker, portfolio, signal, confidence,
                variant_perception, position):
                {high_conviction}

                Write the thesis as JSON exactly matching this schema:
                {{
                  "condensed": "<1-3 sentences>",
                  "full": "<multi-paragraph PM memo>",
                  "bias": "bullish" | "bearish" | "mixed" | "neutral",
                  "top_long": "<ticker or null>",
                  "top_short": "<ticker or null>"
                }}
                """,
            ),
        ]
    )
    return template.invoke(
        {
            "scan_date": scan_date,
            "portfolio_rollup": json.dumps(portfolio_rollup, indent=2, default=str),
            "per_sleeve": json.dumps(per_sleeve, indent=2, default=str),
            "high_conviction": json.dumps(high_conviction, indent=2, default=str),
        }
    )


def _build_sleeve_prompt(
    sleeve_name: str,
    scan_date: str,
    sleeve_meta: dict[str, Any],
    rows: list[dict[str, Any]],
) -> Any:
    template = ChatPromptTemplate.from_messages(
        [
            ("system", _SLEEVE_SYSTEM),
            (
                "human",
                """Portfolio: {sleeve_name}
                Scan date: {scan_date}

                Portfolio metadata (allocation, agents, agent_weights):
                {sleeve_meta}

                Ticker rows (signal, weighted_score, avg_confidence,
                variant_perception, position_type, per_agent verdicts):
                {rows}

                Return JSON exactly matching:
                {{
                  "condensed": "<1 sentence>",
                  "full": "<2-3 paragraphs>",
                  "bias": "bullish" | "bearish" | "mixed" | "neutral",
                  "top_long": "<ticker or null>",
                  "top_short": "<ticker or null>"
                }}
                """,
            ),
        ]
    )
    return template.invoke(
        {
            "sleeve_name": sleeve_name,
            "scan_date": scan_date,
            "sleeve_meta": json.dumps(sleeve_meta, indent=2, default=str),
            "rows": json.dumps(rows, indent=2, default=str),
        }
    )


# ─── Default factories for LLM failure ─────────────────────────────────────


def _default_portfolio(portfolio_rollup: dict[str, Any]) -> ThesisOutput:
    """Returned when the LLM call fails — keeps the UI from blanking."""
    return ThesisOutput(
        condensed=(
            f"LLM synthesis unavailable. Scan returned "
            f"{portfolio_rollup.get('bullish', 0)} bullish vs "
            f"{portfolio_rollup.get('bearish', 0)} bearish across "
            f"{portfolio_rollup.get('scanned', 0)} tickers."
        ),
        full=(
            "The LLM thesis call did not complete. Refresh to retry, or "
            "review the deterministic per-sleeve readouts directly."
        ),
        bias="neutral",
        top_long=None,
        top_short=None,
    )


def _default_sleeve(sleeve_name: str) -> ThesisOutput:
    return ThesisOutput(
        condensed=f"LLM synthesis for {sleeve_name} unavailable.",
        full="Refresh to retry the synthesis call.",
        bias="neutral",
        top_long=None,
        top_short=None,
    )


# ─── Public entry points ───────────────────────────────────────────────────


def synthesize_portfolio_thesis(
    *,
    scan_date: str,
    portfolio_rollup: dict[str, Any],
    per_sleeve: list[dict[str, Any]],
    high_conviction: list[dict[str, Any]],
) -> dict[str, Any]:
    sig_payload = {
        "portfolio_rollup": portfolio_rollup,
        "per_sleeve": per_sleeve,
        "high_conviction": high_conviction,
    }
    key = _CacheKey(
        scope="portfolio",
        scan_date=scan_date,
        signature=_signature([sig_payload]),
    )
    cached = _cache_get(key)
    if cached is not None:
        return cached

    prompt = _build_portfolio_prompt(
        scan_date, portfolio_rollup, per_sleeve, high_conviction
    )
    output = call_llm(
        prompt=prompt,
        pydantic_model=ThesisOutput,
        agent_name="portfolio_thesis",
        state=state_for_selected_model(),
        default_factory=lambda: _default_portfolio(portfolio_rollup),
    )

    payload = {
        **output.model_dump(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "portfolio",
        "scan_date": scan_date,
    }
    _cache_put(key, payload)
    return payload


def synthesize_sleeve_thesis(
    *,
    sleeve_name: str,
    scan_date: str,
    sleeve_meta: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    sig_payload = {"sleeve_meta": sleeve_meta, "rows": rows}
    key = _CacheKey(
        scope=f"sleeve:{sleeve_name}",
        scan_date=scan_date,
        signature=_signature([sig_payload]),
    )
    cached = _cache_get(key)
    if cached is not None:
        return cached

    prompt = _build_sleeve_prompt(sleeve_name, scan_date, sleeve_meta, rows)
    output = call_llm(
        prompt=prompt,
        pydantic_model=ThesisOutput,
        agent_name=f"sleeve_thesis:{sleeve_name}",
        state=state_for_selected_model(),
        default_factory=lambda: _default_sleeve(sleeve_name),
    )
    payload = {
        **output.model_dump(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": f"sleeve:{sleeve_name}",
        "scan_date": scan_date,
    }
    _cache_put(key, payload)
    return payload


def clear_cache() -> int:
    """Drop all cached thesis payloads. Returns the count cleared."""
    with _cache_lock:
        n = len(_cache)
        _cache.clear()
        return n


# Re-exports for tests / introspection.
__all__ = [
    "ThesisOutput",
    "synthesize_portfolio_thesis",
    "synthesize_sleeve_thesis",
    "clear_cache",
]
