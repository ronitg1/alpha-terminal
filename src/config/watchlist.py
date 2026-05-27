"""Opportunistic watchlist — dynamic ticker list for the opportunistic sleeve.

Edit ``WATCHLIST`` to add ad-hoc tickers you want scanned by the
opportunistic agent panel (alpha_seeker + michael_burry by default).
Use the CLI flag ``--watchlist`` on ``run_morning_scan.py`` to inject
these tickers into the morning run without committing config changes.

Pattern: when you have a candidate you're not yet ready to size into a
sleeve, drop it here for a two-agent sanity check.
"""
from __future__ import annotations

# One ticker per line. Keep comments — they're useful when you come back to
# this file in two weeks and can't remember why "PLTR" is in here.
WATCHLIST: list[str] = [
    # ── examples to delete once you populate ──
    # "PLTR",   # 2026-05 — checking variant on commercial mix shift
    # "SOFI",   # 2026-05 — burry-style net interest margin compression thesis
]


def get_watchlist() -> list[str]:
    """Return a deduplicated, uppercase copy of the watchlist."""
    seen: set[str] = set()
    out: list[str] = []
    for t in WATCHLIST:
        u = t.strip().upper()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out
