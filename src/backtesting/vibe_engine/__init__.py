# Portions derived from HKUDS/Vibe-Trading (MIT License).
# See THIRD_PARTY_NOTICES.md at the repository root.
"""Ported Vibe-Trading backtest engine (signals-in, metrics-out).

Package layout:
  - ``models``     — Position / TradeRecord / EquitySnapshot dataclasses
  - ``metrics``    — calc_metrics + trade statistics
  - ``validation`` — Monte Carlo / bootstrap / walk-forward checks
  - ``run_card``   — reproducibility run-card writer
  - ``base``       — BaseEngine bar-by-bar execution loop
  - ``equity``     — GlobalEquityEngine (US market rules used here)
  - ``loaders``    — MassiveLoader (daily OHLCV via our Massive client)
  - ``signals``    — PatternSignalEngine (chart-pattern detections -> signals)

Named ``vibe_engine`` (not ``engine`` as originally planned) because
``src/backtesting/engine.py`` already exists and a same-named package would
shadow it for every existing importer.
"""

from __future__ import annotations

from src.backtesting.vibe_engine.base import BaseEngine
from src.backtesting.vibe_engine.equity import GlobalEquityEngine
from src.backtesting.vibe_engine.loaders import MassiveLoader
from src.backtesting.vibe_engine.metrics import calc_metrics
from src.backtesting.vibe_engine.models import EquitySnapshot, Position, TradeRecord
from src.backtesting.vibe_engine.signals import PatternSignalEngine
from src.backtesting.vibe_engine.validation import run_validation

__all__ = [
    "BaseEngine",
    "GlobalEquityEngine",
    "MassiveLoader",
    "PatternSignalEngine",
    "calc_metrics",
    "run_validation",
    "EquitySnapshot",
    "Position",
    "TradeRecord",
]
