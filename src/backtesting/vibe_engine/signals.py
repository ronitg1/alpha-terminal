"""Chart-pattern signal engine for the ported backtest engine.

Bridges our pattern detectors (:mod:`src.patterns.patterns`) to the signal
contract :meth:`BaseEngine.run_backtest` expects: ``generate(data_map)``
returning ``{symbol: pandas.Series}`` with values in ``[-1, 1]``.

Each historical detection becomes a rectangular pulse on the symbol's signal
series: signed confidence (``+`` bullish / ``-`` bearish, scaled to
``confidence / 100``) active for ``hold`` bars starting at the detection's
``end_date`` (the breakout bar). Where pulses overlap, the value with the
largest absolute magnitude wins — pulses are never summed.

Look-ahead safety: the detectors key each detection by its breakout bar and
only use data up to that bar, and the engine's ``_align`` step shifts signals
by +1 bar for next-open execution — so no additional shift happens here.
"""

from __future__ import annotations

import logging
from typing import Callable

import numpy as np
import pandas as pd

from src.patterns.patterns import BULLISH_PATTERNS, PATTERN_DETECTORS

logger = logging.getLogger(__name__)

DEFAULT_HOLD_BARS = 10


class PatternSignalEngine:
    """Signal engine that replays all historical chart-pattern detections.

    Args:
        patterns: Pattern names to run (subset of ``PATTERN_DETECTORS``).
            ``None`` means all twelve detectors.
        hold: Number of bars each detection's pulse stays active, starting
            at the detection's breakout bar.

    Raises:
        ValueError: If an unknown pattern name is requested or ``hold`` < 1.
    """

    def __init__(self, patterns: list[str] | None = None, hold: int = DEFAULT_HOLD_BARS) -> None:
        names = list(patterns) if patterns else list(PATTERN_DETECTORS.keys())
        unknown = [p for p in names if p not in PATTERN_DETECTORS]
        if unknown:
            raise ValueError(f"Unknown pattern names: {unknown}. Valid: {sorted(PATTERN_DETECTORS)}")
        if int(hold) < 1:
            raise ValueError(f"hold must be >= 1 bar, got {hold}")
        self.patterns: dict[str, Callable] = {n: PATTERN_DETECTORS[n] for n in names}
        self.hold = int(hold)

    def generate(self, data_map: dict[str, pd.DataFrame]) -> dict[str, pd.Series]:
        """Build per-symbol signal series from pattern detections.

        Args:
            data_map: symbol -> OHLCV DataFrame (DatetimeIndex, float
                open/high/low/close/volume columns).

        Returns:
            symbol -> signal Series aligned to the symbol's own index,
            values in ``[-1, 1]``.
        """
        return {symbol: self._signal_for_symbol(symbol, df) for symbol, df in data_map.items()}

    def _signal_for_symbol(self, symbol: str, df: pd.DataFrame) -> pd.Series:
        """Run all detectors over the full history and rasterize the pulses."""
        signal = pd.Series(0.0, index=df.index)
        if df.empty:
            return signal

        candles = _frame_to_candles(df)
        detections: list[dict] = []
        for name, detector in self.patterns.items():
            try:
                found = detector(candles)
            except Exception as exc:
                logger.warning("Detector %s failed for %s: %s", name, symbol, exc)
                continue
            detections.extend(found or [])

        values = signal.to_numpy(copy=True)
        n = len(values)
        for det in detections:
            pos = _bar_position(df.index, det.get("end_date"))
            if pos is None:
                logger.warning(
                    "Detection %s end_date %r not in %s index — skipped",
                    det.get("pattern"), det.get("end_date"), symbol,
                )
                continue
            bullish = det.get("pattern") in BULLISH_PATTERNS
            confidence = float(det.get("confidence", 0.0))
            value = (1.0 if bullish else -1.0) * confidence / 100.0
            stop = min(pos + self.hold, n)
            window = values[pos:stop]
            replace = np.abs(value) > np.abs(window)
            window[replace] = value

        return pd.Series(np.clip(values, -1.0, 1.0), index=df.index)


def _frame_to_candles(df: pd.DataFrame) -> list[dict]:
    """Convert an OHLCV frame into the candle-dict list the detectors expect.

    Daily bars are labeled ``YYYY-MM-DD`` (matching the detectors'
    ``_bar_label`` convention), intraday bars ``YYYY-MM-DDTHH:MM``.
    """
    candles: list[dict] = []
    for ts, row in df.iterrows():
        label = ts.strftime("%Y-%m-%dT%H:%M") if (ts.hour or ts.minute) else ts.strftime("%Y-%m-%d")
        candles.append(
            {
                "date": label,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", 0.0)),
            }
        )
    return candles


def _bar_position(index: pd.DatetimeIndex, end_date: object) -> int | None:
    """Locate a detection's breakout bar in the symbol's index.

    Args:
        index: The symbol's bar index (sorted ascending).
        end_date: Detection ``end_date`` string (``YYYY-MM-DD`` or
            ``YYYY-MM-DDTHH:MM``).

    Returns:
        Integer position of the bar, or ``None`` when unparseable / absent.
    """
    if not end_date:
        return None
    try:
        ts = pd.Timestamp(str(end_date))
    except (ValueError, TypeError):
        return None
    positions = index.get_indexer([ts])
    pos = int(positions[0])
    return pos if pos >= 0 else None
