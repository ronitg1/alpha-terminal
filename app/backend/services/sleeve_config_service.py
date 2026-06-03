"""Sleeve config read/write service.

Source of truth is ``src/config/portfolio_config.py`` — the same module the
CLI scan reads from. CRUD operations rewrite only the PORTFOLIO_SLEEVES
dict literal in that file, preserving the header docstring, helper
functions, and ``validate_portfolio`` invariant check.

Writes are atomic (temp file + ``os.replace``) and trigger ``importlib.reload``
so subsequent endpoints in the same uvicorn process see the new sleeves
without a restart.

Sleeve invariants enforced before writing:
* Agent weights within a sleeve must sum to 1.0.
* Every agent in ``agents`` must have a matching ``agent_weights`` key.
* Sleeve name must be unique and a valid Python identifier.
* Tickers must match the same uppercase-alphanumeric pattern the watchlist
  service uses.

Note: per-sleeve ``allocation_pct`` is stored on the sleeve definition but is
no longer required to sum to 100% across sleeves — per-ticker portfolio
allocation is tracked in a separate overlay (``portfolio_settings_service``).
"""
from __future__ import annotations

import importlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from fastapi import HTTPException

import src.config.portfolio_config as portfolio_config_module  # noqa: F401  (reload target)

_CONFIG_PATH = (
    Path(__file__).resolve().parents[3] / "src" / "config" / "portfolio_config.py"
)

_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_SLEEVE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
_ALLOC_TOLERANCE = 1e-6


# ─── Read ───────────────────────────────────────────────────────────────────


def read_sleeves() -> dict[str, dict[str, Any]]:
    """Snapshot the live PORTFOLIO_SLEEVES dict from the imported module.

    Reloading happens on every write so this view is always current. We
    coerce to plain dicts so callers can serialize without TypedDict drama.
    """
    raw = portfolio_config_module.PORTFOLIO_SLEEVES
    return {
        name: {
            "allocation_pct": float(sleeve["allocation_pct"]),
            "agents": list(sleeve["agents"]),
            "agent_weights": {k: float(v) for k, v in sleeve["agent_weights"].items()},
            "tickers": list(sleeve["tickers"]),
        }
        for name, sleeve in raw.items()
    }


# ─── Validate ───────────────────────────────────────────────────────────────


def _validate_sleeve_payload(name: str, sleeve: dict[str, Any]) -> dict[str, Any]:
    """Coerce + validate a single sleeve definition. Raises HTTPException on bad input."""
    if not _SLEEVE_NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid sleeve name '{name}'. Use lowercase letters, digits, "
                "underscores; start with a letter; max 31 chars."
            ),
        )

    try:
        allocation_pct = float(sleeve.get("allocation_pct", 0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="allocation_pct must be a number.")
    if allocation_pct < 0 or allocation_pct > 100:
        raise HTTPException(status_code=400, detail="allocation_pct must be 0..100.")

    agents_raw = sleeve.get("agents") or []
    if not isinstance(agents_raw, list) or not agents_raw:
        raise HTTPException(status_code=400, detail="agents must be a non-empty list.")
    agents = [str(a).strip() for a in agents_raw if str(a).strip()]
    if not agents:
        raise HTTPException(status_code=400, detail="agents must be a non-empty list.")
    if len(set(agents)) != len(agents):
        raise HTTPException(status_code=400, detail="agents list contains duplicates.")

    weights_raw = sleeve.get("agent_weights") or {}
    if not isinstance(weights_raw, dict):
        raise HTTPException(status_code=400, detail="agent_weights must be a {agent: weight} map.")
    agent_weights: dict[str, float] = {}
    for k, v in weights_raw.items():
        try:
            agent_weights[str(k)] = float(v)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"agent_weights[{k!r}] must be a number.")

    if set(agents) != set(agent_weights.keys()):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Sleeve '{name}': agents {sorted(agents)} doesn't match "
                f"agent_weights keys {sorted(agent_weights.keys())}."
            ),
        )
    weight_sum = sum(agent_weights.values())
    if abs(weight_sum - 1.0) > _ALLOC_TOLERANCE:
        raise HTTPException(
            status_code=400,
            detail=f"Sleeve '{name}': agent_weights must sum to 1.0; got {weight_sum:.4f}.",
        )

    tickers_raw = sleeve.get("tickers") or []
    if not isinstance(tickers_raw, list):
        raise HTTPException(status_code=400, detail="tickers must be a list.")
    tickers: list[str] = []
    seen_t: set[str] = set()
    for t in tickers_raw:
        u = str(t).strip().upper()
        if not u:
            continue
        if not _TICKER_RE.match(u):
            raise HTTPException(status_code=400, detail=f"Invalid ticker: {u!r}")
        if u in seen_t:
            continue
        seen_t.add(u)
        tickers.append(u)

    return {
        "allocation_pct": allocation_pct,
        "agents": agents,
        "agent_weights": agent_weights,
        "tickers": tickers,
    }


