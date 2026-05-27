"""v2 backtesting — pluggable strategies and simulation engine."""

from v2.backtesting.engine import BacktestEngine
from v2.backtesting.models import (
    BacktestResult,
    PerformanceMetrics,
    Trade,
    TradeSignal,
)
from v2.backtesting.strategy import PEADStrategy, Strategy

__all__ = [
    "BacktestEngine",
    "BacktestResult",
    "PerformanceMetrics",
    "PEADStrategy",
    "Strategy",
    "Trade",
    "TradeSignal",
]
