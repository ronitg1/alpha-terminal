"""Morning scan — produce a ranked signal table across all portfolio sleeves.

Runs each sleeve's agent panel on its tickers, aggregates per-agent signals
into a weighted consensus, and emits two artifacts:

* A terminal table with color highlights (green / red / yellow / variant-bold).
* A timestamped CSV in ``outputs/YYYY-MM-DD_morning_scan.csv``.

Usage::

    poetry run python -m src.run_morning_scan
    poetry run python -m src.run_morning_scan --sleeve energy_transition
    poetry run python -m src.run_morning_scan --watchlist
    poetry run python -m src.run_morning_scan --end-date 2026-05-27

This script intentionally does **not** invoke the LangGraph portfolio
manager — we only want signals here, not sized positions.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from colorama import Fore, Style, init as colorama_init
from langchain_core.messages import HumanMessage

from src.config.portfolio_config import PORTFOLIO_SLEEVES, Sleeve
from src.config.watchlist import get_watchlist
from src.utils.analysts import ANALYST_CONFIG

logger = logging.getLogger(__name__)

colorama_init()

# ─── Aggregation thresholds ───────────────────────────────────────────────────

# Consensus is computed by mapping bullish→+1, bearish→-1, neutral→0, scaling
# by per-agent weight × confidence (0-100), then summing. The result is a
# weighted score in roughly [-100, +100]. Thresholds for tagging consensus:
CONSENSUS_BULLISH_THRESHOLD = 35.0
CONSENSUS_BEARISH_THRESHOLD = -35.0
HIGH_CONVICTION_CONFIDENCE = 70.0


# ─── Data classes ────────────────────────────────────────────────────────────


@dataclass
class AgentVerdict:
    """Single agent's read on a single ticker."""

    agent_key: str
    signal: str  # bullish | bearish | neutral
    confidence: float  # 0-100
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def direction(self) -> int:
        return {"bullish": 1, "bearish": -1, "neutral": 0}.get(self.signal, 0)


@dataclass
class TickerRow:
    ticker: str
    sleeve: str
    verdicts: dict[str, AgentVerdict] = field(default_factory=dict)
    consensus: str = "neutral"
    weighted_score: float = 0.0
    avg_confidence: float = 0.0
    position_type: str = "no_position"
    hold_period: str = "n_a"
    has_variant_perception: bool = False
    variant_perception_text: str = ""
    highlight: str = "neutral"  # green | red | yellow | neutral


# ─── Signal aggregation ──────────────────────────────────────────────────────


def aggregate_verdicts(
    sleeve_name: str,
    ticker: str,
    verdicts: dict[str, AgentVerdict],
    agent_weights: dict[str, float],
) -> TickerRow:
    """Combine per-agent verdicts into a single TickerRow."""
    row = TickerRow(ticker=ticker, sleeve=sleeve_name, verdicts=verdicts)

    if not verdicts:
        return row

    weighted_sum = 0.0
    confidence_sum = 0.0
    weight_total = 0.0
    for agent_key, weight in agent_weights.items():
        v = verdicts.get(agent_key)
        if v is None:
            # Agent failed or skipped this ticker. Skip the weight as well so
            # we don't dilute confidence by an absent agent.
            continue
        weighted_sum += weight * v.direction * v.confidence
        confidence_sum += weight * v.confidence
        weight_total += weight

    if weight_total > 0:
        row.weighted_score = weighted_sum / weight_total
        row.avg_confidence = confidence_sum / weight_total

    # Consensus tag.
    if row.weighted_score >= CONSENSUS_BULLISH_THRESHOLD:
        row.consensus = "bullish"
    elif row.weighted_score <= CONSENSUS_BEARISH_THRESHOLD:
        row.consensus = "bearish"
    else:
        row.consensus = "neutral"

    # Highlight: green = all agents bullish & high conviction, red mirror, yellow = mixed.
    directions = {v.direction for v in verdicts.values()}
    confidences = [v.confidence for v in verdicts.values()]
    all_high_conf = bool(confidences) and min(confidences) >= HIGH_CONVICTION_CONFIDENCE
    if directions == {1} and all_high_conf:
        row.highlight = "green"
    elif directions == {-1} and all_high_conf:
        row.highlight = "red"
    elif len(directions) > 1:
        row.highlight = "yellow"
    else:
        row.highlight = "neutral"

    # Pull position_type / hold_period / variant from the richest agent output
    # available. Order of preference: alpha_seeker → emerging_tech → energy_transition.
    for preferred in ("alpha_seeker", "emerging_tech", "energy_transition"):
        v = verdicts.get(preferred)
        if v is None:
            continue
        raw = v.raw
        if isinstance(raw, dict):
            row.position_type = raw.get("position_type") or row.position_type
            row.hold_period = raw.get("hold_period") or row.hold_period
        break

    alpha = verdicts.get("alpha_seeker")
    if alpha is not None and isinstance(alpha.raw, dict):
        vp = alpha.raw.get("variant_perception") or ""
        row.variant_perception_text = vp
        row.has_variant_perception = (
            bool(vp)
            and "no edge" not in vp.lower()
            and bool(alpha.raw.get("has_edge", False))
        )

    return row


