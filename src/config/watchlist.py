"""Opportunistic watchlist — dynamic ticker list for the opportunistic sleeve.

Edit ``WATCHLIST`` to add ad-hoc tickers you want scanned by the
opportunistic agent panel (alpha_seeker + michael_burry by default).
Use the CLI flag ``--watchlist`` on ``run_morning_scan.py`` to inject
these tickers into the morning run without committing config changes.

This file is also edited by the Sleeves Dashboard watchlist editor —
manual edits below the WATCHLIST list are preserved on every save, but
edits to the WATCHLIST list itself are overwritten.
"""
from __future__ import annotations

# One ticker per line. The UI editor preserves any "  # comment" suffix.
WATCHLIST: list[str] = [
    "PLTR",  # commercial mix shift
    "SOFI",
    "NOW",
    "GTLB",
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
