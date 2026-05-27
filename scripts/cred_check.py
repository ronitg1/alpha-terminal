"""Tiny credential check: Massive ticker details + DeepSeek 1-token ping.

Run with:
    poetry run python scripts/cred_check.py

Exits 0 only if both APIs respond successfully.
"""
from __future__ import annotations

import os
import sys
import traceback

# Make ``src`` importable regardless of where the script is invoked from.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv

load_dotenv()


def check_massive() -> bool:
    print("=== Cred check 1/2: Massive (NVDA ticker details) ===")
    try:
        from src.tools.massive import MassiveClient

        client = MassiveClient()
        details = client.get_ticker_details("NVDA")
        result = details.get("results") or {}
        print(
            f"  OK  ticker={result.get('ticker')} "
            f"name={result.get('name')} "
            f"mkt_cap={result.get('market_cap')}"
        )
        return True
    except Exception as exc:
        print(f"  FAIL  {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return False


def check_deepseek() -> bool:
    print("=== Cred check 2/2: DeepSeek (deepseek-chat, tiny ping) ===")
    try:
        from src.llm.models import ModelProvider, get_model

        llm = get_model("deepseek-chat", ModelProvider.DEEPSEEK.value)
        response = llm.invoke("Reply with the single word OK and nothing else.")
        text = response.content if hasattr(response, "content") else str(response)
        print(f"  OK  response={text.strip()[:80]!r}")
        return True
    except Exception as exc:
        print(f"  FAIL  {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return False


def main() -> int:
    ok_massive = check_massive()
    print()
    ok_deepseek = check_deepseek()
    print()
    if ok_massive and ok_deepseek:
        print("Both creds OK — safe to run agent.")
        return 0
    print("FAILED — fix above before running an agent.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
