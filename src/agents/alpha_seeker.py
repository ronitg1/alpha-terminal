"""Alpha Seeker — general-purpose, sector-agnostic alpha generation.

Designed to think like a multi-strategy pod at a quant-fundamental hybrid
fund. The defining requirement: **must produce a variant perception** or
explicitly abstain ("No edge — skip"). Pretending to have an edge is the
single worst failure mode of an alpha agent, so abstention is built into
the schema as a first-class outcome.

Framework (see project spec):
1. Variant perception
2. Catalyst identification (near + medium term)
3. Positioning signal
4. Risk flags / kill switch
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing_extensions import Literal

from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.graph.state import AgentState, show_agent_reasoning
from src.tools.api import (
    get_company_news,
    get_financial_metrics,
    get_market_cap,
    get_prices,
    search_line_items,
)
from src.utils.api_key import get_api_key_from_state
from src.utils.llm import call_llm
from src.utils.progress import progress


# ─── Output schema ───────────────────────────────────────────────────────────


PositionType = Literal[
    "long_equity",
    "short_equity",
    "long_calls",
    "long_puts",
    "spread",
    "pair_trade",
    "no_position",
]

Conviction = Literal["high", "medium", "low", "none"]

HoldPeriod = Literal["lt_30d", "30_90d", "90_365d", "gt_1yr", "n_a"]

CatalystType = Literal["binary", "continuous", "n_a"]


class AlphaSeekerSignal(BaseModel):
    """Final structured output from the alpha seeker agent."""

    signal: Literal["bullish", "bearish", "neutral"]
    confidence: float = Field(..., ge=0, le=100)

    variant_perception: str = Field(
        ...,
        description=(
            'One sentence: "Consensus is wrong because [X]". If no genuine '
            'variant perception exists, return "No edge — skip" verbatim.'
        ),
    )
    has_edge: bool = Field(..., description="False if variant perception is 'No edge — skip'.")

    catalyst_near_term: str = Field(..., description="0-90 day catalyst (specific, dated if possible).")
    catalyst_medium_term: str = Field(..., description="90-365 day re-rating event.")
    catalyst_type: CatalystType

    position_type: PositionType
    pair_with: str | None = Field(
        None, description="If position_type == pair_trade, the other leg's ticker."
    )
    conviction: Conviction
    hold_period: HoldPeriod

    kill_switch: str = Field(
        ..., description="One specific event/condition that invalidates the trade."
    )
    probability_wrong: Literal["low", "medium", "high"]

    reasoning: str


# ─── Agent entry point ──────────────────────────────────────────────────────


def alpha_seeker_agent(state: AgentState, agent_id: str = "alpha_seeker_agent"):
    """Analyze tickers through a multi-strategy alpha lens."""
    api_key = get_api_key_from_state(state, "FINANCIAL_DATASETS_API_KEY")
    data = state["data"]
    end_date: str = data["end_date"]
    tickers: list[str] = data["tickers"]

    # 90-day window for short-term momentum + sentiment context. Cheap enough
    # that we always pull it; agents downstream can ignore if irrelevant.
    short_window_start = (datetime.fromisoformat(end_date) - timedelta(days=90)).date().isoformat()
    long_window_start = (datetime.fromisoformat(end_date) - timedelta(days=365)).date().isoformat()

    analysis_data: dict[str, dict] = {}
    alpha_analysis: dict[str, dict] = {}

    for ticker in tickers:
        progress.update_status(agent_id, ticker, "Fetching prices (3M + 12M)")
        prices_3m = get_prices(ticker, short_window_start, end_date, api_key=api_key)
        prices_12m = get_prices(ticker, long_window_start, end_date, api_key=api_key)

        progress.update_status(agent_id, ticker, "Fetching financial metrics")
        metrics = get_financial_metrics(ticker, end_date, period="ttm", limit=4, api_key=api_key)

        progress.update_status(agent_id, ticker, "Fetching line items")
        line_items = search_line_items(
            ticker,
            [
                "revenue",
                "operating_income",
                "net_income",
                "free_cash_flow",
                "operating_cash_flow",
                "outstanding_shares",
            ],
            end_date,
            limit=4,
            api_key=api_key,
        )

        progress.update_status(agent_id, ticker, "Fetching news flow")
        news = get_company_news(ticker, end_date=end_date, start_date=short_window_start, limit=200)

        progress.update_status(agent_id, ticker, "Fetching market cap")
        market_cap = get_market_cap(ticker, end_date, api_key=api_key)

        # ── Sub-analyses ───────────────────────────────────────────────────
        progress.update_status(agent_id, ticker, "Analyzing momentum + dispersion")
        momentum = _analyze_momentum(prices_3m, prices_12m)

        progress.update_status(agent_id, ticker, "Analyzing fundamental inflection")
        inflection = _analyze_fundamental_inflection(metrics, line_items)

        progress.update_status(agent_id, ticker, "Analyzing news velocity")
        news_signal = _analyze_news_velocity(news)

        analysis_data[ticker] = {
            "ticker": ticker,
            "market_cap": market_cap,
            "momentum": momentum,
            "fundamental_inflection": inflection,
            "news_velocity": news_signal,
            "latest_metrics": _serialize_metrics(metrics),
            "recent_news_headlines": [n.title for n in news[:15]],
        }

        progress.update_status(agent_id, ticker, "Generating alpha thesis")
        output = _generate_alpha_output(
            ticker=ticker,
            analysis_data=analysis_data,
            state=state,
            agent_id=agent_id,
        )

        alpha_analysis[ticker] = output.model_dump()
        progress.update_status(agent_id, ticker, "Done", analysis=output.reasoning)

    message = HumanMessage(content=json.dumps(alpha_analysis), name=agent_id)
    if state["metadata"].get("show_reasoning"):
        show_agent_reasoning(alpha_analysis, "Alpha Seeker Agent")
    state["data"]["analyst_signals"][agent_id] = alpha_analysis
    progress.update_status(agent_id, None, "Done")
    return {"messages": [message], "data": state["data"]}


# ─── Sub-analyses ────────────────────────────────────────────────────────────


def _analyze_momentum(prices_3m, prices_12m) -> dict:
    """Return short-term and long-term return + volatility regime."""
    details: list[str] = []
    out: dict[str, object] = {}

    def _pct_change(series) -> float | None:
        if not series or len(series) < 2:
            return None
        first, last = series[0].close, series[-1].close
        if first == 0:
            return None
        return (last - first) / first

    ret_3m = _pct_change(prices_3m)
    ret_12m = _pct_change(prices_12m)
    out["return_3m"] = ret_3m
    out["return_12m"] = ret_12m

    if ret_3m is not None:
        details.append(f"3M return {ret_3m * 100:+.1f}%")
    if ret_12m is not None:
        details.append(f"12M return {ret_12m * 100:+.1f}%")

    # Crude regime tag — strong move in either direction is information.
    if ret_3m is not None:
        if ret_3m > 0.20:
            out["regime"] = "strong_uptrend"
        elif ret_3m < -0.20:
            out["regime"] = "strong_downtrend"
        else:
            out["regime"] = "range"
    else:
        out["regime"] = "unknown"

    out["details"] = "; ".join(details) if details else "Price data unavailable"
    return out


def _analyze_fundamental_inflection(metrics, line_items) -> dict:
    """Look for revenue / FCF / margin inflection across the last few periods."""
    if not metrics or not line_items:
        return {"details": "Insufficient data", "inflection": "unknown"}

    details: list[str] = []
    latest = metrics[0]

    # Margin trajectory: compare latest vs trailing average.
    margin_now = latest.operating_margin
    margin_history = [m.operating_margin for m in metrics[1:] if m.operating_margin is not None]
    margin_avg = sum(margin_history) / len(margin_history) if margin_history else None

    inflection = "none"
    if margin_now is not None and margin_avg is not None:
        delta = margin_now - margin_avg
        if delta >= 0.03:
            inflection = "improving_margins"
            details.append(f"Op margin {margin_now:.1%} vs trailing {margin_avg:.1%} (Δ +{delta * 100:.1f}pp)")
        elif delta <= -0.03:
            inflection = "deteriorating_margins"
            details.append(f"Op margin {margin_now:.1%} vs trailing {margin_avg:.1%} (Δ {delta * 100:+.1f}pp)")
        else:
            details.append(f"Op margin steady at {margin_now:.1%}")

    # Revenue trajectory: latest two line-item rows.
    if len(line_items) >= 2:
        rev_now = getattr(line_items[0], "revenue", None)
        rev_prev = getattr(line_items[1], "revenue", None)
        if rev_now is not None and rev_prev not in (None, 0):
            growth = (rev_now - rev_prev) / rev_prev
            details.append(f"Sequential revenue {growth * 100:+.1f}%")
            if growth > 0.05 and inflection == "none":
                inflection = "revenue_acceleration"
            elif growth < -0.05 and inflection == "none":
                inflection = "revenue_decline"

    return {"details": "; ".join(details) if details else "No inflection signal", "inflection": inflection}


def _analyze_news_velocity(news) -> dict:
    """Count headlines and crude pos/neg dispersion (sentiment is best-effort)."""
    if not news:
        return {"details": "No recent news", "headline_count": 0, "sentiment_mix": "n_a"}

    pos = sum(1 for n in news if (n.sentiment or "").lower() in {"positive", "bullish"})
    neg = sum(1 for n in news if (n.sentiment or "").lower() in {"negative", "bearish"})
    total = len(news)
    mix = "balanced"
    if pos and not neg:
        mix = "uniformly_positive"
    elif neg and not pos:
        mix = "uniformly_negative"
    elif pos and neg:
        mix = "divergent"
    return {
        "details": f"{total} headlines (pos={pos}, neg={neg})",
        "headline_count": total,
        "sentiment_mix": mix,
    }


def _serialize_metrics(metrics) -> list[dict]:
    """Convert metric rows to dicts for the LLM prompt."""
    return [m.model_dump() for m in metrics[:2]] if metrics else []


# ─── LLM generation ─────────────────────────────────────────────────────────


def _generate_alpha_output(
    ticker: str,
    analysis_data: dict,
    state: AgentState,
    agent_id: str,
) -> AlphaSeekerSignal:
    template = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """You are the Alpha Seeker — a multi-strategy alpha analyst at a
                quant-fundamental hybrid hedge fund. You produce trade ideas across
                any sector, but your bar is high: if you cannot articulate a
                **variant perception** (a specific way the market is wrong), you
                explicitly skip the name.

                Your discipline:
                1. VARIANT PERCEPTION — write one sentence: "Consensus is wrong
                   because [X]". Sources of edge to look for: estimate revision
                   inflection, narrative/reality divergence, catalyst misunderstood
                   by the market, hidden optionality not in consensus models. If
                   you cannot find one, set variant_perception to "No edge — skip"
                   and has_edge=false; everything downstream becomes n_a.

                2. CATALYSTS — name a specific near-term (0-90 day) and medium-term
                   (90-365 day) catalyst. Be concrete: "Q4 earnings 2026-02-12"
                   beats "next earnings". Tag catalyst_type as binary (event-driven)
                   or continuous (fundamental grind).

                3. POSITIONING — pick the most efficient expression: long/short
                   equity, options (calls/puts), spread, or pair trade. If pair,
                   name the other leg in pair_with. Conviction (high/medium/low)
                   and hold_period must be consistent with the catalyst horizon.

                4. KILL SWITCH — one sentence describing the specific event or
                   metric move that invalidates the trade. Vague kill switches
                   ("if fundamentals deteriorate") are not acceptable.

                Be terse. Cite numbers from the analysis data. Do not pad.

                If has_edge=false, set: signal=neutral, confidence<=20,
                position_type=no_position, hold_period=n_a, catalyst_type=n_a,
                conviction=none, catalyst fields="n/a — no edge".
                """,
            ),
            (
                "human",
                """Analyze {ticker} and produce a structured alpha signal.

                Analysis data:
                {analysis_data}

                Return JSON matching this schema exactly:
                {{
                  "signal": "bullish" | "bearish" | "neutral",
                  "confidence": <0-100>,
                  "variant_perception": "Consensus is wrong because ..." or "No edge — skip",
                  "has_edge": <true|false>,
                  "catalyst_near_term": "<specific catalyst, 0-90d>",
                  "catalyst_medium_term": "<specific catalyst, 90-365d>",
                  "catalyst_type": "binary" | "continuous" | "n_a",
                  "position_type": "long_equity" | "short_equity" | "long_calls" | "long_puts" | "spread" | "pair_trade" | "no_position",
                  "pair_with": "<ticker or null>",
                  "conviction": "high" | "medium" | "low" | "none",
                  "hold_period": "lt_30d" | "30_90d" | "90_365d" | "gt_1yr" | "n_a",
                  "kill_switch": "<one sentence>",
                  "probability_wrong": "low" | "medium" | "high",
                  "reasoning": "<brief, data-cited rationale>"
                }}
                """,
            ),
        ]
    )

    prompt = template.invoke({"ticker": ticker, "analysis_data": json.dumps(analysis_data, indent=2, default=str)})

    def _default() -> AlphaSeekerSignal:
        return AlphaSeekerSignal(
            signal="neutral",
            confidence=0.0,
            variant_perception="No edge — skip",
            has_edge=False,
            catalyst_near_term="n/a — parse error",
            catalyst_medium_term="n/a — parse error",
            catalyst_type="n_a",
            position_type="no_position",
            pair_with=None,
            conviction="none",
            hold_period="n_a",
            kill_switch="n/a — parse error",
            probability_wrong="high",
            reasoning="LLM output failed to parse; defaulted to neutral.",
        )

    return call_llm(
        prompt=prompt,
        pydantic_model=AlphaSeekerSignal,
        agent_name=agent_id,
        state=state,
        default_factory=_default,
    )