# ─── Running agents ──────────────────────────────────────────────────────────


def run_sleeve(
    sleeve_name: str,
    sleeve: Sleeve,
    end_date: str,
    *,
    show_reasoning: bool = False,
) -> list[TickerRow]:
    """Execute every agent in a sleeve on every ticker and return ranked rows."""
    if not sleeve["tickers"]:
        logger.info("Sleeve %s has no tickers — skipping.", sleeve_name)
        return []

    # Build a minimal AgentState the agents can read.
    state = {
        "messages": [],
        "data": {
            "tickers": sleeve["tickers"],
            "end_date": end_date,
            "start_date": end_date,
            "analyst_signals": {},
        },
        "metadata": {"show_reasoning": show_reasoning},
    }

    for agent_key in sleeve["agents"]:
        config = ANALYST_CONFIG.get(agent_key)
        if config is None:
            logger.error("Sleeve %s references unknown agent '%s' — skipping.", sleeve_name, agent_key)
            continue
        agent_func = config["agent_func"]
        agent_id = f"{agent_key}_agent"
        logger.info("Running %s on %d tickers in sleeve %s", agent_id, len(sleeve["tickers"]), sleeve_name)
        try:
            agent_func(state, agent_id=agent_id)
        except Exception as exc:
            # One agent failing must not nuke the whole scan.
            logger.exception("Agent %s blew up on sleeve %s: %s", agent_id, sleeve_name, exc)
            continue

    # ─── Build per-ticker rows ──────────────────────────────────────────────
    rows: list[TickerRow] = []
    for ticker in sleeve["tickers"]:
        verdicts: dict[str, AgentVerdict] = {}
        for agent_key in sleeve["agents"]:
            agent_id = f"{agent_key}_agent"
            agent_output = state["data"]["analyst_signals"].get(agent_id, {}).get(ticker)
            if not agent_output:
                continue
            verdicts[agent_key] = AgentVerdict(
                agent_key=agent_key,
                signal=str(agent_output.get("signal", "neutral")),
                confidence=float(agent_output.get("confidence", 0.0)),
                raw=agent_output if isinstance(agent_output, dict) else {},
            )
        rows.append(aggregate_verdicts(sleeve_name, ticker, verdicts, sleeve["agent_weights"]))

    # Sort by abs(weighted_score) desc — strongest convictions first.
    rows.sort(key=lambda r: abs(r.weighted_score), reverse=True)
    return rows


# ─── Output formatting ───────────────────────────────────────────────────────


_COLOR_MAP = {
    "green": Fore.GREEN,
    "red": Fore.RED,
    "yellow": Fore.YELLOW,
    "neutral": "",
}


def render_terminal_table(rows: list[TickerRow]) -> str:
    """Render the ranked signal table for terminal output."""
    if not rows:
        return "(no signals)"

    header = (
        f"{'Ticker':<7} {'Sleeve':<18} {'Signals':<24} "
        f"{'Consensus':<10} {'Conv':>5} {'Position':<14} {'Hold':<8}  Variant"
    )
    sep = "─" * len(header)
    lines = [header, sep]

    for r in rows:
        signals_str = " ".join(
            f"{k[:3]}:{v.signal[:2]}({v.confidence:.0f})"
            for k, v in r.verdicts.items()
        )
        color = _COLOR_MAP.get(r.highlight, "")
        reset = Style.RESET_ALL if color else ""
        ticker_display = f"{Style.BRIGHT}{r.ticker}{Style.NORMAL}" if r.has_variant_perception else r.ticker
        line = (
            f"{color}{ticker_display:<7} {r.sleeve:<18} {signals_str:<24} "
            f"{r.consensus:<10} {r.avg_confidence:>5.0f} "
            f"{r.position_type:<14} {r.hold_period:<8}  "
            f"{(r.variant_perception_text[:60] + '…') if len(r.variant_perception_text) > 60 else r.variant_perception_text}"
            f"{reset}"
        )
        lines.append(line)
    return "\n".join(lines)


def render_summary(rows: list[TickerRow]) -> str:
    high_long = sum(1 for r in rows if r.highlight == "green")
    high_short = sum(1 for r in rows if r.highlight == "red")
    mixed = sum(1 for r in rows if r.highlight == "yellow")
    return (
        f"{high_long} high-conviction longs, "
        f"{high_short} high-conviction shorts, "
        f"{mixed} mixed — review manually"
    )


