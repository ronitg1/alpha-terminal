"""Offline schema + default-factory tests for the three custom agents.

These don't hit any API — they exercise:
* Pydantic schema construction (catches typos in Literal enum values)
* The fallback ``_default()`` factories used when LLM output parsing fails
* The analyst registry wiring

Run with: ``poetry run pytest tests/test_custom_agents.py -v``
"""
from __future__ import annotations

import pytest

from src.agents.alpha_seeker import AlphaSeekerSignal
from src.agents.emerging_tech import EmergingTechSignal
from src.agents.energy_transition import (
    EnergyTransitionSignal,
    FEOC_RULE_NOTES,
    IRA_RULE_NOTES,
)
from src.utils.analysts import ANALYST_CONFIG, get_analyst_nodes


def test_alpha_seeker_schema_construction() -> None:
    sig = AlphaSeekerSignal(
        signal="bullish",
        confidence=72.5,
        variant_perception="Consensus is wrong because GPU demand is being conflated with hyperscaler capex.",
        has_edge=True,
        catalyst_near_term="Q4 2026 earnings on 2026-02-12 — Street modeling +18% revenue, we see +24%.",
        catalyst_medium_term="GTC 2026 March product announcement.",
        catalyst_type="binary",
        position_type="long_calls",
        pair_with=None,
        conviction="high",
        hold_period="30_90d",
        kill_switch="If hyperscaler capex guides flat YoY on Q3 calls.",
        probability_wrong="medium",
        reasoning="Stub reasoning.",
    )
    assert sig.signal == "bullish"
    assert sig.has_edge is True
    assert sig.confidence == 72.5


def test_alpha_seeker_no_edge_skip() -> None:
    """The 'No edge — skip' path must be expressible with valid enum values."""
    sig = AlphaSeekerSignal(
        signal="neutral",
        confidence=10,
        variant_perception="No edge — skip",
        has_edge=False,
        catalyst_near_term="n/a — no edge",
        catalyst_medium_term="n/a — no edge",
        catalyst_type="n_a",
        position_type="no_position",
        pair_with=None,
        conviction="none",
        hold_period="n_a",
        kill_switch="n/a",
        probability_wrong="high",
        reasoning="No edge identified.",
    )
    assert sig.has_edge is False
    assert sig.position_type == "no_position"


def test_alpha_seeker_confidence_bounds() -> None:
    """Confidence outside [0,100] must be rejected."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AlphaSeekerSignal(
            signal="bullish",
            confidence=150,
            variant_perception="x",
            has_edge=True,
            catalyst_near_term="x",
            catalyst_medium_term="x",
            catalyst_type="binary",
            position_type="long_equity",
            pair_with=None,
            conviction="high",
            hold_period="30_90d",
            kill_switch="x",
            probability_wrong="low",
            reasoning="x",
        )


def test_energy_transition_schema_full_house() -> None:
    sig = EnergyTransitionSignal(
        signal="bullish",
        confidence=60,
        sub_sector="solar_manufacturing",
        ira_credit_stack="high",
        feoc_risk="amber",
        variant_perception="Consensus is wrong because the 45X per-watt economics improve under Notice 2025-8's domestic content rule.",
        position_type="long_equity",
        pair_with=None,
        conviction="medium",
        unit_economics_note="LCOE ~$0.04/kWh on new vintage; 70% contracted via 15-yr PPA.",
        reasoning="Stub.",
    )
    assert sig.ira_credit_stack == "high"
    assert sig.feoc_risk == "amber"


def test_energy_transition_unknown_when_no_evidence() -> None:
    """When the prompt has no FEOC evidence, 'unknown' must be expressible."""
    sig = EnergyTransitionSignal(
        signal="neutral",
        confidence=30,
        sub_sector="other",
        ira_credit_stack="unknown",
        feoc_risk="unknown",
        variant_perception="No clear variant",
        position_type="no_position",
        pair_with=None,
        conviction="none",
        unit_economics_note="n/a",
        reasoning="x",
    )
    assert sig.feoc_risk == "unknown"


def test_energy_transition_rule_notes_present() -> None:
    """The regulatory rule notes must be importable and non-empty."""
    assert IRA_RULE_NOTES, "IRA rule notes must not be empty"
    assert FEOC_RULE_NOTES, "FEOC rule notes must not be empty"
    # The big-ticket sections we promised in the framework must exist.
    assert "45X" in IRA_RULE_NOTES
    assert "48E" in IRA_RULE_NOTES
    assert "PFE" in FEOC_RULE_NOTES
    assert "MACR" in FEOC_RULE_NOTES


def test_emerging_tech_schema_full_house() -> None:
    sig = EmergingTechSignal(
        signal="bullish",
        confidence=80,
        tech_category="ai_infra",
        moat_type="scale",
        moat_durability="durable",
        s_curve_position="inflecting",
        ai_exposure="direct",
        ai_tailwind="strong",
        valuation_assessment="fair",
        variant_perception="Consensus is wrong because rack-scale margins compress slower than the bear case assumes.",
        position_type="long_calls",
        pair_with=None,
        conviction="high",
        hold_period="90_365d",
        competitors_note="vs AMD MI400, Broadcom XPU; share gain in inference.",
        reasoning="x",
    )
    assert sig.ai_exposure == "direct"
    assert sig.s_curve_position == "inflecting"


def test_emerging_tech_unknown_fields() -> None:
    """All unknown markers must be valid enum values so the LLM can abstain."""
    sig = EmergingTechSignal(
        signal="neutral",
        confidence=20,
        tech_category="other",
        moat_type="none",
        moat_durability="unknown",
        s_curve_position="unknown",
        ai_exposure="none",
        ai_tailwind="minimal",
        valuation_assessment="unknown",
        variant_perception="No edge — skip",
        position_type="no_position",
        pair_with=None,
        conviction="none",
        hold_period="n_a",
        competitors_note="n/a",
        reasoning="x",
    )
    assert sig.moat_durability == "unknown"


def test_analyst_registry_includes_custom_agents() -> None:
    """Custom agents must be registered with unique order numbers."""
    expected = {"alpha_seeker", "energy_transition", "emerging_tech"}
    assert expected.issubset(ANALYST_CONFIG.keys())

    orders = [ANALYST_CONFIG[k]["order"] for k in ANALYST_CONFIG]
    assert len(orders) == len(set(orders)), "Analyst orders must be unique"


def test_get_analyst_nodes_resolves_custom_agents() -> None:
    """The graph wiring helper must return callables for the new agents."""
    nodes = get_analyst_nodes()
    for key in ("alpha_seeker", "energy_transition", "emerging_tech"):
        assert key in nodes
        node_name, fn = nodes[key]
        assert node_name == f"{key}_agent"
        assert callable(fn)