# ─── Mutate ─────────────────────────────────────────────────────────────────


def replace_all_sleeves(sleeves: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Replace the entire PORTFOLIO_SLEEVES dict atomically.

    Use for transactional edits where multiple sleeves change at once (e.g.
    rebalancing two sleeves so totals stay at 100%). Validates each sleeve
    and the total allocation in one shot.
    """
    if not sleeves:
        raise HTTPException(status_code=400, detail="Cannot save zero sleeves.")
    validated: dict[str, dict[str, Any]] = {}
    for name, sleeve in sleeves.items():
        validated[name] = _validate_sleeve_payload(name, sleeve)
    _persist(validated)
    return read_sleeves()


def create_sleeve(name: str, sleeve: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Add a new sleeve. Raises 409 if the name already exists."""
    current = read_sleeves()
    if name in current:
        raise HTTPException(status_code=409, detail=f"Sleeve '{name}' already exists.")
    validated = _validate_sleeve_payload(name, sleeve)
    current[name] = validated
    _persist(current)
    return read_sleeves()


def update_sleeve(name: str, sleeve: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Replace an existing sleeve. Raises 404 if it doesn't exist."""
    current = read_sleeves()
    if name not in current:
        raise HTTPException(status_code=404, detail=f"Sleeve '{name}' not found.")
    validated = _validate_sleeve_payload(name, sleeve)
    current[name] = validated
    _persist(current)
    return read_sleeves()


def delete_sleeve(name: str) -> dict[str, dict[str, Any]]:
    """Delete a sleeve. Raises 404 if missing; refuses to delete the last sleeve."""
    current = read_sleeves()
    if name not in current:
        raise HTTPException(status_code=404, detail=f"Sleeve '{name}' not found.")
    if len(current) == 1:
        raise HTTPException(
            status_code=400, detail="Cannot delete the only remaining sleeve."
        )
    del current[name]
    _persist(current)
    return read_sleeves()


def rename_sleeve(old_name: str, new_name: str) -> dict[str, dict[str, Any]]:
    """Rename a sleeve from ``old_name`` to ``new_name``.

    Raises 400 if ``new_name`` is not a valid sleeve identifier, 404 if
    ``old_name`` does not exist, and 409 if ``new_name`` already exists.
    Returns the updated full sleeves snapshot.
    """
    if not _SLEEVE_NAME_RE.match(new_name):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid sleeve name '{new_name}'. Use lowercase letters, digits, "
                "underscores; start with a letter; max 31 chars."
            ),
        )
    current = read_sleeves()
    if old_name not in current:
        raise HTTPException(status_code=404, detail=f"Sleeve '{old_name}' not found.")
    if new_name in current:
        raise HTTPException(status_code=409, detail=f"Sleeve '{new_name}' already exists.")
    # Build new ordered dict preserving insertion order with the rename in place.
    updated: dict[str, dict[str, Any]] = {}
    for key, val in current.items():
        if key == old_name:
            updated[new_name] = val
        else:
            updated[key] = val
    _persist(updated)
    return read_sleeves()


