"""Massive (Polygon.io rebrand) data loader for the ported backtest engine.

Implements the loader contract that :meth:`BaseEngine.run_backtest` expects:
``fetch(codes, start_date, end_date, *, interval, fields)`` returning a
mapping of symbol -> OHLCV DataFrame with a DatetimeIndex and float
``open/high/low/close/volume`` columns.

Only daily bars are supported: :class:`~src.tools.massive.client.MassiveClient`
exposes ``get_daily_aggregates`` for stocks (its intraday aggregates method
covers option contracts only), so intraday intervals raise
``NotImplementedError`` rather than silently degrading.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from src.tools.massive import MassiveClient, MassiveError

logger = logging.getLogger(__name__)

# Interval spellings accepted as "daily bars".
_DAILY_INTERVALS = {"1d", "d", "day", "daily"}

_OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]

# Polygon aggregate result keys -> our column names.
_AGG_FIELD_MAP = {"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}


class MassiveLoader:
    """Daily OHLCV loader backed by the Massive aggregates endpoint.

    One failing symbol is logged and skipped so it never aborts the batch;
    symbols with no data in the window are omitted from the result.
    """

    name = "massive"

    def __init__(self, client: MassiveClient | None = None) -> None:
        """Create the loader.

        Args:
            client: Optional pre-built :class:`MassiveClient` (e.g. with a
                per-user API key bound). When omitted, a default client is
                constructed lazily on the first ``fetch`` so importing this
                module never requires ``MASSIVE_API_KEY`` to be set.
        """
        self._client = client

    def _get_client(self) -> MassiveClient:
        """Return the bound client, constructing the default one on demand."""
        if self._client is None:
            self._client = MassiveClient()
        return self._client

    def fetch(
        self,
        codes: list[str],
        start_date: str,
        end_date: str,
        *,
        interval: str = "1D",
        fields: list[str] | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Fetch daily OHLCV history keyed by the input symbols.

        Args:
            codes: Ticker symbols (US equities, e.g. ``NVDA``).
            start_date: Inclusive start date, ``YYYY-MM-DD``.
            end_date: Inclusive end date, ``YYYY-MM-DD``.
            interval: Bar size. Only daily (``1D``/``day``) is supported.
            fields: Ignored; present for loader-contract compatibility.

        Returns:
            Mapping of symbol -> DataFrame indexed by a ``DatetimeIndex`` with
            float ``open/high/low/close/volume`` columns, sorted ascending.
            Symbols without data are omitted.

        Raises:
            NotImplementedError: For intraday intervals (no stock intraday
                aggregates method exists on MassiveClient).
        """
        del fields
        if str(interval).lower() not in _DAILY_INTERVALS:
            raise NotImplementedError(
                f"MassiveLoader supports daily bars only, got interval={interval!r}. "
                "MassiveClient has no stock intraday aggregates method."
            )

        client = self._get_client()
        result: dict[str, pd.DataFrame] = {}
        for code in codes:
            try:
                payload = client.get_daily_aggregates(code, start_date, end_date)
            except MassiveError as exc:
                logger.warning("Massive aggregates failed for %s: %s", code, exc)
                continue
            df = _frame_from_aggregates(payload)
            if df is not None and not df.empty:
                result[code] = df
            else:
                logger.warning("No usable daily bars for %s in %s..%s", code, start_date, end_date)
        return result


def _frame_from_aggregates(payload: dict[str, Any]) -> pd.DataFrame | None:
    """Convert a Polygon ``/v2/aggs`` payload into a normalized OHLCV frame.

    Args:
        payload: Raw parsed JSON from ``get_daily_aggregates`` — bars live in
            ``results`` as ``{"t": epoch_ms, "o", "h", "l", "c", "v", ...}``.

    Returns:
        DataFrame with a ``datetime64[ns]`` index and float OHLCV columns,
        or ``None`` when the payload holds no usable bars.
    """
    results = payload.get("results") or []
    if not isinstance(results, list) or not results:
        return None

    rows: list[dict[str, Any]] = []
    for bar in results:
        if not isinstance(bar, dict):
            continue
        row: dict[str, Any] = {"timestamp": bar.get("t")}
        for key, col in _AGG_FIELD_MAP.items():
            row[col] = bar.get(key)
        if row["timestamp"] is None or any(row[c] is None for c in ("open", "high", "low", "close")):
            continue
        rows.append(row)
    if not rows:
        return None

    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms").astype("datetime64[ns]")
    # Daily bars are labeled by session date; normalize away the exchange
    # midnight-ET offset in the epoch so the index matches YYYY-MM-DD dates.
    df["timestamp"] = df["timestamp"].dt.normalize()
    df = df.set_index("timestamp").sort_index()
    df = df[_OHLCV_COLUMNS].astype(float)
    df["volume"] = df["volume"].fillna(0.0)
    df = df.dropna(subset=["open", "high", "low", "close"])
    return df
