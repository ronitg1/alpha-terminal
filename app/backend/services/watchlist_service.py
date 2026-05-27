"""Watchlist read/write service.

Source of truth is ``src/config/watchlist.py`` — a plain Python list. We
keep it as text so git diffs of watchlist changes show up alongside other
config edits and the CLI keeps working without a DB.

Writes are atomic (write temp file in the same directory, then ``os.replace``)
and trigger ``importlib.reload`` so the next scan in the same uvicorn
process sees the new tickers without a restart.

Comments per ticker are preserved as ``"TICKER",  # <comment>`` lines and
parsed back on read via regex. Header docstring is regenerated from a
template — anyone hand-editing the file should keep additions BELOW the
``WATCHLIST`` assignment.
"""
from __future__ import annotations

import importlib
import os
import re
import tempfile
from pathlib import Path
from typing import Sequence

from fastapi import HTTPException

import src.config.watchlist as watchlist_module  # noqa: F401  (for reload target)

# Strict-ish ticker pattern — uppercase, digits, dots, hyphens. Up to 10 chars.
# Generous enough for foreign listings ("BABA"), share-class suffixes ("BRK.B"),
# crypto pseudo-tickers ("BTC-USD") without admitting freeform garbage.
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

# Ticker-with-comment line: "TICKER",  # comment text
_LINE_WITH_COMMENT_RE = re.compile(r'^\s*"([A-Z0-9.\-]+)"\s*,\s*#\s*(.*)$')
# Bare ticker line: "TICKER",
_LINE_BARE_RE = re.compile(r'^\s*"([A-Z0-9.\-]+)"\s*,?\s*$')

_WATCHLIST_PATH = Path(__file__).resolve().parents[3] / "src" / "config" / "watchlist.py"

# Header preserved verbatim across writes — keeps the file readable when the
# user opens it directly.
_FILE_TEMPLATE = '''\
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
{entries}]


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
'''


def read_watchlist_with_comments() -> list[dict[str, str]]:
    """Parse ``src/config/watchlist.py`` and return ``[{ticker, comment}]``.

    Best-effort regex parse — survives hand edits as long as each ticker
    sits on its own line in the standard form.
    """
    if not _WATCHLIST_PATH.exists():
        return []
    text = _WATCHLIST_PATH.read_text(encoding="utf-8")
    # Pull only the lines between WATCHLIST: list[str] = [ and the closing ]
    match = re.search(r"WATCHLIST[^=]*=\s*\[(.*?)\]", text, flags=re.DOTALL)
    if not match:
        return []
    block = match.group(1)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_line in block.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m_comment = _LINE_WITH_COMMENT_RE.match(raw_line)
        if m_comment:
            ticker, comment = m_comment.group(1), m_comment.group(2).strip()
        else:
            m_bare = _LINE_BARE_RE.match(raw_line)
            if not m_bare:
                continue
            ticker, comment = m_bare.group(1), ""
        ticker = ticker.upper()
        if ticker in seen:
            continue
        seen.add(ticker)
        out.append({"ticker": ticker, "comment": comment})
    return out


def write_watchlist(entries: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    """Replace the WATCHLIST block with ``entries``.

    Validates every ticker, writes atomically, reloads the module. Returns
    the canonicalized list (uppercased, deduped) actually persisted.
    Raises ``HTTPException(400)`` on any invalid ticker.
    """
    # Canonicalize + validate.
    canonical: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in entries:
        ticker = (raw.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        if not _TICKER_RE.match(ticker):
            raise HTTPException(status_code=400, detail=f"Invalid ticker: {ticker!r}")
        if ticker in seen:
            continue
        seen.add(ticker)
        canonical.append(
            {
                "ticker": ticker,
                "comment": (raw.get("comment") or "").strip(),
            }
        )

    lines: list[str] = []
    for e in canonical:
        if e["comment"]:
            # Escape any '#' or quotes that would break the file. Comments
            # are free text — strip newlines defensively.
            cmt = e["comment"].replace("\n", " ").replace("\r", "")
            lines.append(f'    "{e["ticker"]}",  # {cmt}')
        else:
            lines.append(f'    "{e["ticker"]}",')

    body = "\n".join(lines)
    if body:
        body += "\n"
    content = _FILE_TEMPLATE.replace("{entries}", body)

    # Atomic replace: write temp file in the same directory then rename.
    dir_ = _WATCHLIST_PATH.parent
    dir_.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".watchlist.", suffix=".tmp", dir=str(dir_))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        os.replace(tmp_path, _WATCHLIST_PATH)
    except Exception:
        # Clean up the orphan temp file if rename failed.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    # Reload so the next scan in this process picks up the new list.
    importlib.reload(watchlist_module)

    return canonical
