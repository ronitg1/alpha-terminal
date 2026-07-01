"""13F ownership / flow tracker — smart-money moves in the names you follow.

For a curated set of well-known institutions we pull their two most recent 13F-HR
filings from SEC EDGAR, parse the holdings, and diff quarter-over-quarter. For each
ticker the user follows we then report which of those funds hold it and whether they
opened / added / trimmed / exited last quarter.

Notes / limitations:
  • 13F is QUARTERLY and lagged ~45 days after quarter-end — this is slow-moving.
  • Filings report CUSIP + issuer name, not tickers (CUSIP↔ticker is licensed data),
    so we match by NORMALISED issuer name against the ticker's company name. That's
    high-precision for distinctive names and simply misses the ambiguous ones.
  • EDGAR requires a descriptive User-Agent and rate-limits; everything is cached a
    day (13F data doesn't change intraday).
"""
from __future__ import annotations

import logging
import re
import threading
import time
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

# (display name, CIK) — famous, widely-followed managers.
_INSTITUTIONS: list[tuple[str, str]] = [
    ("Berkshire Hathaway", "0001067983"),
    ("Bridgewater", "0001350694"),
    ("Renaissance Tech", "0001037389"),
    ("Citadel Advisors", "0001423053"),
    ("Pershing Square (Ackman)", "0001336528"),
    ("Appaloosa (Tepper)", "0001006438"),
    ("Scion (Burry)", "0001649339"),
    ("Tiger Global", "0001167483"),
]

_UA = {"User-Agent": "AlphaTerminal 13F research (ganguly.ronit@gmail.com)"}
_SUFFIX_RE = re.compile(
    r"\b(INC|CORP|CORPORATION|CO|COMPANY|LTD|LLC|PLC|HLDGS?|HOLDINGS?|GROUP|CLASS|CL|"
    r"COM|COMMON|STK|CAP|SHS?|SER|A|B|C|THE|NEW|ADR|SP|SPON|NV|SA|AG)\b",
    re.I,
)

_filings_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_name_cache: dict[str, str] = {}
_cache_lock = threading.Lock()
_TTL = 24 * 3600.0


def _get(url: str) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=25).read()


def _normalize(name: str) -> str:
    n = re.sub(r"[^A-Z0-9 ]", " ", (name or "").upper())
    n = _SUFFIX_RE.sub(" ", n)
    return re.sub(r"\s+", " ", n).strip()


def _match(company_norm: str, issuer_norm: str) -> bool:
    """High-precision: one normalized name is a prefix of the other (min 4 chars)."""
    if len(company_norm) < 4 or len(issuer_norm) < 4:
        return False
    return issuer_norm.startswith(company_norm) or company_norm.startswith(issuer_norm)


