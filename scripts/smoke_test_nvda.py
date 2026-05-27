"""Live NVDA smoke test for the alpha_seeker agent.

End-to-end exercise of the full pipeline:
    Massive (data) -> alpha_seeker (LangGraph node) -> DeepSeek R1 -> structured signal

This is the cheapest possible live test (one ticker, one agent, one LLM call)
intended to catch wiring/plumbing bugs before we run the wider 10-ticker scan.

Run with:
    poetry run python scripts/smoke_test_nvda.py

Exits 0 only if a structured AlphaSeekerSignal is produced. Prints the full
signal output so you can eyeball the reasoning quality.
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import time
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv

load_dotenv()

from src.agents.alpha_seeker import alpha_seeker_agent


def main() -> int:
    ticker = "NVDA"
    end_date = datetime.date.today().isoformat()

    print(f"=== NVDA smoke test ({end_date}) ===")
    print("agent: alpha_seeker")
    print("LLM:   deepseek-reasoner (R1)")
    print("data:  Massive (Polygon)")
    print()

    state = {
        "messages": [],
        "data": {
            "tickers": [ticker],
            "end_date": end_date,
            "start_date": end_date,
            "analyst_signals": {},
        },
        # show_reasoning=True so we see the prompt/response. model defaults
        # fall through to the system default (deepseek-reasoner).
        "metadata": {"show_reasoning": True},
    }

    # NOTE: we intentionally do NOT call progress.start() here. The Rich Live
    # display crashes on Windows cp1252 consoles when it tries to render
    # unicode glyphs at teardown. For a one-ticker smoke test the per-step
    # progress updates are noise anyway; the agent's own logging is enough.
    t0 = time.time()
    try:
        alpha_seeker_agent(state, agent_id="alpha_seeker_agent")
    except Exception as exc:
        print()
        print(f"FAIL  {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1
    elapsed = time.time() - t0

    signal = state["data"]["analyst_signals"].get("alpha_seeker_agent", {}).get(ticker)
    if not signal:
        print()
        print("FAIL  no signal produced for NVDA")
        return 1

    print()
    print(f"=== Result ({elapsed:.1f}s) ===")
    print(json.dumps(signal, indent=2, default=str))

    # Minimal structural assertions — fields must be present and the right type.
    required_fields = [
        "signal", "confidence", "variant_perception", "has_edge",
        "catalyst_near_term", "catalyst_medium_term", "catalyst_type",
        "position_type", "conviction", "hold_period",
        "kill_switch", "probability_wrong", "reasoning",
    ]
    missing = [f for f in required_fields if f not in signal]
    if missing:
        print()
        print(f"FAIL  signal missing fields: {missing}")
        return 1
    if signal["signal"] not in {"bullish", "bearish", "neutral"}:
        print(f"FAIL  unexpected signal value: {signal['signal']!r}")
        return 1

    print()
    print("PASS — structured AlphaSeekerSignal returned successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
