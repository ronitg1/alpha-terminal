"""Emerging Tech — crossover-fund analyst covering AI infra, semis, robotics,
quantum, defense tech, biotech/longevity, and fintech disruption.

The defining lens is *S-curve positioning* (where on the adoption curve is
the company?) combined with mandatory *AI exposure* classification. Every
ticker in this universe gets an AI tailwind rating, even biotech, because
that is the single largest macro narrative driving 2025-2026 capital flows.
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


TechCategory = Literal[
    "ai_infra",
    "semis",
    "robotics",
    "quantum",
    "defense_tech",
    "biotech_longevity",
    "fintech",
    "other",
]

MoatType = Literal["ip", "network_effects", "switching_costs", "scale", "talent", "regulatory", "none"]
MoatDurability = Literal["durable", "eroding", "commoditizing", "unknown"]
SCurvePosition = Literal["early", "growth", "inflecting", "maturing", "declining", "unknown"]
AIExposure = Literal["direct", "enabling", "application", "none"]
AITailwind = Literal["strong", "moderate", "minimal", "headwind"]
ValuationAssessment = Literal["too_optimistic", "fair", "too_pessimistic", "unknown"]
PositionType = Literal[
    "long_equity",
    "short_equity",
    "long_calls",
    "long_puts",
    "spread",
    "pair_trade",
    "no_position",
]
HoldPeriod = Literal["lt_30d", "30_90d", "90_365d", "gt_1yr", "n_a"]


class EmergingTechSignal(BaseModel):
    signal: Literal["bullish", "bearish", "neutral"]
    confidence: float = Field(..., ge=0, le=100)

    tech_category: TechCategory
    moat_type: MoatType
    moat_durability: MoatDurability
    s_curve_position: SCurvePosition
    ai_exposure: AIExposure
    ai_tailwind: AITailwind
    valuation_assessment: ValuationAssessment

    variant_perception: str
    position_type: PositionType
    pair_with: str | None = None
    conviction: Literal["high", "medium", "low", "none"]
    hold_period: HoldPeriod

    competitors_note: str = Field(..., description="Named competitors + relative positioning, one sentence.")
    reasoning: str


# ─── Agent entry point ──────────────────────────────────────────────────────


def emerging_tech_agent(state: AgentState, agent_id: str = "emerging_tech_agent"):
    """Score emerging-tech tickers on moat, S-curve, AI exposure, valuation."""
    api_key = get_api_key_from_state(state, "FINANCIAL_DATASETS_API_KEY")
    data = state["data"]
    end_date: str = data["end_date"]
    tickers: list[str] = data["tickers"]

    window_start = (datetime.fromisoformat(end_date) - timedelta(days=270)).date().isoformat()

    analysis_data: dict[str, dict] = {}
    et_analysis: dict[str, dict] = {}

    for ticker in tickers:
        progress.update_status(agent_id, ticker, "Fetching financial metrics")
        metrics = get_financial_metrics(ticker, end_date, period="ttm", limit=6, api_key=api_key)

        progress.update_status(agent_id, ticker, "Fetching line items")
        line_items = search_line_items(
            ticker,
            [
                "revenue",
                "gross_profit",
                "operating_income",
                "operating_cash_flow",
                "free_cash_flow",
                "research_and_development",
                "outstanding_shares",
            ],
            end_date,
            limit=6,
            api_key=api_key,
        )

        progress.update_status(agent_id, ticker, "Fetching prices")
        prices = get_prices(ticker, window_start, end_date, api_key=api_key)  # noqa: F841 -- fetched to warm the cache for downstream calls

        progress.update_status(agent_id, ticker, "Fetching news")
        news = get_company_news(ticker, end_date=end_date, start_date=window_start, limit=300)

        progress.update_status(agent_id, ticker, "Fetching market cap")
        market_cap = get_market_cap(ticker, end_date, api_key=api_key)

        # ── Sub-analyses ───────────────────────────────────────────────────
        progress.update_status(agent_id, ticker, "Analyzing growth profile")
        growth_signal = _analyze_growth(line_items, metrics)

        progress.update_status(agent_id, ticker, "Analyzing R&D intensity")
        rnd_signal = _analyze_rnd_intensity(line_items)

        progress.update_status(agent_id, ticker, "Analyzing rule-of-40")
        rule_of_40 = _analyze_rule_of_40(line_items)

        progress.update_status(agent_id, ticker, "Scanning AI-narrative headlines")
        ai_flow = _scan_ai_headlines(news)

        progress.update_status(agent_id, ticker, "Analyzing valuation context")
        valuation_signal = _analyze_valuation_context(metrics)

        analysis_data[ticker] = {
            "ticker": ticker,
            "market_cap": market_cap,
            "growth": growth_signal,
            "rnd_intensity": rnd_signal,
            "rule_of_40": rule_of_40,
            "ai_headline_flow": ai_flow,
            "valuation_context": valuation_signal,
            "recent_news_headlines": [n.title for n in news[:25]],
        }

        progress.update_status(agent_id, ticker, "Generating thesis")
        output = _generate_emerging_tech_output(
            ticker=ticker,
            analysis_data=analysis_data,
            state=state,
            agent_id=agent_id,
        )
        et_analysis[ticker] = output.model_dump()
        progress.update_status(agent_id, ticker, "Done", analysis=output.reasoning)

    message = HumanMessage(content=json.dumps(et_analysis), name=agent_id)
    if state["metadata"].get("show_reasoning"):
        show_agent_reasoning(et_analysis, "Emerging Tech Agent")
    state["data"]["analyst_signals"][agent_id] = et_analysis
    progress.update_status(agent_id, None, "Done")
    return {"messages": [message], "data": state["data"]}


# ─── Sub-analyses ────────────────────────────────────────────────────────────


def _analyze_growth(line_items, metrics) -> dict:
    """Sequential and YoY revenue growth."""
    if not line_items or len(line_items) < 2:
        return {"details": "Insufficient data for growth", "sequential": None, "yoy": None}

    rev_now = getattr(line_items[0], "revenue", None)
    rev_prev = getattr(line_items[1], "revenue", None)
    rev_yoy = getattr(line_items[4], "revenue", None) if len(line_items) >= 5 else None

    seq = None
    yoy = None
    details: list[str] = []

    if rev_now and rev_prev:
        seq = (rev_now - rev_prev) / rev_prev
        details.append(f"sequential rev {seq * 100:+.1f}%")
    if rev_now and rev_yoy:
        yoy = (rev_now - rev_yoy) / rev_yoy
        details.append(f"YoY rev {yoy * 100:+.1f}%")

    return {"details": "; ".join(details) if details else "n/a", "sequential": seq, "yoy": yoy}


def _analyze_rnd_intensity(line_items) -> dict:
    """R&D as % of revenue — high R&D is a moat tell for emerging tech."""
    if not line_items:
        return {"details": "No data", "rnd_to_revenue": None}

    latest = line_items[0]
    rev = getattr(latest, "revenue", None)
    rnd = getattr(latest, "research_and_development", None)
    if not rev or rnd is None:
        return {"details": "R&D or revenue missing", "rnd_to_revenue": None}

    ratio = rnd / rev
    band = "low"
    if ratio >= 0.25:
        band = "very_high"
    elif ratio >= 0.15:
        band = "high"
    elif ratio >= 0.08:
        band = "moderate"
    return {"details": f"R&D/revenue {ratio:.1%} ({band})", "rnd_to_revenue": ratio, "band": band}


def _analyze_rule_of_40(line_items) -> dict:
    """Rule of 40 = revenue growth % + FCF margin %. Software/AI infra benchmark."""
    if not line_items or len(line_items) < 2:
        return {"details": "Insufficient data", "rule_of_40": None}

    rev_now = getattr(line_items[0], "revenue", None)
    rev_prev = getattr(line_items[1], "revenue", None)
    fcf = getattr(line_items[0], "free_cash_flow", None)
    if not rev_now or not rev_prev:
        return {"details": "Revenue missing", "rule_of_40": None}

    growth_pct = (rev_now - rev_prev) / rev_prev * 100
    fcf_margin_pct = (fcf / rev_now * 100) if (fcf is not None and rev_now) else 0
    score = growth_pct + fcf_margin_pct
    return {
        "details": f"growth {growth_pct:.1f}% + FCF margin {fcf_margin_pct:.1f}% = {score:.1f}",
        "rule_of_40": score,
        "passes": score >= 40,
    }


# Keywords used to score the AI tailwind exposure from news flow.
_AI_KEYWORDS = {
    "ai", "artificial intelligence", "machine learning", "llm", "large language model",
    "generative", "transformer", "gpu", "tpu", "neural", "foundation model", "inference",
    "training cluster", "data center", "accelerator", "cuda",
}


def _scan_ai_headlines(news) -> dict:
    if not news:
        return {"details": "No news", "hits": 0, "examples": []}
    hits = [n.title for n in news if any(k in (n.title or "").lower() for k in _AI_KEYWORDS)]
    return {"details": f"{len(hits)} AI-tagged headlines", "hits": len(hits), "examples": hits[:10]}


def _analyze_valuation_context(metrics) -> dict:
    """Snapshot PE / EV-revenue. Emerging tech often has no PE — fall back to EV/Rev."""
    if not metrics:
        return {"details": "No metrics", "pe": None, "ev_to_revenue": None}

    m = metrics[0]
    pe = m.price_to_earnings_ratio
    ev_rev = m.enterprise_value_to_revenue_ratio
    details: list[str] = []
    if pe is not None:
        details.append(f"PE {pe:.1f}")
    if ev_rev is not None:
        details.append(f"EV/Rev {ev_rev:.1f}")
    return {
        "details": "; ".join(details) if details else "Valuation metrics unavailable",
        "pe": pe,
        "ev_to_revenue": ev_rev,
    }


# ─── LLM generation ─────────────────────────────────────────────────────────


def _generate_emerging_tech_output(
    ticker: str,
    analysis_data: dict,
    state: AgentState,
    agent_id: str,
) -> EmergingTechSignal:
    template = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """You are a crossover-fund analyst covering emerging tech: AI
                infrastructure, semiconductors, robotics, quantum, defense tech,
                biotech/longevity, fintech disruption. You bridge private and
                public markets — pattern-match on private-market comps, public
                trading multiples, and the AI capex super-cycle.

                # Framework — every field is required.
                1. Tech category — pick the closest enum value.
                2. Moat type + durability — IP / network effects / switching
                   costs / scale / talent / regulatory; durable / eroding /
                   commoditizing. If unsure on durability, say so.
                3. S-curve position — early (pre-rev or <$100M), growth
                   (accelerating), inflecting (rule-of-40 improving), maturing,
                   declining.
                4. AI/compute exposure — direct (builds infra), enabling
                   (picks-and-shovels), application (deploys AI), none. Also
                   rate the AI tailwind strength.
                5. Valuation assessment — given growth + moat durability, is
                   the multiple too optimistic / fair / too pessimistic?
                6. Variant perception — "Consensus is wrong because [X]".
                   If you don't have a contrarian thesis, you may still produce
                   a directional lean grounded in the scorecard (moat + S-curve
                   + AI tailwind + valuation) — set the variant_perception field
                   to a one-sentence summary of the lean instead of "n/a".
                   Reserve signal=neutral for genuinely conflicted setups.

                # CONFIDENCE CALIBRATION (anchor explicitly, do not default to 50)
                Score the confidence field by how many of these align:
                  • 70-90: strong directional read + ≥3 of (moat durability,
                    S-curve, AI tailwind, valuation) agreeing + variant perception
                  • 50-70: directional lean with 2-3 scorecard dimensions agreeing
                  • 30-50: directional lean with 1 strong dimension, others mixed
                  • 10-30: thin data or 2 dimensions disagreeing
                  • 0-10: insufficient evidence (signal=neutral, conviction=none)

                Cite specific numbers from the analysis data. Name competitors
                in the competitors_note field. Use "unknown" for a SPECIFIC field
                when evidence is truly absent (e.g. moat_durability for an opaque
                pre-revenue name), but do NOT default the whole signal to neutral
                just because one field is unknown — score what you can score.
                """,
            ),
            (
                "human",
                """Analyze {ticker}.

                Analysis data:
                {analysis_data}

                Return JSON exactly matching this schema:
                {{
                  "signal": "bullish" | "bearish" | "neutral",
                  "confidence": <0-100>,
                  "tech_category": "ai_infra" | "semis" | "robotics" | "quantum" | "defense_tech" | "biotech_longevity" | "fintech" | "other",
                  "moat_type": "ip" | "network_effects" | "switching_costs" | "scale" | "talent" | "regulatory" | "none",
                  "moat_durability": "durable" | "eroding" | "commoditizing" | "unknown",
                  "s_curve_position": "early" | "growth" | "inflecting" | "maturing" | "declining" | "unknown",
                  "ai_exposure": "direct" | "enabling" | "application" | "none",
                  "ai_tailwind": "strong" | "moderate" | "minimal" | "headwind",
                  "valuation_assessment": "too_optimistic" | "fair" | "too_pessimistic" | "unknown",
                  "variant_perception": "Consensus is wrong because ...",
                  "position_type": "long_equity" | "short_equity" | "long_calls" | "long_puts" | "spread" | "pair_trade" | "no_position",
                  "pair_with": "<ticker or null>",
                  "conviction": "high" | "medium" | "low" | "none",
                  "hold_period": "lt_30d" | "30_90d" | "90_365d" | "gt_1yr" | "n_a",
                  "competitors_note": "<named competitors + positioning>",
                  "reasoning": "<brief, evidence-cited>"
                }}
                """,
            ),
        ]
    )

    prompt = template.invoke({"ticker": ticker, "analysis_data": json.dumps(analysis_data, indent=2, default=str)})

    def _default() -> EmergingTechSignal:
        return EmergingTechSignal(
            signal="neutral",
            confidence=0.0,
            tech_category="other",
            moat_type="none",
            moat_durability="unknown",
            s_curve_position="unknown",
            ai_exposure="none",
            ai_tailwind="minimal",
            valuation_assessment="unknown",
            variant_perception="LLM parse error",
            position_type="no_position",
            pair_with=None,
            conviction="none",
            hold_period="n_a",
            competitors_note="n/a",
            reasoning="LLM output failed to parse; defaulted to neutral.",
        )

    return call_llm(
        prompt=prompt,
        pydantic_model=EmergingTechSignal,
        agent_name=agent_id,
        state=state,
        default_factory=_default,
    )
