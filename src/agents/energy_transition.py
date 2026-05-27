"""Energy Transition — sector specialist for IRA-era US clean energy.

Thinks like a senior energy transition PE analyst. Two regulatory regimes
drive most of the alpha/risk in this space and the prompt makes the agent
explicitly score against both:

* **IRA tax credit stack** — 45X advanced manufacturing, 48E/ITC investment
  tax credit, Section 6418 transferability, domestic content adder rules.
  Update the IRA_RULE_NOTES dict below as Treasury notices land.

* **FEOC compliance** — Notice 2026-15 governs Prohibited Foreign Entity
  (PFE) exposure, Material Assistance Cost Ratio (MACR), interim safe
  harbor, and the 10-year recapture window on 48E credits. The agent does
  not auto-detect FEOC status (that would require supplier-graph data we
  don't have); instead it asks the LLM to surface red/amber flags from
  available public disclosures and news flow.

These notices change. Inline comments mark the spots to update.
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
    search_line_items,
)
from src.utils.api_key import get_api_key_from_state
from src.utils.llm import call_llm
from src.utils.progress import progress


# ─── Regulatory knowledge baked into the prompt ──────────────────────────────
# IMPORTANT: update these when Treasury issues new notices. Each entry is a
# short canonical sentence the LLM gets in its system prompt — keep them
# under one line each so the prompt stays cache-friendly.

IRA_RULE_NOTES: dict[str, str] = {
    # 45X — Advanced Manufacturing Production Credit (per-unit credit for US-made
    # cells, modules, inverters, batteries, critical minerals).
    "45X": "45X is a per-unit production credit on US-made cells/modules/inverters/batteries/critical minerals.",
    # 48E — Clean Electricity Investment Tax Credit (post-2025 replacement for 48).
    "48E": "48E gives a 30% investment tax credit on qualifying clean-electricity property placed in service after 2024.",
    # Section 6418 — transferability of credits to unrelated taxpayers.
    "6418": "Section 6418 lets eligible credits be sold for cash to unrelated taxpayers (one transfer only).",
    # Domestic content adder rules (Notice 2025-8 update):
    # - 38% non-PFE threshold for safe harbor under cost-method
    # - 51.6% domestic wafer rule for solar manufactured products
    "domestic_content_adder": (
        "Domestic content adder (Notice 2025-8): safe harbor at 38% non-PFE cost share; "
        "solar wafer rule sets 51.6% domestic threshold."
    ),
    # OBBBA — One Big Beautiful Bill Act mechanics (tightened phaseouts, FEOC).
    "OBBBA": "OBBBA tightened phaseouts and brought FEOC restrictions into force from 2026.",
}

FEOC_RULE_NOTES: dict[str, str] = {
    # Notice 2026-15 — PFE / MACR / safe harbor / recapture.
    "PFE": "Prohibited Foreign Entity (PFE) covers China, Russia, Iran, North Korea entities at >25% ownership/influence.",
    "MACR": "Material Assistance Cost Ratio (MACR) caps PFE content in the bill of materials; computed per Notice 2026-15.",
    "safe_harbor": "Interim safe harbor in Notice 2026-15 grandfathers projects begun before 2026-01-01 under prior rules.",
    "recapture_48E": "48E credits have a 10-year recapture window if FEOC content rises above MACR threshold post-PIS.",
}


# ─── Output schema ───────────────────────────────────────────────────────────


# Canonical sub-sector vocabulary the prompt encourages, but the LLM is
# allowed to invent new categories (e.g. 'nuclear', 'solar_inverter',
# 'green_hydrogen') without failing schema validation. Earlier runs showed
# the model legitimately wanted those values and the strict Literal made the
# whole agent default to neutral. We keep the canonical list documented and
# in the prompt; validation just accepts any non-empty string.
CANONICAL_SUB_SECTORS: tuple[str, ...] = (
    "solar_manufacturing",
    "solar_developer",
    "residential_storage",
    "ci_storage",
    "ev_charging",
    "grid_infrastructure",
    "utility_independent_power",
    "nuclear",
    "green_hydrogen",
    "vpp",
    "other",
)

CreditStack = Literal["high", "medium", "low", "none", "unknown"]
FEOCRisk = Literal["clean", "amber", "red", "unknown"]

PositionType = Literal[
    "long_equity",
    "short_equity",
    "long_puts",
    "pair_trade",
    "no_position",
]


class EnergyTransitionSignal(BaseModel):
    signal: Literal["bullish", "bearish", "neutral"]
    confidence: float = Field(..., ge=0, le=100)

    sub_sector: str = Field(
        ...,
        description=(
            "Sub-sector classification. Prefer one of CANONICAL_SUB_SECTORS "
            "but any short snake_case label is accepted."
        ),
    )
    ira_credit_stack: CreditStack = Field(
        ...,
        description="Strength of the company's IRA tax credit exposure (45X/48E/adders).",
    )
    feoc_risk: FEOCRisk = Field(
        ...,
        description="Flag for Prohibited Foreign Entity exposure under Notice 2026-15.",
    )

    variant_perception: str
    position_type: PositionType
    pair_with: str | None = None
    conviction: Literal["high", "medium", "low", "none"]

    unit_economics_note: str = Field(
        ..., description="One sentence on $/kWh, LCOE, or contracted-vs-merchant mix."
    )
    reasoning: str


# ─── Agent entry point ──────────────────────────────────────────────────────


def energy_transition_agent(state: AgentState, agent_id: str = "energy_transition_agent"):
    """Score US energy-transition tickers against IRA + FEOC frameworks."""
    api_key = get_api_key_from_state(state, "FINANCIAL_DATASETS_API_KEY")
    data = state["data"]
    end_date: str = data["end_date"]
    tickers: list[str] = data["tickers"]

    news_start = (datetime.fromisoformat(end_date) - timedelta(days=180)).date().isoformat()

    analysis_data: dict[str, dict] = {}
    et_analysis: dict[str, dict] = {}

    for ticker in tickers:
        progress.update_status(agent_id, ticker, "Fetching financial metrics")
        metrics = get_financial_metrics(ticker, end_date, period="ttm", limit=4, api_key=api_key)

        progress.update_status(agent_id, ticker, "Fetching line items")
        line_items = search_line_items(
            ticker,
            [
                "revenue",
                "gross_profit",
                "operating_income",
                "operating_cash_flow",
                "free_cash_flow",
                "capital_expenditure",
                "long_term_debt",
                "total_equity",
            ],
            end_date,
            limit=4,
            api_key=api_key,
        )

        progress.update_status(agent_id, ticker, "Fetching IRA/FEOC news")
        news = get_company_news(ticker, end_date=end_date, start_date=news_start, limit=250)

        progress.update_status(agent_id, ticker, "Fetching market cap")
        market_cap = get_market_cap(ticker, end_date, api_key=api_key)

        # ── Sub-analyses ───────────────────────────────────────────────────
        progress.update_status(agent_id, ticker, "Analyzing capex intensity")
        capex_signal = _analyze_capex_intensity(line_items)

        progress.update_status(agent_id, ticker, "Analyzing leverage")
        leverage_signal = _analyze_leverage(metrics, line_items)

        progress.update_status(agent_id, ticker, "Scanning IRA/FEOC headline flow")
        regulatory_flow = _scan_regulatory_headlines(news)

        analysis_data[ticker] = {
            "ticker": ticker,
            "market_cap": market_cap,
            "capex_intensity": capex_signal,
            "leverage": leverage_signal,
            "regulatory_headline_flow": regulatory_flow,
            "latest_metrics": [m.model_dump() for m in metrics[:1]],
            "recent_news_headlines": [n.title for n in news[:25]],
        }

        progress.update_status(agent_id, ticker, "Generating thesis")
        output = _generate_energy_output(
            ticker=ticker,
            analysis_data=analysis_data,
            state=state,
            agent_id=agent_id,
        )
        et_analysis[ticker] = output.model_dump()
        progress.update_status(agent_id, ticker, "Done", analysis=output.reasoning)

    message = HumanMessage(content=json.dumps(et_analysis), name=agent_id)
    if state["metadata"].get("show_reasoning"):
        show_agent_reasoning(et_analysis, "Energy Transition Agent")
    state["data"]["analyst_signals"][agent_id] = et_analysis
    progress.update_status(agent_id, None, "Done")
    return {"messages": [message], "data": state["data"]}


# ─── Sub-analyses ────────────────────────────────────────────────────────────


def _analyze_capex_intensity(line_items) -> dict:
    """Energy-transition names are capex-heavy; flag funding gaps."""
    if not line_items:
        return {"details": "No line items", "capex_to_revenue": None, "fcf_status": "unknown"}

    latest = line_items[0]
    rev = getattr(latest, "revenue", None)
    capex = getattr(latest, "capital_expenditure", None)
    ocf = getattr(latest, "operating_cash_flow", None)
    fcf = getattr(latest, "free_cash_flow", None)

    details: list[str] = []
    capex_to_rev = None
    if rev and capex is not None:
        capex_to_rev = abs(capex) / rev if rev else None
        if capex_to_rev is not None:
            details.append(f"capex/revenue {capex_to_rev:.1%}")

    fcf_status = "unknown"
    if fcf is not None:
        fcf_status = "fcf_positive" if fcf > 0 else "fcf_negative"
        details.append(f"FCF {fcf:+,.0f}")
    elif ocf is not None and capex is not None:
        implied = ocf + capex
        fcf_status = "fcf_positive" if implied > 0 else "fcf_negative"
        details.append(f"OCF+capex {implied:+,.0f}")

    return {
        "details": "; ".join(details) if details else "Capex data missing",
        "capex_to_revenue": capex_to_rev,
        "fcf_status": fcf_status,
    }


def _analyze_leverage(metrics, line_items) -> dict:
    """Debt + interest coverage — project-finance-style companies blow up here."""
    if not metrics and not line_items:
        return {"details": "No leverage data", "debt_to_equity": None}

    debt_to_equity = None
    if metrics:
        debt_to_equity = metrics[0].debt_to_equity

    details: list[str] = []
    if debt_to_equity is not None:
        if debt_to_equity > 2.0:
            details.append(f"high D/E {debt_to_equity:.2f}")
        elif debt_to_equity > 1.0:
            details.append(f"moderate D/E {debt_to_equity:.2f}")
        else:
            details.append(f"low D/E {debt_to_equity:.2f}")

    if line_items:
        ltd = getattr(line_items[0], "long_term_debt", None)
        equity = getattr(line_items[0], "total_equity", None)
        if ltd is not None:
            details.append(f"LTD {ltd:,.0f}")
        if equity is not None:
            details.append(f"equity {equity:,.0f}")

    return {
        "details": "; ".join(details) if details else "Leverage data unavailable",
        "debt_to_equity": debt_to_equity,
    }


# Keywords we surface to the LLM so it can spot regulatory tailwinds/risks.
# Lowercase substring match. Order matters only for documentation.
_IRA_KEYWORDS = {
    "ira", "tax credit", "45x", "48e", "itc", "ptc", "domestic content",
    "transferability", "section 6418", "treasury", "obbba",
}
_FEOC_KEYWORDS = {
    "feoc", "pfe", "prohibited foreign entity", "macr", "notice 2026-15",
    "recapture", "china", "uyghur", "supply chain restriction",
}


def _scan_regulatory_headlines(news) -> dict:
    """Bucket headlines by IRA-relevant vs FEOC-relevant keywords."""
    if not news:
        return {"details": "No news in window", "ira_hits": 0, "feoc_hits": 0, "examples": []}

    ira_hits: list[str] = []
    feoc_hits: list[str] = []
    for n in news:
        title = (n.title or "").lower()
        if any(k in title for k in _IRA_KEYWORDS):
            ira_hits.append(n.title)
        if any(k in title for k in _FEOC_KEYWORDS):
            feoc_hits.append(n.title)

    return {
        "details": f"{len(ira_hits)} IRA-related, {len(feoc_hits)} FEOC-related headlines",
        "ira_hits": len(ira_hits),
        "feoc_hits": len(feoc_hits),
        "examples": (ira_hits + feoc_hits)[:8],
    }


# ─── LLM generation ─────────────────────────────────────────────────────────


def _format_rule_notes(rules: dict[str, str]) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in rules.items())


def _generate_energy_output(
    ticker: str,
    analysis_data: dict,
    state: AgentState,
    agent_id: str,
) -> EnergyTransitionSignal:
    template = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """You are a senior energy-transition PE analyst covering US clean
                energy. Your edge is regulatory: you score companies against the
                IRA tax credit stack and the FEOC compliance regime, then form a
                directional view.

                # IRA tax credit stack (update as Treasury issues notices)
                {ira_rules}

                # FEOC compliance (Notice 2026-15 and successors)
                {feoc_rules}

                # Framework
                1. Sub-sector — pick the closest from the enum.
                2. IRA credit stack — score the company's exposure to 45X / 48E /
                   adders. "high" = multiple stackable credits and meets domestic
                   content threshold; "low" = qualifies but small revenue share;
                   "none" = no material credit exposure.
                3. FEOC risk — "clean" = no material China/Russia/Iran/NK
                   supplier exposure in public disclosures; "amber" = some
                   exposure or pending MACR concerns; "red" = explicit PFE
                   exposure or recapture risk on 48E credits.
                4. Unit economics — one sentence on $/kWh, LCOE, or
                   contracted-vs-merchant revenue mix.
                5. Variant perception — "Consensus is wrong because [X]". If
                   you cannot find one, you may set signal=neutral.
                6. Signal + position type + conviction.

                Be specific. Cite numbers from the analysis data and from the
                regulatory headline flow when available. If you do not have
                evidence on FEOC status, mark it "unknown" — do not guess.
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
                  "sub_sector": "<one of the enum>",
                  "ira_credit_stack": "high" | "medium" | "low" | "none" | "unknown",
                  "feoc_risk": "clean" | "amber" | "red" | "unknown",
                  "variant_perception": "Consensus is wrong because ...",
                  "position_type": "long_equity" | "short_equity" | "long_puts" | "pair_trade" | "no_position",
                  "pair_with": "<ticker or null>",
                  "conviction": "high" | "medium" | "low" | "none",
                  "unit_economics_note": "<one sentence>",
                  "reasoning": "<brief, evidence-cited>"
                }}
                """,
            ),
        ]
    )

    prompt = template.invoke(
        {
            "ticker": ticker,
            "analysis_data": json.dumps(analysis_data, indent=2, default=str),
            "ira_rules": _format_rule_notes(IRA_RULE_NOTES),
            "feoc_rules": _format_rule_notes(FEOC_RULE_NOTES),
        }
    )

    def _default() -> EnergyTransitionSignal:
        return EnergyTransitionSignal(
            signal="neutral",
            confidence=0.0,
            sub_sector="other",
            ira_credit_stack="unknown",
            feoc_risk="unknown",
            variant_perception="LLM parse error",
            position_type="no_position",
            pair_with=None,
            conviction="none",
            unit_economics_note="n/a",
            reasoning="LLM output failed to parse; defaulted to neutral.",
        )

    return call_llm(
        prompt=prompt,
        pydantic_model=EnergyTransitionSignal,
        agent_name=agent_id,
        state=state,
        default_factory=_default,
    )
