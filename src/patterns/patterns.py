"""
Technical chart pattern detection engine.

Each detector takes a list of candle dicts (date, open, high, low, close, volume)
and returns a list of detected pattern instances with confidence scores.

Confidence = 0.4 * breakout_score + 0.3 * volume_score + 0.3 * touch_score
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ─── Shared utilities ──────────────────────────────────────────────────────────

def _to_df(candles: list[dict]) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame()
    df = pd.DataFrame(candles)
    df["date"] = pd.to_datetime(df["date"])
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)
    df["avg_vol_20"] = df["volume"].rolling(20, min_periods=1).mean()
    return df


def _bar_label(df: pd.DataFrame, i: int) -> str:
    """Render a bar's timestamp for output.

    Daily bars → ``YYYY-MM-DD``. Intraday bars (any nonzero time component)
    → ``YYYY-MM-DDTHH:MM`` so multiple bars within one session stay distinct.
    RTH-filtered equity bars never land exactly on midnight, so a zero time
    component reliably means a daily bar.
    """
    t = df["date"].iloc[i]
    if t.hour or t.minute:
        return t.strftime("%Y-%m-%dT%H:%M")
    return str(t)[:10]


def _result(
    pattern: str,
    df: pd.DataFrame,
    start_i: int,
    end_i: int,
    confidence: float,
    description: str,
    key_levels: dict | None = None,
    trendlines: list | None = None,
) -> dict:
    return {
        "pattern": pattern,
        "start_date": _bar_label(df, start_i),
        "end_date": _bar_label(df, end_i),
        "confidence": round(float(np.clip(confidence, 0, 100)), 1),
        "description": description,
        "key_levels": key_levels or {},
        "trendlines": trendlines or [],
    }


def _vol_score(signal_vol: float, avg_vol: float) -> float:
    """0–100. Score increases linearly as volume exceeds the 20-day average."""
    if avg_vol <= 0:
        return 50.0
    ratio = signal_vol / avg_vol
    return float(np.clip((ratio - 1.0) * 100.0, 0, 100))


def _brk_score(move: float, reference: float) -> float:
    """0–100. Score based on how far price cleared the trigger level (pct * 20)."""
    if reference <= 0:
        return 0.0
    pct = abs(move) / reference * 100.0
    return float(np.clip(pct * 20.0, 0, 100))


def _touch_score(n_touches: int) -> float:
    """0–100 based on number of trendline touches."""
    return float(np.clip((n_touches - 1) * 20.0, 0, 100))


def _local_extrema(values: np.ndarray, order: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """Return arrays of local maxima and minima indices."""
    n = len(values)
    order = min(order, max(1, n // 5))
    maxima, minima = [], []
    for i in range(order, n - order):
        window = values[i - order : i + order + 1]
        if values[i] >= np.max(window) - 1e-9:
            maxima.append(i)
        if values[i] <= np.min(window) + 1e-9:
            minima.append(i)
    return np.array(maxima, dtype=int), np.array(minima, dtype=int)


def _seg(df: pd.DataFrame, i0: int, v0: float, i1: int, v1: float, label: str = "") -> dict:
    """Create a chart trendline segment: two time+price endpoints."""
    return {
        "time_start": _bar_label(df, i0),
        "time_end": _bar_label(df, i1),
        "value_start": round(float(v0), 4),
        "value_end": round(float(v1), 4),
        "label": label,
    }


def _dedup(results: list[dict], pattern_name: str) -> list[dict]:
    """Remove lower-confidence overlapping detections of the same pattern."""
    results.sort(key=lambda x: -x["confidence"])
    kept: list[dict] = []
    for r in results:
        s = pd.Timestamp(r["start_date"])
        e = pd.Timestamp(r["end_date"])
        overlaps = any(
            max(s, pd.Timestamp(k["start_date"])) < min(e, pd.Timestamp(k["end_date"]))
            for k in kept
        )
        if not overlaps:
            kept.append(r)
    return kept


# ─── Flag patterns ─────────────────────────────────────────────────────────────

def detect_bullish_flag(candles: list[dict], config: dict | None = None) -> list[dict]:
    cfg = {
        "min_pole_pct": 8.0,
        "max_pole_bars": 8,
        "min_consol_bars": 2,
        "max_consol_bars": 10,
        "max_range_pct": 3.0,
        **(config or {}),
    }
    df = _to_df(candles)
    n = len(df)
    results = []
    if n < 30:
        return results

    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values

    for pole_end in range(cfg["max_pole_bars"], n - cfg["max_consol_bars"] - 2):
        for pole_len in range(2, cfg["max_pole_bars"] + 1):
            pole_start = pole_end - pole_len
            if pole_start < 0:
                continue
            pole_low = lows[pole_start : pole_end + 1].min()
            pole_high = highs[pole_start : pole_end + 1].max()
            pole_pct = (pole_high - pole_low) / pole_low * 100
            if pole_pct < cfg["min_pole_pct"]:
                continue
            if closes[pole_end] < closes[pole_start] * 1.04:
                continue

            for consol_len in range(cfg["min_consol_bars"], cfg["max_consol_bars"] + 1):
                if pole_len + consol_len < 8:  # require ≥10 candles total
                    continue
                end_i = pole_end + consol_len
                if end_i >= n - 1:
                    break
                c_highs = highs[pole_end + 1 : end_i + 1]
                c_lows = lows[pole_end + 1 : end_i + 1]
                c_high = c_highs.max()
                c_range = (c_high - c_lows.min()) / pole_high * 100
                if c_range > cfg["max_range_pct"]:
                    continue

                # Downward sloping channel (negative slope on highs)
                if len(c_highs) >= 2:
                    slope = np.polyfit(np.arange(len(c_highs)), c_highs, 1)[0]
                    if slope > 0:
                        continue

                brk = end_i + 1
                if brk >= n:
                    break
                brk_close = closes[brk]
                if brk_close <= c_high:
                    continue

                b_score = _brk_score(brk_close - c_high, c_high)
                v_score = _vol_score(
                    df["volume"].iloc[brk], df["avg_vol_20"].iloc[brk]
                )
                touches = int(np.sum(np.abs(c_highs - c_high) / c_high < 0.008))
                t_score = _touch_score(touches)
                conf = 0.4 * b_score + 0.3 * v_score + 0.3 * t_score

                c_start_i = pole_end + 1
                tl = [
                    _seg(df, pole_start, float(pole_low), pole_end, float(pole_high), "pole"),
                    _seg(df, c_start_i, float(c_highs[0]), end_i, float(c_highs[-1]), "upper_channel"),
                    _seg(df, c_start_i, float(c_lows[0]), end_i, float(c_lows[-1]), "lower_channel"),
                ]
                results.append(
                    _result(
                        "Bullish Flag",
                        df,
                        pole_start,
                        brk,
                        conf,
                        f"Pole +{pole_pct:.1f}% over {pole_len} bars, "
                        f"{consol_len}-bar flag, breakout at {brk_close:.2f}",
                        {
                            "channel_high": round(float(c_high), 2),
                            "channel_low": round(float(c_lows.min()), 2),
                            "pole_high": round(float(pole_high), 2),
                        },
                        tl,
                    )
                )
    return _dedup(results, "Bullish Flag")


def detect_bearish_flag(candles: list[dict], config: dict | None = None) -> list[dict]:
    cfg = {
        "min_pole_pct": 8.0,
        "max_pole_bars": 8,
        "min_consol_bars": 2,
        "max_consol_bars": 10,
        "max_range_pct": 3.0,
        **(config or {}),
    }
    df = _to_df(candles)
    n = len(df)
    results = []
    if n < 30:
        return results

    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values

    for pole_end in range(cfg["max_pole_bars"], n - cfg["max_consol_bars"] - 2):
        for pole_len in range(2, cfg["max_pole_bars"] + 1):
            pole_start = pole_end - pole_len
            if pole_start < 0:
                continue
            pole_high = highs[pole_start : pole_end + 1].max()
            pole_low = lows[pole_start : pole_end + 1].min()
            pole_pct = (pole_high - pole_low) / pole_high * 100
            if pole_pct < cfg["min_pole_pct"]:
                continue
            if closes[pole_end] > closes[pole_start] * 0.96:
                continue

            for consol_len in range(cfg["min_consol_bars"], cfg["max_consol_bars"] + 1):
                if pole_len + consol_len < 8:  # require ≥10 candles total
                    continue
                end_i = pole_end + consol_len
                if end_i >= n - 1:
                    break
                c_highs = highs[pole_end + 1 : end_i + 1]
                c_lows = lows[pole_end + 1 : end_i + 1]
                c_low = c_lows.min()
                c_range = (c_highs.max() - c_low) / pole_low * 100
                if c_range > cfg["max_range_pct"]:
                    continue

                # Upward sloping channel (positive slope on lows)
                if len(c_lows) >= 2:
                    slope = np.polyfit(np.arange(len(c_lows)), c_lows, 1)[0]
                    if slope < 0:
                        continue

                brk = end_i + 1
                if brk >= n:
                    break
                brk_close = closes[brk]
                if brk_close >= c_low:
                    continue

                b_score = _brk_score(c_low - brk_close, c_low)
                v_score = _vol_score(df["volume"].iloc[brk], df["avg_vol_20"].iloc[brk])
                touches = int(np.sum(np.abs(c_lows - c_low) / c_low < 0.008))
                t_score = _touch_score(touches)
                conf = 0.4 * b_score + 0.3 * v_score + 0.3 * t_score

                c_start_i = pole_end + 1
                tl = [
                    _seg(df, pole_start, float(pole_high), pole_end, float(pole_low), "pole"),
                    _seg(df, c_start_i, float(c_highs[0]), end_i, float(c_highs[-1]), "upper_channel"),
                    _seg(df, c_start_i, float(c_lows[0]), end_i, float(c_lows[-1]), "lower_channel"),
                ]
                results.append(
                    _result(
                        "Bearish Flag",
                        df,
                        pole_start,
                        brk,
                        conf,
                        f"Pole -{pole_pct:.1f}% over {pole_len} bars, "
                        f"{consol_len}-bar flag, breakdown at {brk_close:.2f}",
                        {
                            "channel_high": round(float(c_highs.max()), 2),
                            "channel_low": round(float(c_low), 2),
                            "pole_low": round(float(pole_low), 2),
                        },
                        tl,
                    )
                )
    return _dedup(results, "Bearish Flag")


# ─── Pennant ───────────────────────────────────────────────────────────────────

def detect_bull_pennant(candles: list[dict], config: dict | None = None) -> list[dict]:
    cfg = {
        "min_pole_pct": 8.0,
        "max_pole_bars": 8,
        "min_consol_bars": 5,
        "max_consol_bars": 15,
        **(config or {}),
    }
    df = _to_df(candles)
    n = len(df)
    results = []
    if n < 30:
        return results

    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values

    for pole_end in range(cfg["max_pole_bars"], n - cfg["min_consol_bars"] - 2):
        for pole_len in range(2, cfg["max_pole_bars"] + 1):
            pole_start = pole_end - pole_len
            if pole_start < 0:
                continue
            pole_high = highs[pole_start : pole_end + 1].max()
            pole_low = lows[pole_start : pole_end + 1].min()
            pole_pct = (pole_high - pole_low) / pole_low * 100
            if pole_pct < cfg["min_pole_pct"]:
                continue
            if closes[pole_end] < closes[pole_start] * 1.04:
                continue

            for consol_len in range(cfg["min_consol_bars"], cfg["max_consol_bars"] + 1):
                if pole_len + consol_len < 8:  # require ≥10 candles total
                    continue
                end_i = pole_end + consol_len
                if end_i >= n - 1:
                    break
                seg_highs = highs[pole_end + 1 : end_i + 1]
                seg_lows = lows[pole_end + 1 : end_i + 1]
                if len(seg_highs) < 4:
                    continue

                x = np.arange(len(seg_highs))
                h_slope, h_int = np.polyfit(x, seg_highs, 1)
                l_slope, l_int = np.polyfit(x, seg_lows, 1)

                # Symmetrical triangle: upper trendline falls, lower rises
                if not (h_slope < 0 and l_slope > 0):
                    continue

                brk = end_i + 1
                if brk >= n:
                    break
                upper_at_brk = h_slope * len(seg_highs) + h_int
                brk_close = closes[brk]
                if brk_close <= upper_at_brk:
                    continue

                b_score = _brk_score(brk_close - upper_at_brk, upper_at_brk)
                v_score = _vol_score(df["volume"].iloc[brk], df["avg_vol_20"].iloc[brk])
                h_line = h_slope * x + h_int
                l_line = l_slope * x + l_int
                h_t = int(np.sum(np.abs(seg_highs - h_line) / h_line < 0.012))
                l_t = int(np.sum(np.abs(seg_lows - l_line) / l_line < 0.012))
                t_score = _touch_score(h_t + l_t)
                conf = 0.4 * b_score + 0.3 * v_score + 0.3 * t_score

                c_start_i = pole_end + 1
                x_end = len(seg_highs) - 1
                tl = [
                    _seg(df, pole_start, float(pole_low), pole_end, float(pole_high), "pole"),
                    _seg(df, c_start_i, float(h_int), end_i, float(h_slope * x_end + h_int), "upper_trendline"),
                    _seg(df, c_start_i, float(l_int), end_i, float(l_slope * x_end + l_int), "lower_trendline"),
                ]
                results.append(
                    _result(
                        "Bull Pennant",
                        df,
                        pole_start,
                        brk,
                        conf,
                        f"Pole +{pole_pct:.1f}%; {consol_len}-bar pennant; "
                        f"breakout at {brk_close:.2f}",
                        {
                            "pole_high": round(float(pole_high), 2),
                            "upper_trendline": round(float(upper_at_brk), 2),
                        },
                        tl,
                    )
                )
    return _dedup(results, "Bull Pennant")


# ─── Double patterns ───────────────────────────────────────────────────────────

def detect_double_bottom(candles: list[dict], config: dict | None = None) -> list[dict]:
    cfg = {
        "tolerance_pct": 3.0,
        "min_separation": 10,
        "max_separation": 50,
        "order": 4,
        **(config or {}),
    }
    df = _to_df(candles)
    n = len(df)
    results = []
    if n < 40:
        return results

    _, minima = _local_extrema(df["close"].values, order=cfg["order"])
    maxima, _ = _local_extrema(df["close"].values, order=cfg["order"])
    tol = cfg["tolerance_pct"] / 100.0

    for ia, a in enumerate(minima):
        for b in minima[ia + 1 :]:
            sep = b - a
            if sep < cfg["min_separation"] or sep > cfg["max_separation"]:
                continue
            va = df["close"].iloc[a]
            vb = df["close"].iloc[b]
            if abs(va - vb) / min(va, vb) > tol:
                continue

            # Neckline = highest peak between the two lows
            between = maxima[(maxima > a) & (maxima < b)]
            if len(between) == 0:
                neck_val = df["close"].iloc[a:b].max()
            else:
                ni = between[df["close"].iloc[between].values.argmax()]
                neck_val = df["close"].iloc[ni]

            # Find close above neckline after second bottom
            brk_found, brk = False, b + 1
            for look in range(b + 1, min(b + 15, n)):
                if df["close"].iloc[look] > neck_val:
                    brk, brk_found = look, True
                    break
            if not brk_found:
                continue

            brk_close = df["close"].iloc[brk]
            b_score = _brk_score(brk_close - neck_val, neck_val)
            v_score = _vol_score(df["volume"].iloc[brk], df["avg_vol_20"].iloc[brk])
            sym_score = float(np.clip((1 - abs(va - vb) / min(va, vb) / tol) * 100, 0, 100))
            conf = 0.4 * b_score + 0.3 * v_score + 0.3 * sym_score

            tl = [
                # Neckline (horizontal from first bottom to breakout)
                _seg(df, a, float(neck_val), brk, float(neck_val), "neckline"),
                # Left V side: descent to first bottom
                _seg(df, a, float(neck_val), a, float(va), "left_bottom"),
                # Connect two bottoms
                _seg(df, a, float(va), b, float(vb), "bottom_connect"),
                # Right V side: rise back to neckline
                _seg(df, b, float(vb), b, float(neck_val), "right_bottom"),
            ]
            results.append(
                _result(
                    "Double Bottom",
                    df,
                    a,
                    brk,
                    conf,
                    f"Lows at {va:.2f} and {vb:.2f} (~{sep} bars apart), "
                    f"neckline {neck_val:.2f}, breakout {brk_close:.2f}",
                    {
                        "bottom_1": round(float(va), 2),
                        "bottom_2": round(float(vb), 2),
                        "neckline": round(float(neck_val), 2),
                    },
                    tl,
                )
            )
    return _dedup(results, "Double Bottom")


def detect_double_top(candles: list[dict], config: dict | None = None) -> list[dict]:
    cfg = {
        "tolerance_pct": 3.0,
        "min_separation": 10,
        "max_separation": 50,
        "order": 4,
        **(config or {}),
    }
    df = _to_df(candles)
    n = len(df)
    results = []
    if n < 40:
        return results

    maxima, _ = _local_extrema(df["close"].values, order=cfg["order"])
    _, minima = _local_extrema(df["close"].values, order=cfg["order"])
    tol = cfg["tolerance_pct"] / 100.0

    for ia, a in enumerate(maxima):
        for b in maxima[ia + 1 :]:
            sep = b - a
            if sep < cfg["min_separation"] or sep > cfg["max_separation"]:
                continue
            va = df["close"].iloc[a]
            vb = df["close"].iloc[b]
            if abs(va - vb) / max(va, vb) > tol:
                continue

            between = minima[(minima > a) & (minima < b)]
            if len(between) == 0:
                neck_val = df["close"].iloc[a:b].min()
            else:
                ni = between[df["close"].iloc[between].values.argmin()]
                neck_val = df["close"].iloc[ni]

            brk_found, brk = False, b + 1
            for look in range(b + 1, min(b + 15, n)):
                if df["close"].iloc[look] < neck_val:
                    brk, brk_found = look, True
                    break
            if not brk_found:
                continue

            brk_close = df["close"].iloc[brk]
            b_score = _brk_score(neck_val - brk_close, neck_val)
            v_score = _vol_score(df["volume"].iloc[brk], df["avg_vol_20"].iloc[brk])
            sym_score = float(np.clip((1 - abs(va - vb) / max(va, vb) / tol) * 100, 0, 100))
            conf = 0.4 * b_score + 0.3 * v_score + 0.3 * sym_score

            tl = [
                # Neckline (horizontal from first top to breakout)
                _seg(df, a, float(neck_val), brk, float(neck_val), "neckline"),
                # Left Λ side: rise to first top
                _seg(df, a, float(neck_val), a, float(va), "left_top"),
                # Connect two tops
                _seg(df, a, float(va), b, float(vb), "top_connect"),
                # Right Λ side: fall back to neckline
                _seg(df, b, float(vb), b, float(neck_val), "right_top"),
            ]
            results.append(
                _result(
                    "Double Top",
                    df,
                    a,
                    brk,
                    conf,
                    f"Peaks at {va:.2f} and {vb:.2f} (~{sep} bars apart), "
                    f"neckline {neck_val:.2f}, breakdown {brk_close:.2f}",
                    {
                        "top_1": round(float(va), 2),
                        "top_2": round(float(vb), 2),
                        "neckline": round(float(neck_val), 2),
                    },
                    tl,
                )
            )
    return _dedup(results, "Double Top")


# ─── Head and Shoulders ────────────────────────────────────────────────────────

def detect_head_and_shoulders(candles: list[dict], config: dict | None = None) -> list[dict]:
    cfg = {
        "shoulder_tol_pct": 5.0,
        "min_bars": 15,
        "max_bars": 80,
        "order": 4,
        **(config or {}),
    }
    df = _to_df(candles)
    n = len(df)
    results = []
    if n < 40:
        return results

    maxima, minima = _local_extrema(df["close"].values, order=cfg["order"])
    if len(maxima) < 3 or len(minima) < 2:
        return results

    tol = cfg["shoulder_tol_pct"] / 100.0

    for i in range(len(maxima) - 2):
        ls_i, hd_i, rs_i = maxima[i], maxima[i + 1], maxima[i + 2]
        if rs_i - ls_i < cfg["min_bars"] or rs_i - ls_i > cfg["max_bars"]:
            continue

        ls_v = df["close"].iloc[ls_i]
        hd_v = df["close"].iloc[hd_i]
        rs_v = df["close"].iloc[rs_i]

        if not (hd_v > ls_v and hd_v > rs_v):
            continue
        if abs(ls_v - rs_v) / ls_v > tol:
            continue

        t1_c = minima[(minima > ls_i) & (minima < hd_i)]
        t2_c = minima[(minima > hd_i) & (minima < rs_i)]
        if len(t1_c) == 0 or len(t2_c) == 0:
            continue

        t1 = t1_c[df["close"].iloc[t1_c].values.argmin()]
        t2 = t2_c[df["close"].iloc[t2_c].values.argmin()]
        neckline = (df["close"].iloc[t1] + df["close"].iloc[t2]) / 2.0

        brk_found, brk = False, rs_i + 1
        for look in range(rs_i + 1, min(rs_i + 15, n)):
            if df["close"].iloc[look] < neckline:
                brk, brk_found = look, True
                break
        if not brk_found:
            continue

        brk_close = df["close"].iloc[brk]
        b_score = _brk_score(neckline - brk_close, neckline)
        v_score = _vol_score(df["volume"].iloc[brk], df["avg_vol_20"].iloc[brk])
        sym_score = float(np.clip((1 - abs(ls_v - rs_v) / ls_v / tol) * 100, 0, 100))
        conf = 0.4 * b_score + 0.3 * v_score + 0.3 * sym_score

        t1_v = float(df["close"].iloc[t1])
        t2_v = float(df["close"].iloc[t2])
        tl = [
            # Shape outline: ls → t1 → head → t2 → rs
            _seg(df, ls_i, float(ls_v), t1, t1_v, "ls_to_t1"),
            _seg(df, t1, t1_v, hd_i, float(hd_v), "t1_to_head"),
            _seg(df, hd_i, float(hd_v), t2, t2_v, "head_to_t2"),
            _seg(df, t2, t2_v, rs_i, float(rs_v), "t2_to_rs"),
            # Neckline connecting the two troughs
            _seg(df, t1, t1_v, t2, t2_v, "neckline"),
            # Neckline extension to breakout
            _seg(df, t2, float(neckline), brk, float(neckline), "neckline_ext"),
        ]
        results.append(
            _result(
                "Head and Shoulders",
                df,
                ls_i,
                brk,
                conf,
                f"L-shoulder {ls_v:.2f}, Head {hd_v:.2f}, R-shoulder {rs_v:.2f}, "
                f"neckline {neckline:.2f}, breakdown {brk_close:.2f}",
                {
                    "left_shoulder": round(float(ls_v), 2),
                    "head": round(float(hd_v), 2),
                    "right_shoulder": round(float(rs_v), 2),
                    "neckline": round(float(neckline), 2),
                },
                tl,
            )
        )
    return _dedup(results, "Head and Shoulders")


def detect_inverse_head_and_shoulders(candles: list[dict], config: dict | None = None) -> list[dict]:
    cfg = {
        "shoulder_tol_pct": 5.0,
        "min_bars": 15,
        "max_bars": 80,
        "order": 4,
        **(config or {}),
    }
    df = _to_df(candles)
    n = len(df)
    results = []
    if n < 40:
        return results

    maxima, minima = _local_extrema(df["close"].values, order=cfg["order"])
    if len(minima) < 3 or len(maxima) < 2:
        return results

    tol = cfg["shoulder_tol_pct"] / 100.0

    for i in range(len(minima) - 2):
        ls_i, hd_i, rs_i = minima[i], minima[i + 1], minima[i + 2]
        if rs_i - ls_i < cfg["min_bars"] or rs_i - ls_i > cfg["max_bars"]:
            continue

        ls_v = df["close"].iloc[ls_i]
        hd_v = df["close"].iloc[hd_i]
        rs_v = df["close"].iloc[rs_i]

        if not (hd_v < ls_v and hd_v < rs_v):
            continue
        if abs(ls_v - rs_v) / ls_v > tol:
            continue

        p1_c = maxima[(maxima > ls_i) & (maxima < hd_i)]
        p2_c = maxima[(maxima > hd_i) & (maxima < rs_i)]
        if len(p1_c) == 0 or len(p2_c) == 0:
            continue

        p1 = p1_c[df["close"].iloc[p1_c].values.argmax()]
        p2 = p2_c[df["close"].iloc[p2_c].values.argmax()]
        neckline = (df["close"].iloc[p1] + df["close"].iloc[p2]) / 2.0

        brk_found, brk = False, rs_i + 1
        for look in range(rs_i + 1, min(rs_i + 15, n)):
            if df["close"].iloc[look] > neckline:
                brk, brk_found = look, True
                break
        if not brk_found:
            continue

        brk_close = df["close"].iloc[brk]
        b_score = _brk_score(brk_close - neckline, neckline)
        v_score = _vol_score(df["volume"].iloc[brk], df["avg_vol_20"].iloc[brk])
        sym_score = float(np.clip((1 - abs(ls_v - rs_v) / ls_v / tol) * 100, 0, 100))
        conf = 0.4 * b_score + 0.3 * v_score + 0.3 * sym_score

        p1_v = float(df["close"].iloc[p1])
        p2_v = float(df["close"].iloc[p2])
        tl = [
            # Shape outline: ls → p1 → head → p2 → rs
            _seg(df, ls_i, float(ls_v), p1, p1_v, "ls_to_p1"),
            _seg(df, p1, p1_v, hd_i, float(hd_v), "p1_to_head"),
            _seg(df, hd_i, float(hd_v), p2, p2_v, "head_to_p2"),
            _seg(df, p2, p2_v, rs_i, float(rs_v), "p2_to_rs"),
            # Neckline connecting the two peaks
            _seg(df, p1, p1_v, p2, p2_v, "neckline"),
            # Neckline extension to breakout
            _seg(df, p2, float(neckline), brk, float(neckline), "neckline_ext"),
        ]
        results.append(
            _result(
                "Inverse Head and Shoulders",
                df,
                ls_i,
                brk,
                conf,
                f"L-shoulder {ls_v:.2f}, Head {hd_v:.2f}, R-shoulder {rs_v:.2f}, "
                f"neckline {neckline:.2f}, breakout {brk_close:.2f}",
                {
                    "left_shoulder": round(float(ls_v), 2),
                    "head": round(float(hd_v), 2),
                    "right_shoulder": round(float(rs_v), 2),
                    "neckline": round(float(neckline), 2),
                },
                tl,
            )
        )
    return _dedup(results, "Inverse Head and Shoulders")


# ─── Triangle patterns ─────────────────────────────────────────────────────────

def detect_ascending_triangle(candles: list[dict], config: dict | None = None) -> list[dict]:
    cfg = {
        "min_bars": 15,
        "max_bars": 60,
        "resistance_tol_pct": 1.5,
        "min_touches": 3,
        **(config or {}),
    }
    df = _to_df(candles)
    n = len(df)
    results = []
    if n < cfg["min_bars"] + 3:
        return results

    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    res_tol = cfg["resistance_tol_pct"] / 100.0

    for start in range(0, n - cfg["min_bars"] - 1, 3):
        for end in range(start + cfg["min_bars"], min(start + cfg["max_bars"], n - 1), 3):
            seg_h = highs[start : end + 1]
            seg_l = lows[start : end + 1]
            x = np.arange(len(seg_h))

            resistance = float(np.percentile(seg_h, 88))
            h_touches = int(np.sum(np.abs(seg_h - resistance) / resistance < res_tol))
            if h_touches < cfg["min_touches"]:
                continue

            l_slope, l_int = np.polyfit(x, seg_l, 1)
            if l_slope <= 0:
                continue

            l_fit = l_slope * x + l_int
            l_touches = int(np.sum(np.abs(seg_l - l_fit) / np.abs(l_fit) < 0.015))
            if l_touches < cfg["min_touches"]:
                continue

            brk = end + 1
            if brk >= n:
                continue
            brk_close = closes[brk]
            if brk_close <= resistance:
                continue

            b_score = _brk_score(brk_close - resistance, resistance)
            v_score = _vol_score(df["volume"].iloc[brk], df["avg_vol_20"].iloc[brk])
            t_score = _touch_score(h_touches + l_touches)
            conf = 0.4 * b_score + 0.3 * v_score + 0.3 * t_score

            seg_len = end - start
            tl = [
                # Flat resistance line
                _seg(df, start, float(resistance), end, float(resistance), "resistance"),
                # Rising support trendline
                _seg(df, start, float(l_int), end, float(l_slope * seg_len + l_int), "support"),
            ]
            results.append(
                _result(
                    "Ascending Triangle",
                    df,
                    start,
                    brk,
                    conf,
                    f"Flat resistance ~{resistance:.2f}, "
                    f"{h_touches + l_touches} trendline touches, breakout {brk_close:.2f}",
                    {
                        "resistance": round(resistance, 2),
                        "support_at_start": round(float(l_int), 2),
                    },
                    tl,
                )
            )
    return _dedup(results, "Ascending Triangle")


def detect_descending_triangle(candles: list[dict], config: dict | None = None) -> list[dict]:
    cfg = {
        "min_bars": 15,
        "max_bars": 60,
        "support_tol_pct": 1.5,
        "min_touches": 3,
        **(config or {}),
    }
    df = _to_df(candles)
    n = len(df)
    results = []
    if n < cfg["min_bars"] + 3:
        return results

    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    sup_tol = cfg["support_tol_pct"] / 100.0

    for start in range(0, n - cfg["min_bars"] - 1, 3):
        for end in range(start + cfg["min_bars"], min(start + cfg["max_bars"], n - 1), 3):
            seg_h = highs[start : end + 1]
            seg_l = lows[start : end + 1]
            x = np.arange(len(seg_l))

            support = float(np.percentile(seg_l, 12))
            l_touches = int(np.sum(np.abs(seg_l - support) / support < sup_tol))
            if l_touches < cfg["min_touches"]:
                continue

            h_slope, h_int = np.polyfit(x, seg_h, 1)
            if h_slope >= 0:
                continue

            h_fit = h_slope * x + h_int
            h_touches = int(np.sum(np.abs(seg_h - h_fit) / np.abs(h_fit) < 0.015))
            if h_touches < cfg["min_touches"]:
                continue

            brk = end + 1
            if brk >= n:
                continue
            brk_close = closes[brk]
            if brk_close >= support:
                continue

            b_score = _brk_score(support - brk_close, support)
            v_score = _vol_score(df["volume"].iloc[brk], df["avg_vol_20"].iloc[brk])
            t_score = _touch_score(l_touches + h_touches)
            conf = 0.4 * b_score + 0.3 * v_score + 0.3 * t_score

            seg_len = end - start
            tl = [
                # Flat support line
                _seg(df, start, float(support), end, float(support), "support"),
                # Descending resistance trendline
                _seg(df, start, float(h_int), end, float(h_slope * seg_len + h_int), "resistance"),
            ]
            results.append(
                _result(
                    "Descending Triangle",
                    df,
                    start,
                    brk,
                    conf,
                    f"Flat support ~{support:.2f}, "
                    f"{l_touches + h_touches} trendline touches, breakdown {brk_close:.2f}",
                    {
                        "support": round(support, 2),
                        "resistance_at_start": round(float(h_int), 2),
                    },
                    tl,
                )
            )
    return _dedup(results, "Descending Triangle")


# ─── Cup and Handle ────────────────────────────────────────────────────────────

def detect_cup_and_handle(candles: list[dict], config: dict | None = None) -> list[dict]:
    cfg = {
        "min_cup_bars": 20,
        "max_cup_bars": 60,
        "max_handle_pct": 15.0,
        "min_handle_bars": 3,
        "max_handle_bars": 15,
        "min_depth_pct": 8.0,
        **(config or {}),
    }
    df = _to_df(candles)
    n = len(df)
    results = []
    if n < cfg["min_cup_bars"] + cfg["max_handle_bars"] + 2:
        return results

    closes = df["close"].values
    lows = df["low"].values

    for cup_start in range(0, n - cfg["min_cup_bars"] - cfg["min_handle_bars"] - 1, 2):
        for cup_end in range(
            cup_start + cfg["min_cup_bars"],
            min(cup_start + cfg["max_cup_bars"], n - cfg["min_handle_bars"] - 1),
            2,
        ):
            lip_left = closes[cup_start]
            lip_right = closes[cup_end]
            cup_lip = max(lip_left, lip_right)

            # Lips must be roughly equal (U-shape, not V-shape)
            if abs(lip_left - lip_right) / cup_lip > 0.06:
                continue

            cup_bottom = lows[cup_start : cup_end + 1].min()
            depth_pct = (cup_lip - cup_bottom) / cup_lip * 100.0
            if depth_pct < cfg["min_depth_pct"]:
                continue

            # Bottom should be in middle 60% of the cup
            cup_len = cup_end - cup_start
            bot_idx = int(lows[cup_start : cup_end + 1].argmin())
            if bot_idx < cup_len * 0.2 or bot_idx > cup_len * 0.8:
                continue

            for handle_end in range(
                cup_end + cfg["min_handle_bars"],
                min(cup_end + cfg["max_handle_bars"] + 1, n - 1),
            ):
                handle_seg = lows[cup_end + 1 : handle_end + 1]
                handle_low = float(handle_seg.min())
                pullback_pct = (cup_lip - handle_low) / cup_lip * 100.0
                if pullback_pct > cfg["max_handle_pct"]:
                    continue

                # Handle should trend downward (the pullback)
                h_closes = closes[cup_end + 1 : handle_end + 1]
                if len(h_closes) >= 2:
                    slope = np.polyfit(np.arange(len(h_closes)), h_closes, 1)[0]
                    if slope > 0:
                        continue

                brk = handle_end + 1
                if brk >= n:
                    break
                brk_close = closes[brk]
                if brk_close <= cup_lip:
                    continue

                b_score = _brk_score(brk_close - cup_lip, cup_lip)
                v_score = _vol_score(df["volume"].iloc[brk], df["avg_vol_20"].iloc[brk])
                roundness = float(np.clip(100 - abs(lip_left - lip_right) / cup_lip * 1000, 0, 100))
                conf = 0.4 * b_score + 0.3 * v_score + 0.3 * roundness

                bot_abs_i = cup_start + int(lows[cup_start : cup_end + 1].argmin())
                tl = [
                    # Cup lip (horizontal resistance from cup start to breakout)
                    _seg(df, cup_start, float(cup_lip), brk, float(cup_lip), "cup_lip"),
                    # Left cup wall: lip down to bottom
                    _seg(df, cup_start, float(cup_lip), bot_abs_i, float(cup_bottom), "left_wall"),
                    # Right cup wall: bottom back up to lip
                    _seg(df, bot_abs_i, float(cup_bottom), cup_end, float(cup_lip), "right_wall"),
                    # Handle channel
                    _seg(df, cup_end + 1, float(cup_lip), handle_end, float(handle_low), "handle"),
                ]
                results.append(
                    _result(
                        "Cup and Handle",
                        df,
                        cup_start,
                        brk,
                        conf,
                        f"Cup depth {depth_pct:.1f}%, handle pullback {pullback_pct:.1f}%, "
                        f"breakout above cup lip {cup_lip:.2f}",
                        {
                            "cup_lip": round(float(cup_lip), 2),
                            "cup_bottom": round(float(cup_bottom), 2),
                            "handle_low": round(float(handle_low), 2),
                        },
                        tl,
                    )
                )
    return _dedup(results, "Cup and Handle")


# ─── Wedge patterns ────────────────────────────────────────────────────────────

def detect_rising_wedge(candles: list[dict], config: dict | None = None) -> list[dict]:
    cfg = {
        "min_bars": 15,
        "max_bars": 50,
        "min_touches": 2,
        **(config or {}),
    }
    df = _to_df(candles)
    n = len(df)
    results = []
    if n < cfg["min_bars"] + 2:
        return results

    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values

    for start in range(0, n - cfg["min_bars"] - 1, 2):
        for end in range(start + cfg["min_bars"], min(start + cfg["max_bars"], n - 1), 2):
            seg_h = highs[start : end + 1]
            seg_l = lows[start : end + 1]
            x = np.arange(len(seg_h))

            h_slope, h_int = np.polyfit(x, seg_h, 1)
            l_slope, l_int = np.polyfit(x, seg_l, 1)

            # Both trendlines must be rising, lower rising faster (converging)
            if not (h_slope > 0 and l_slope > 0 and l_slope > h_slope):
                continue

            h_fit = h_slope * x + h_int
            l_fit = l_slope * x + l_int
            h_t = int(np.sum(np.abs(seg_h - h_fit) / h_fit < 0.015))
            l_t = int(np.sum(np.abs(seg_l - l_fit) / l_fit < 0.015))
            if h_t < cfg["min_touches"] or l_t < cfg["min_touches"]:
                continue

            brk = end + 1
            if brk >= n:
                continue
            lower_at_brk = l_slope * len(seg_l) + l_int
            brk_close = closes[brk]
            if brk_close >= lower_at_brk:
                continue

            b_score = _brk_score(lower_at_brk - brk_close, lower_at_brk)
            v_score = _vol_score(df["volume"].iloc[brk], df["avg_vol_20"].iloc[brk])
            t_score = _touch_score(h_t + l_t)
            conf = 0.4 * b_score + 0.3 * v_score + 0.3 * t_score

            seg_len = end - start
            tl = [
                _seg(df, start, float(h_int), end, float(h_slope * seg_len + h_int), "upper"),
                _seg(df, start, float(l_int), end, float(l_slope * seg_len + l_int), "lower"),
            ]
            results.append(
                _result(
                    "Rising Wedge",
                    df,
                    start,
                    brk,
                    conf,
                    f"Both trendlines rising (converging), breakdown below "
                    f"lower trendline at {brk_close:.2f}",
                    {
                        "upper_trendline": round(float(h_slope * len(seg_h) + h_int), 2),
                        "lower_trendline": round(float(lower_at_brk), 2),
                    },
                    tl,
                )
            )
    return _dedup(results, "Rising Wedge")


def detect_falling_wedge(candles: list[dict], config: dict | None = None) -> list[dict]:
    cfg = {
        "min_bars": 15,
        "max_bars": 50,
        "min_touches": 2,
        **(config or {}),
    }
    df = _to_df(candles)
    n = len(df)
    results = []
    if n < cfg["min_bars"] + 2:
        return results

    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values

    for start in range(0, n - cfg["min_bars"] - 1, 2):
        for end in range(start + cfg["min_bars"], min(start + cfg["max_bars"], n - 1), 2):
            seg_h = highs[start : end + 1]
            seg_l = lows[start : end + 1]
            x = np.arange(len(seg_h))

            h_slope, h_int = np.polyfit(x, seg_h, 1)
            l_slope, l_int = np.polyfit(x, seg_l, 1)

            # Both trendlines falling, upper falling faster (converging)
            if not (h_slope < 0 and l_slope < 0 and h_slope < l_slope):
                continue

            h_fit = h_slope * x + h_int
            l_fit = l_slope * x + l_int
            h_t = int(np.sum(np.abs(seg_h - h_fit) / h_fit < 0.015))
            l_t = int(np.sum(np.abs(seg_l - l_fit) / l_fit < 0.015))
            if h_t < cfg["min_touches"] or l_t < cfg["min_touches"]:
                continue

            brk = end + 1
            if brk >= n:
                continue
            upper_at_brk = h_slope * len(seg_h) + h_int
            brk_close = closes[brk]
            if brk_close <= upper_at_brk:
                continue

            b_score = _brk_score(brk_close - upper_at_brk, upper_at_brk)
            v_score = _vol_score(df["volume"].iloc[brk], df["avg_vol_20"].iloc[brk])
            t_score = _touch_score(h_t + l_t)
            conf = 0.4 * b_score + 0.3 * v_score + 0.3 * t_score

            seg_len = end - start
            tl = [
                _seg(df, start, float(h_int), end, float(h_slope * seg_len + h_int), "upper"),
                _seg(df, start, float(l_int), end, float(l_slope * seg_len + l_int), "lower"),
            ]
            results.append(
                _result(
                    "Falling Wedge",
                    df,
                    start,
                    brk,
                    conf,
                    f"Both trendlines falling (converging), breakout above "
                    f"upper trendline at {brk_close:.2f}",
                    {
                        "upper_trendline": round(float(upper_at_brk), 2),
                        "lower_trendline": round(float(l_slope * len(seg_l) + l_int), 2),
                    },
                    tl,
                )
            )
    return _dedup(results, "Falling Wedge")


# ─── Registry ──────────────────────────────────────────────────────────────────

PATTERN_DETECTORS: dict[str, callable] = {
    "Bullish Flag": detect_bullish_flag,
    "Bearish Flag": detect_bearish_flag,
    "Bull Pennant": detect_bull_pennant,
    "Double Bottom": detect_double_bottom,
    "Double Top": detect_double_top,
    "Head and Shoulders": detect_head_and_shoulders,
    "Inverse Head and Shoulders": detect_inverse_head_and_shoulders,
    "Ascending Triangle": detect_ascending_triangle,
    "Descending Triangle": detect_descending_triangle,
    "Cup and Handle": detect_cup_and_handle,
    "Rising Wedge": detect_rising_wedge,
    "Falling Wedge": detect_falling_wedge,
}

BULLISH_PATTERNS = {
    "Bullish Flag",
    "Bull Pennant",
    "Double Bottom",
    "Inverse Head and Shoulders",
    "Ascending Triangle",
    "Cup and Handle",
    "Falling Wedge",
}
