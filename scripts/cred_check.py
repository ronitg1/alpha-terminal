"""Tiny credential check: Massive ticker details + DeepSeek 1-token ping.

Run with:
    poetry run python scripts/cred_check.py

Exits 0 only if both APIs respond successfully.
"""
from __future__ import annotations

import os
import sys
import traceback  # noqa: F401 -- kept available for ad-hoc debugging of this script

# Make ``src`` importable regardless of where the script is invoked from.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv

load_dotenv()


def check_massive() -> bool:
    print("=== Massive (NVDA ticker details) ===")
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
        return False


def check_fds() -> bool:
    """Hit the financialdatasets.ai prices endpoint for a 1-day NVDA bar."""
    print("=== financialdatasets.ai (NVDA 1-day price) ===")
    try:
        import os

        import requests

        key = os.environ.get("FINANCIAL_DATASETS_API_KEY", "")
        if not key:
            print("  SKIP  FINANCIAL_DATASETS_API_KEY not set")
            return True
        headers = {"X-API-KEY": key}
        url = (
            "https://api.financialdatasets.ai/prices/"
            "?ticker=NVDA&interval=day&interval_multiplier=1"
            "&start_date=2026-05-20&end_date=2026-05-26"
        )
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"  FAIL  status={resp.status_code} body={resp.text[:200]}")
            return False
        data = resp.json()
        n = len(data.get("prices") or [])
        print(f"  OK  returned {n} daily bars")
        return True
    except Exception as exc:
        print(f"  FAIL  {type(exc).__name__}: {exc}")
        return False


def check_deepseek() -> bool:
    print("=== DeepSeek (deepseek-chat, tiny ping) ===")
    try:
        from src.llm.models import ModelProvider, get_model

        llm = get_model("deepseek-chat", ModelProvider.DEEPSEEK.value)
        response = llm.invoke("Reply with the single word OK and nothing else.")
        text = response.content if hasattr(response, "content") else str(response)
        print(f"  OK  response={text.strip()[:80]!r}")
        return True
    except Exception as exc:
        print(f"  FAIL  {type(exc).__name__}: {exc}")
        return False


def main() -> int:
    results = [
        ("Massive", check_massive()),
        ("FDS", check_fds()),
        ("DeepSeek", check_deepseek()),
    ]
    print()
    failed = [name for name, ok in results if not ok]
    if not failed:
        print("All creds OK — safe to run agent.")
        return 0
    print(f"FAILED: {failed} — fix above before running an agent.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