# ─── Serialize + write ──────────────────────────────────────────────────────


def _format_sleeves_dict(sleeves: dict[str, dict[str, Any]]) -> str:
    """Render a sleeves dict as Python source, mimicking the hand-written style."""
    lines: list[str] = ["{"]
    for name, sleeve in sleeves.items():
        lines.append(f'    "{name}": {{')
        lines.append(f'        "allocation_pct": {_fmt_float(sleeve["allocation_pct"])},')
        agents_str = ", ".join(f'"{a}"' for a in sleeve["agents"])
        lines.append(f'        "agents": [{agents_str}],')
        lines.append('        "agent_weights": {')
        for k, v in sleeve["agent_weights"].items():
            lines.append(f'            "{k}": {_fmt_float(v)},')
        lines.append("        },")
        if sleeve["tickers"]:
            tickers_str = ", ".join(f'"{t}"' for t in sleeve["tickers"])
            # Wrap long ticker lists for readability.
            if len(tickers_str) > 80:
                lines.append('        "tickers": [')
                for t in sleeve["tickers"]:
                    lines.append(f'            "{t}",')
                lines.append("        ],")
            else:
                lines.append(f'        "tickers": [{tickers_str}],')
        else:
            lines.append('        "tickers": [],')
        lines.append("    },")
    lines.append("}")
    return "\n".join(lines)


def _fmt_float(v: float) -> str:
    """Render a float without trailing ``.0`` only when it's actually integer."""
    if v == int(v):
        return f"{v:.1f}"  # keep 50.0 not 50, matches the hand-written style
    return repr(v)


def _splice_sleeves_block(file_text: str, new_block: str) -> str:
    """Replace the PORTFOLIO_SLEEVES dict literal in ``file_text`` with ``new_block``.

    Uses a brace-matching walker rather than a regex so nested dicts /
    string contents don't trip up the boundary detection.
    """
    header = re.search(r"PORTFOLIO_SLEEVES\s*:\s*[^=]*=\s*\{", file_text)
    if not header:
        raise HTTPException(
            status_code=500,
            detail="Could not locate PORTFOLIO_SLEEVES assignment in portfolio_config.py.",
        )
    # Walk forward from the opening brace, counting depth.
    open_pos = header.end() - 1
    depth = 1
    i = open_pos + 1
    while i < len(file_text):
        c = file_text[i]
        # Skip string literals so braces inside strings don't fool us.
        if c in ('"', "'"):
            # Find the matching closing quote (no escape handling needed — our
            # serialized output has no escaped quotes, but hand-edits might).
            quote = c
            i += 1
            while i < len(file_text) and file_text[i] != quote:
                if file_text[i] == "\\":
                    i += 2
                    continue
                i += 1
            i += 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                close_pos = i
                return file_text[: header.start()] + (
                    f"PORTFOLIO_SLEEVES: dict[str, Sleeve] = {new_block}"
                ) + file_text[close_pos + 1 :]
        i += 1
    raise HTTPException(
        status_code=500,
        detail="Unbalanced braces in PORTFOLIO_SLEEVES — refusing to rewrite.",
    )


def _persist(sleeves: dict[str, dict[str, Any]]) -> None:
    """Atomically rewrite portfolio_config.py with the new sleeves dict + reload."""
    text = _CONFIG_PATH.read_text(encoding="utf-8")
    new_block = _format_sleeves_dict(sleeves)
    new_text = _splice_sleeves_block(text, new_block)

    dir_ = _CONFIG_PATH.parent
    fd, tmp_path = tempfile.mkstemp(prefix=".portfolio_config.", suffix=".tmp", dir=str(dir_))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_text)
        os.replace(tmp_path, _CONFIG_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    # Reload so the same uvicorn process sees the new sleeves immediately.
    importlib.reload(portfolio_config_module)
