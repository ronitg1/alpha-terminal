"""Sleeve / agent performance attribution for backtest output.

Takes a list of ``Trade`` records (whatever shape your backtester emits)
and produces three artifacts:

1. Per-sleeve performance: Sharpe, max drawdown, win rate, avg hold period.
2. Agent attribution: total realized P&L attributable to each agent,
   weighted by its agent_weight inside the sleeve. Useful for spotting
   which agents are doing the heavy lifting.
3. Underperforming-agent warnings: any agent whose 90-day trailing win
   rate drops below 45% gets a printed warning.

This module is deliberately separate from the upstream backtester engine —
it's a *reporter* that runs on saved output. That keeps the simulation
engine simple and lets you re-run attribution offline on cached results.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable

logger = logging.getLogger(__name__)


# ─── Input types ─────────────────────────────────────────────────────────────


@dataclass
class Trade:
    """A single closed trade record.

    Designed to map cleanly onto whatever the upstream backtester emits, plus
    the rg-alpha-engine sleeve/agent metadata. Build these by joining your
    backtest output with the sleeve config.
    """

    ticker: str
    sleeve: str
    agent: str  # canonical agent key (e.g. "alpha_seeker")
    open_date: date
    close_date: date
    side: str  # "long" | "short"
    pnl: float  # realized $ P&L, signed
    entry_value: float  # gross $ deployed at entry (for return computation)

    @property
    def hold_days(self) -> int:
        return (self.close_date - self.open_date).days

    @property
    def return_pct(self) -> float:
        if self.entry_value == 0:
            return 0.0
        # Shorts make money on a price drop, so flip the sign convention if
        # the upstream backtester stores entry_value as positive notional.
        return self.pnl / abs(self.entry_value)


# ─── Per-sleeve metrics ──────────────────────────────────────────────────────


@dataclass
class SleeveMetrics:
    sleeve: str
    n_trades: int
    win_rate: float
    avg_hold_days: float
    total_pnl: float
    sharpe: float | None
    max_drawdown: float


def compute_sleeve_metrics(trades: Iterable[Trade]) -> dict[str, SleeveMetrics]:
    """Group trades by sleeve and compute aggregate metrics."""
    by_sleeve: dict[str, list[Trade]] = {}
    for t in trades:
        by_sleeve.setdefault(t.sleeve, []).append(t)

    out: dict[str, SleeveMetrics] = {}
    for sleeve, group in by_sleeve.items():
        if not group:
            continue
        wins = sum(1 for t in group if t.pnl > 0)
        win_rate = wins / len(group)
        avg_hold = sum(t.hold_days for t in group) / len(group)
        total_pnl = sum(t.pnl for t in group)
        sharpe = _sharpe_from_trade_returns([t.return_pct for t in group])
        max_dd = _max_drawdown([t.pnl for t in sorted(group, key=lambda x: x.close_date)])
        out[sleeve] = SleeveMetrics(
            sleeve=sleeve,
            n_trades=len(group),
            win_rate=win_rate,
            avg_hold_days=avg_hold,
            total_pnl=total_pnl,
            sharpe=sharpe,
            max_drawdown=max_dd,
        )
    return out


def _sharpe_from_trade_returns(returns: list[float]) -> float | None:
    """Trade-level Sharpe (mean / stddev). Returns None for <2 trades.

    Note: this is per-trade Sharpe, not annualized — annualizing requires
    a stable frequency assumption that backtests rarely satisfy. For
    comparison across sleeves the relative ranking matters more than the
    absolute number.
    """
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return None
    return mean / sd


def _max_drawdown(pnl_sequence: list[float]) -> float:
    """Peak-to-trough drawdown on a cumulative P&L curve (return as $)."""
    if not pnl_sequence:
        return 0.0
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnl_sequence:
        cumulative += pnl
        peak = max(peak, cumulative)
        max_dd = min(max_dd, cumulative - peak)
    return max_dd


# ─── Agent attribution ──────────────────────────────────────────────────────


@dataclass
class AgentAttribution:
    agent: str
    n_trades: int
    win_rate: float
    total_pnl_attributed: float
    avg_return_pct: float


def compute_agent_attribution(
    trades: Iterable[Trade],
    sleeves_config: dict[str, dict],
) -> dict[str, AgentAttribution]:
    """Attribute realized P&L to agents, weighted by sleeve agent_weights.

    For each trade, the attributed P&L for an agent equals
    ``trade.pnl * sleeve.agent_weights[agent]``. The sum across trades is
    that agent's attribution.
    """
    by_agent_pnl: dict[str, float] = {}
    by_agent_wins: dict[str, int] = {}
    by_agent_trades: dict[str, int] = {}
    by_agent_returns: dict[str, list[float]] = {}

    for t in trades:
        sleeve_def = sleeves_config.get(t.sleeve)
        if sleeve_def is None:
            logger.warning("Trade for sleeve '%s' has no sleeve config — skipping.", t.sleeve)
            continue
        weights = sleeve_def.get("agent_weights", {})
        # If the trade names an agent (e.g. the agent that was the strongest
        # signal at entry), prefer that. Otherwise distribute by weights.
        if t.agent in weights:
            agents_and_shares = [(t.agent, 1.0)]
        else:
            agents_and_shares = list(weights.items())

        for agent, share in agents_and_shares:
            by_agent_pnl[agent] = by_agent_pnl.get(agent, 0.0) + t.pnl * share
            by_agent_trades[agent] = by_agent_trades.get(agent, 0) + 1
            if t.pnl > 0:
                by_agent_wins[agent] = by_agent_wins.get(agent, 0) + 1
            by_agent_returns.setdefault(agent, []).append(t.return_pct)

    out: dict[str, AgentAttribution] = {}
    for agent, pnl in by_agent_pnl.items():
        n = by_agent_trades[agent]
        wins = by_agent_wins.get(agent, 0)
        rets = by_agent_returns[agent]
        out[agent] = AgentAttribution(
            agent=agent,
            n_trades=n,
            win_rate=wins / n if n else 0.0,
            total_pnl_attributed=pnl,
            avg_return_pct=sum(rets) / len(rets) if rets else 0.0,
        )
    return out


# ─── Underperforming-agent warnings ─────────────────────────────────────────


UNDERPERFORM_WIN_RATE_THRESHOLD = 0.45
DEFAULT_TRAILING_WINDOW_DAYS = 90


@dataclass
class UnderperformWarning:
    agent: str
    n_trades_in_window: int
    win_rate: float
    threshold: float

    def message(self) -> str:
        return (
            f"Agent '{self.agent}' underperforming — "
            f"{self.win_rate:.1%} win rate over last {self.n_trades_in_window} trades "
            f"(threshold {self.threshold:.0%}). Consider reducing weight."
        )


def warn_underperforming_agents(
    trades: Iterable[Trade],
    *,
    as_of: date | None = None,
    window_days: int = DEFAULT_TRAILING_WINDOW_DAYS,
    threshold: float = UNDERPERFORM_WIN_RATE_THRESHOLD,
    min_trades: int = 5,
) -> list[UnderperformWarning]:
    """Return warnings for agents whose trailing win rate is below threshold."""
    as_of = as_of or date.today()
    window_start = as_of - timedelta(days=window_days)
    trailing: dict[str, list[Trade]] = {}
    for t in trades:
        if t.close_date < window_start or t.close_date > as_of:
            continue
        trailing.setdefault(t.agent, []).append(t)

    warnings: list[UnderperformWarning] = []
    for agent, group in trailing.items():
        if len(group) < min_trades:
            continue
        wins = sum(1 for t in group if t.pnl > 0)
        win_rate = wins / len(group)
        if win_rate < threshold:
            warnings.append(
                UnderperformWarning(
                    agent=agent,
                    n_trades_in_window=len(group),
                    win_rate=win_rate,
                    threshold=threshold,
                )
            )
    return warnings


# ─── Convenience renderer ───────────────────────────────────────────────────


def extract_trades_from_day_results(
    day_results: list[dict],
    *,
    ticker_to_sleeve: dict[str, str],
) -> list[Trade]:
    """Reconstruct closed ``Trade`` records from a BacktestService day-result stream.

    Walks the per-day ``executed_trades`` + ``current_prices`` to identify
    open → close transitions for each ticker (long-only for now — the
    sleeves strategy doesn't short). For each closed trade:

    - ``open_date``  = day a buy executed and the position was previously flat.
    - ``close_date`` = day a sell brought the position back to zero.
    - ``pnl``        = (close_price - avg_entry_price) * closed_shares.
    - ``agent``      = the analyst whose signal had the highest confidence
                       at entry. Falls back to empty string (sleeve_attribution
                       distributes by sleeve weights when the agent key isn't
                       in the sleeve's weight dict).

    Tickers whose sleeve can't be resolved are skipped with a debug log.
    Day results are assumed to be in chronological order — the upstream
    BacktestService emits them that way.
    """
    if not day_results:
        return []

    # Per-ticker FIFO position tracker: each open position is one buy lot.
    # The first sell flat-out closes it (no partial fills tracked; backtest
    # decisions are coarse enough that this captures realistic strategy P&L).
    open_lots: dict[str, dict] = {}  # ticker -> {qty, cost_basis, open_date, agent}
    trades: list[Trade] = []

    for day in day_results:
        date_str = day.get("date")
        try:
            date_obj = date.fromisoformat(date_str) if date_str else None
        except (TypeError, ValueError):
            date_obj = None
        if date_obj is None:
            continue

        decisions = day.get("decisions") or {}
        executed = day.get("executed_trades") or {}
        prices = day.get("current_prices") or {}
        analyst_signals = day.get("analyst_signals") or {}

        for ticker, qty in executed.items():
            if not qty:
                continue
            decision = decisions.get(ticker) or {}
            action = decision.get("action") or ""
            price = prices.get(ticker)
            if price is None:
                continue

            if action == "buy" and qty > 0:
                # Find best-confidence agent for attribution.
                best_agent = ""
                best_conf = -1.0
                for agent_id, sigmap in analyst_signals.items():
                    sig = (sigmap or {}).get(ticker)
                    if not isinstance(sig, dict):
                        continue
                    conf = float(sig.get("confidence", 0))
                    if conf > best_conf:
                        best_conf = conf
                        # Strip the trailing _agent suffix to match sleeve config keys.
                        best_agent = agent_id.removesuffix("_agent") if hasattr(agent_id, "removesuffix") else agent_id.replace("_agent", "")
                if ticker in open_lots:
                    # Average into the existing lot.
                    lot = open_lots[ticker]
                    new_qty = lot["qty"] + qty
                    lot["cost_basis"] = (lot["cost_basis"] * lot["qty"] + price * qty) / new_qty
                    lot["qty"] = new_qty
                else:
                    open_lots[ticker] = {
                        "qty": qty,
                        "cost_basis": price,
                        "open_date": date_obj,
                        "agent": best_agent,
                    }
            elif action == "sell" and qty > 0:
                lot = open_lots.get(ticker)
                if not lot:
                    continue
                closed_qty = min(qty, lot["qty"])
                pnl = (price - lot["cost_basis"]) * closed_qty
                entry_value = lot["cost_basis"] * closed_qty
                sleeve = ticker_to_sleeve.get(ticker)
                if sleeve is None:
                    logger.debug("Ticker %s has no sleeve mapping; skipping trade.", ticker)
                else:
                    trades.append(
                        Trade(
                            ticker=ticker,
                            sleeve=sleeve,
                            agent=lot["agent"],
                            open_date=lot["open_date"],
                            close_date=date_obj,
                            side="long",
                            pnl=pnl,
                            entry_value=entry_value,
                        )
                    )
                lot["qty"] -= closed_qty
                if lot["qty"] <= 0:
                    del open_lots[ticker]

    return trades


def render_attribution_report(
    sleeve_metrics: dict[str, SleeveMetrics],
    agent_attribution: dict[str, AgentAttribution],
    warnings: list[UnderperformWarning],
) -> str:
    """Render a plain-text attribution report (no color)."""
    lines: list[str] = []

    lines.append("─── Per-sleeve performance ─" + "─" * 40)
    lines.append(f"{'Sleeve':<22} {'Trades':>6} {'Win%':>6} {'AvgHold':>8} {'Sharpe':>8} {'MaxDD':>10} {'TotalPnL':>12}")
    for sm in sorted(sleeve_metrics.values(), key=lambda x: x.total_pnl, reverse=True):
        sharpe_str = f"{sm.sharpe:.2f}" if sm.sharpe is not None else "  n/a"
        lines.append(
            f"{sm.sleeve:<22} {sm.n_trades:>6d} {sm.win_rate * 100:>5.1f}% "
            f"{sm.avg_hold_days:>7.1f}d {sharpe_str:>8} {sm.max_drawdown:>10,.0f} {sm.total_pnl:>12,.0f}"
        )

    lines.append("")
    lines.append("─── Agent attribution ─" + "─" * 44)
    lines.append(f"{'Agent':<22} {'Trades':>6} {'Win%':>6} {'AvgRet%':>8} {'PnL($)':>14}")
    for aa in sorted(agent_attribution.values(), key=lambda x: x.total_pnl_attributed, reverse=True):
        lines.append(
            f"{aa.agent:<22} {aa.n_trades:>6d} {aa.win_rate * 100:>5.1f}% "
            f"{aa.avg_return_pct * 100:>7.1f}% {aa.total_pnl_attributed:>14,.0f}"
        )

    if warnings:
        lines.append("")
        lines.append("─── Underperforming agents ─" + "─" * 38)
        for w in warnings:
            lines.append("  ⚠  " + w.message())

    return "\n".join(lines)


# ─── Backtester default start date ──────────────────────────────────────────
# Post-IRA implementation baseline. Imported by backtest runners that need a
# sensible default.
DEFAULT_BACKTEST_START_DATE = "2023-01-01"


__all__ = [
    "Trade",
    "SleeveMetrics",
    "AgentAttribution",
    "UnderperformWarning",
    "compute_sleeve_metrics",
    "compute_agent_attribution",
    "warn_underperforming_agents",
    "extract_trades_from_day_results",
    "render_attribution_report",
    "DEFAULT_BACKTEST_START_DATE",
    "UNDERPERFORM_WIN_RATE_THRESHOLD",
]