def write_csv(rows: Iterable[TickerRow], out_dir: Path, end_date: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{end_date}_morning_scan.csv"
    fieldnames = [
        "ticker",
        "sleeve",
        "consensus",
        "weighted_score",
        "avg_confidence",
        "highlight",
        "position_type",
        "hold_period",
        "has_variant_perception",
        "variant_perception",
        "per_agent_signals",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    "ticker": r.ticker,
                    "sleeve": r.sleeve,
                    "consensus": r.consensus,
                    "weighted_score": round(r.weighted_score, 2),
                    "avg_confidence": round(r.avg_confidence, 2),
                    "highlight": r.highlight,
                    "position_type": r.position_type,
                    "hold_period": r.hold_period,
                    "has_variant_perception": r.has_variant_perception,
                    "variant_perception": r.variant_perception_text,
                    "per_agent_signals": "; ".join(
                        f"{k}={v.signal}({v.confidence:.0f})" for k, v in r.verdicts.items()
                    ),
                }
            )
    return path


# ─── CLI entry point ─────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run a portfolio-wide morning scan across all sleeves.")
    p.add_argument(
        "--sleeve",
        action="append",
        choices=list(PORTFOLIO_SLEEVES.keys()),
        help="Limit the scan to one or more sleeves (default: all).",
    )
    p.add_argument(
        "--tickers",
        type=str,
        help=(
            "Comma-separated tickers. Filters each sleeve to its intersection with "
            "this list. Tickers not in any sleeve are dropped with a warning."
        ),
    )
    p.add_argument(
        "--end-date",
        default=datetime.date.today().isoformat(),
        help="End date for data fetches (YYYY-MM-DD). Default: today.",
    )
    p.add_argument(
        "--watchlist",
        action="store_true",
        help="Include the opportunistic watchlist (src/config/watchlist.py).",
    )
    p.add_argument(
        "--show-reasoning",
        action="store_true",
        help="Pass show_reasoning=True to each agent (chatty terminal output).",
    )
    p.add_argument(
        "--outputs-dir",
        default="outputs",
        help="Directory to write the CSV (default: ./outputs).",
    )
    return p.parse_args(argv)


def _apply_ticker_filter(
    selected: dict[str, Sleeve],
    ticker_filter: list[str] | None,
) -> dict[str, Sleeve]:
    """Filter each sleeve's tickers down to the intersection with ``ticker_filter``.

    Returns a fresh dict so we don't mutate the global PORTFOLIO_SLEEVES.
    Logs any tickers in ``ticker_filter`` that don't appear in any selected sleeve.
    """
    if not ticker_filter:
        return selected
    wanted = {t.strip().upper() for t in ticker_filter if t.strip()}
    seen: set[str] = set()
    out: dict[str, Sleeve] = {}
    for name, sleeve in selected.items():
        kept = [t for t in sleeve["tickers"] if t.upper() in wanted]
        seen.update(t.upper() for t in kept)
        # Always keep the sleeve entry — even if empty — so the scan reports
        # accurately (no sleeve silently disappears).
        new_sleeve = dict(sleeve)
        new_sleeve["tickers"] = kept
        out[name] = new_sleeve  # type: ignore[assignment]
    missing = sorted(wanted - seen)
    if missing:
        logger.warning(
            "--tickers requested but not in any selected sleeve: %s", ", ".join(missing)
        )
    return out


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    selected: dict[str, Sleeve] = {
        name: sleeve
        for name, sleeve in PORTFOLIO_SLEEVES.items()
        if (args.sleeve is None) or (name in args.sleeve)
    }
    if args.watchlist:
        watchlist_tickers = get_watchlist()
        if watchlist_tickers:
            # Override the opportunistic sleeve tickers with the watchlist.
            opp = selected.get("opportunistic") or PORTFOLIO_SLEEVES["opportunistic"]
            opp = dict(opp)
            opp["tickers"] = watchlist_tickers
            selected["opportunistic"] = opp  # type: ignore[assignment]
        else:
            logger.warning("--watchlist passed but src/config/watchlist.py is empty.")

    # --tickers filter (applied AFTER --watchlist so users can intersect both).
    if args.tickers:
        ticker_list = [t.strip() for t in args.tickers.split(",") if t.strip()]
        selected = _apply_ticker_filter(selected, ticker_list)

    all_rows: list[TickerRow] = []
    for sleeve_name, sleeve in selected.items():
        rows = run_sleeve(sleeve_name, sleeve, args.end_date, show_reasoning=args.show_reasoning)
        all_rows.extend(rows)

    # Print the table + summary, write CSV.
    print()
    print(render_terminal_table(all_rows))
    print()
    print(render_summary(all_rows))
    print()
    csv_path = write_csv(all_rows, Path(args.outputs_dir), args.end_date)
    print(f"CSV: {csv_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
