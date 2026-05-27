"""Base class for backtesting strategies and the PEAD implementation.

A strategy's single job: given tickers and a data client, produce a list
of TradeSignals. The engine handles everything else (position sizing,
price lookup, equity curve, metrics).

To create a new strategy:
    1. Subclass Strategy
    2. Implement name and generate_signals
    3. Pass it to BacktestEngine.run()
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from v2.backtesting.models import TradeSignal
from v2.data.client import FDClient


class Strategy(ABC):
    """Abstract base for backtesting strategies."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable strategy name (e.g. 'pead', 'momentum')."""
        ...

    @abstractmethod
    def generate_signals(
        self,
        tickers: list[str],
        fd_client: FDClient,
    ) -> list[TradeSignal]:
        """Scan historical data and produce trade signals.

        Args:
            tickers:   Stock symbols to scan.
            fd_client: Data client for fetching prices, earnings, etc.

        Returns:
            List of TradeSignal objects, one per intended trade.
            Order does not matter — the engine sorts by entry_date.
        """
        ...


class PEADStrategy(Strategy):
    """Post-Earnings Announcement Drift: long BEATs, short MISSes.

    Scans earnings history for events where EPS beat or missed
    estimates, then generates a signal to enter the first trading
    day after the filing date and hold for a fixed number of days.
    """

    def __init__(
        self,
        *,
        earnings_limit: int = 8,
        holding_days: int = 5,
    ) -> None:
        self._earnings_limit = earnings_limit
        self._holding_days = holding_days

    @property
    def name(self) -> str:
        return "pead"

    def generate_signals(
        self,
        tickers: list[str],
        fd_client: FDClient,
    ) -> list[TradeSignal]:
        signals: list[TradeSignal] = []

        for ticker in tickers:
            records = fd_client.get_earnings_history(
                ticker, limit=self._earnings_limit,
            )

            # Deduplicate: one signal per (ticker, report_period).
            # Prefer 8-K (earliest announcement) over 10-Q/10-K.
            best: dict[str, tuple[int, object]] = {}
            source_priority = {"8-K": 0, "10-Q": 1, "10-K": 2, "20-F": 3}
            for record in records:
                if not record.filing_date or not record.quarterly:
                    continue
                surprise = record.quarterly.eps_surprise
                if surprise not in ("BEAT", "MISS"):
                    continue

                # 45-day filter: skip retrospective rows where the filing
                # date is too far after the report period end date
                filing = datetime.strptime(record.filing_date[:10], "%Y-%m-%d").date()
                report = datetime.strptime(record.report_period[:10], "%Y-%m-%d").date()
                if (filing - report).days >= 45:
                    continue

                key = f"{ticker}:{record.report_period}"
                priority = source_priority.get(record.source_type, 99)
                if key not in best or priority < best[key][0]:
                    best[key] = (priority, record)

            for _, record in best.values():
                surprise = record.quarterly.eps_surprise
                signals.append(TradeSignal(
                    ticker=ticker,
                    direction="long" if surprise == "BEAT" else "short",
                    entry_date=record.filing_date,
                    holding_days=self._holding_days,
                    metadata={
                        "eps_surprise": surprise,
                        "source_type": record.source_type,
                        "report_period": record.report_period,
                    },
                ))

        return signals