def _latest_two_13f(cik: str) -> list[dict[str, Any]]:
    """The two most recent 13F-HR filings' holdings, aggregated by issuer name:
    ``[{period, holdings: {norm_name: {name, shares, value}}}]`` (newest first)."""
    import json

    cache_key = cik
    with _cache_lock:
        hit = _filings_cache.get(cache_key)
        if hit is not None and (time.monotonic() - hit[0]) < _TTL:
            return hit[1]

    out: list[dict[str, Any]] = []
    try:
        sub = json.loads(_get(f"https://data.sec.gov/submissions/CIK{cik}.json"))
        rec = sub["filings"]["recent"]
        idxs = [i for i, f in enumerate(rec["form"]) if f == "13F-HR"][:2]
        cik_int = str(int(cik))
        for i in idxs:
            acc = rec["accessionNumber"][i].replace("-", "")
            base = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/"
            try:
                idx = json.loads(_get(base + "index.json"))
                files = [it["name"] for it in idx["directory"]["item"]]
                info = next((f for f in files if f.lower().endswith(".xml") and "primary" not in f.lower()), None)
                if not info:
                    continue
                xml = _get(base + info).decode("utf-8", "ignore")
            except Exception as exc:  # noqa: BLE001
                logger.debug("13F info table fetch failed for %s: %s", cik, exc)
                continue
            holdings: dict[str, dict[str, Any]] = {}
            for e in re.findall(r"<(?:\w+:)?infoTable>(.*?)</(?:\w+:)?infoTable>", xml, re.S):
                nm = re.search(r"nameOfIssuer>(.*?)<", e)
                sh = re.search(r"sshPrnamt>(.*?)<", e)
                val = re.search(r"value>(.*?)<", e)
                if not nm:
                    continue
                name = nm.group(1).strip()
                key = _normalize(name)
                if not key:
                    continue
                agg = holdings.setdefault(key, {"name": name, "shares": 0, "value": 0})
                try:
                    agg["shares"] += int(sh.group(1)) if sh else 0
                    agg["value"] += int(val.group(1)) if val else 0
                except (TypeError, ValueError):
                    pass
            out.append({"period": rec["filingDate"][i], "holdings": holdings})
    except Exception as exc:  # noqa: BLE001
        logger.warning("13F fetch failed for CIK %s: %s", cik, type(exc).__name__)

    with _cache_lock:
        _filings_cache[cache_key] = (time.monotonic(), out)
    return out


def _company_name(sym: str) -> str:
    if sym in _name_cache:
        return _name_cache[sym]
    name = ""
    try:
        from src.tools.finnhub.client import FinnhubClient, is_finnhub_configured

        if is_finnhub_configured():
            name = (FinnhubClient().company_profile(sym).get("name") or "").strip()
    except Exception:  # noqa: BLE001
        pass
    _name_cache[sym] = name
    return name


def build_ownership(tickers: list[str]) -> dict[str, Any]:
    """Per-ticker: which tracked funds hold it and their last-quarter change."""
    syms = [t.strip().upper() for t in tickers if t.strip()][:25]
    if not syms:
        return {"names": [], "institutions": [n for n, _ in _INSTITUTIONS]}

    # Pull each institution's latest two filings once (cached).
    filings = {name: _latest_two_13f(cik) for name, cik in _INSTITUTIONS}

    results = []
    for sym in syms:
        cnorm = _normalize(_company_name(sym) or sym)
        if not cnorm:
            continue
        holders = []
        for inst, quarters in filings.items():
            if not quarters:
                continue
            cur = quarters[0]["holdings"]
            prev = quarters[1]["holdings"] if len(quarters) > 1 else {}
            cur_h = next((v for k, v in cur.items() if _match(cnorm, k)), None)
            prev_h = next((v for k, v in prev.items() if _match(cnorm, k)), None)
            cur_sh = cur_h["shares"] if cur_h else 0
            prev_sh = prev_h["shares"] if prev_h else 0
            if cur_sh == 0 and prev_sh == 0:
                continue
            if cur_sh > 0 and prev_sh == 0:
                change = "new"
            elif cur_sh == 0 and prev_sh > 0:
                change = "exited"
            elif cur_sh > prev_sh * 1.02:
                change = "added"
            elif cur_sh < prev_sh * 0.98:
                change = "trimmed"
            else:
                change = "held"
            delta_pct = None
            if prev_sh > 0:
                delta_pct = round((cur_sh - prev_sh) / prev_sh * 100, 0)
            holders.append({
                "institution": inst,
                "shares": cur_sh,
                "prev_shares": prev_sh,
                "value": (cur_h or prev_h or {}).get("value"),
                "change": change,
                "delta_pct": delta_pct,
            })
        if holders:
            # Most interesting first: new/exited/added/trimmed before held.
            order = {"new": 0, "exited": 1, "added": 2, "trimmed": 3, "held": 4}
            holders.sort(key=lambda h: (order.get(h["change"], 9), -(h["shares"] or 0)))
            results.append({"ticker": sym, "holders": holders})

    return {"names": results, "institutions": [n for n, _ in _INSTITUTIONS]}
