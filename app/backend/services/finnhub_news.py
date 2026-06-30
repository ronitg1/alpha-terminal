"""Market News service — Finnhub-primary news with Polygon fallback.

Provides the three feeds the Market News tab needs:
  * per-ticker company news (book / sector headlines + ticker search)
  * a macro feed auto-categorized into six buckets via keyword regex
  * a per-article "3 bullets + why it matters to my book" LLM summary

Finnhub is primary (the user has a key); Polygon ``get_company_news`` is the
fallback so per-ticker news still works if Finnhub is unconfigured. The macro
feed requires Finnhub (Polygon has no general-market endpoint).
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

# ─── Macro categorization (ported verbatim from the-terminal) ────────────────

MACRO_CATEGORIES = ["monetary", "geopolitics", "government", "economy", "energy", "markets"]

_EARNINGS_NOISE_RE = re.compile(
    r"\b(earnings|results|q[1-4](?:\s*'?\d{2})?|quarterly|eps|guidance|beats?|misses?|"
    r"reports?\s+(profit|loss|revenue))\b",
    re.IGNORECASE,
)

_MACRO_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "monetary",
        re.compile(
            r"\b(fed|federal reserve|fomc|powell|cpi|ppi|inflation|interest rate|rate cut|"
            r"rate hike|rate decision|treasury yield|bond yield|yield curve|monetary policy|"
            r"disinflation)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "geopolitics",
        re.compile(
            r"\b(china|xi jinping|beijing|russia|putin|ukraine|kyiv|moscow|israel|gaza|iran|"
            r"tehran|sanctions|nato|taiwan|north korea|hamas|hezbollah|red sea|houthis|war)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "government",
        re.compile(
            r"\b(congress|senate|house of representatives|biden|trump|white house|debt ceiling|"
            r"federal budget|government shutdown|executive order|treasury department|tariff|"
            r"trade deal|export controls|capitol)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "economy",
        re.compile(
            r"\b(gdp|jobs report|payroll|nonfarm|unemployment|jobless claims|ism|pmi|retail sales|"
            r"consumer confidence|recession|housing starts|durable goods|industrial production)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "energy",
        re.compile(
            r"\b(opec\+?|crude|oil price|brent|wti|gasoline|natural gas|saudi arabia|oil supply|"
            r"oil demand|spr release|strategic petroleum)\b",
            re.IGNORECASE,
        ),
    ),
]


def categorize_macro(text: str) -> str:
    """First-match-wins keyword categorization; default bucket is 'markets'."""
    for category, pattern in _MACRO_PATTERNS:
        if pattern.search(text):
            return category
    return "markets"


def is_earnings_noise(text: str) -> bool:
    """True for company-results stories we strip from the macro feed."""
    return bool(_EARNINGS_NOISE_RE.search(text))


# ─── Article normalization ───────────────────────────────────────────────────


def _normalize_finnhub(article: dict[str, Any], related: str | None = None) -> dict[str, Any] | None:
    headline = article.get("headline")
    url = article.get("url")
    if not headline or not url:
        return None
    return {
        "id": str(article.get("id") or url),
        "headline": headline,
        "summary": article.get("summary") or "",
        "source": article.get("source") or "",
        "url": url,
        "datetime": int(article.get("datetime") or 0),
        "image": article.get("image") or None,
        "related": related or article.get("related") or None,
        "category": article.get("category") or None,
    }


def _normalize_polygon(item: Any, related: str | None = None) -> dict[str, Any] | None:
    title = getattr(item, "title", None)
    url = getattr(item, "url", None)
    if not title or not url:
        return None
    # Polygon ships an ISO date; convert to a unix timestamp for uniform sorting.
    ts = 0
    date_str = getattr(item, "date", None)
    if date_str:
        try:
            import datetime as _dt

            ts = int(_dt.datetime.fromisoformat(date_str.replace("Z", "+00:00")).timestamp())
        except (ValueError, TypeError):
            ts = 0
    return {
        "id": url,
        "headline": title,
        "summary": "",
        "source": getattr(item, "source", "") or "",
        "url": url,
        "datetime": ts,
        "image": None,
        "related": related or getattr(item, "ticker", None),
        "category": None,
    }


def _company_news(ticker: str, *, hours_back: int) -> list[dict[str, Any]]:
    """Per-ticker news: Finnhub primary, Polygon fallback. Normalized + sorted."""
    import datetime as _dt

    from src.tools.finnhub import get_finnhub_client

    end = _dt.date.today()
    start = end - _dt.timedelta(hours=hours_back) if hours_back < 24 else end - _dt.timedelta(
        days=max(1, hours_back // 24)
    )

    client = get_finnhub_client()
    out: list[dict[str, Any]] = []
    if client is not None:
        try:
            raw = client.company_news(
                ticker, start_date=start.isoformat(), end_date=end.isoformat()
            )
            out = [a for a in (_normalize_finnhub(r, related=ticker) for r in raw) if a]
        except Exception as exc:  # noqa: BLE001
            logger.info("Finnhub company news failed for %s: %s", ticker, exc)

    if not out:
        try:
            from src.tools.api import get_company_news

            raw_p = get_company_news(
                ticker, end_date=end.isoformat(), start_date=start.isoformat(), limit=20
            )
            out = [a for a in (_normalize_polygon(n, related=ticker) for n in raw_p) if a]
        except Exception as exc:  # noqa: BLE001
            logger.info("Polygon company news fallback failed for %s: %s", ticker, exc)

    out.sort(key=lambda a: a["datetime"], reverse=True)
    return out


# ─── Feed assembly ───────────────────────────────────────────────────────────

MAX_FEED_TICKERS = 8
MAX_PER_TICKER = 5
MAX_MACRO_ARTICLES = 60


def build_feed(tickers: list[str], *, hours_back: int = 168) -> dict[str, Any]:
    """Assemble book headlines (fanned across tickers) + the macro feed."""
    seen_urls: set[str] = set()
    book: list[dict[str, Any]] = []
    for t in tickers[:MAX_FEED_TICKERS]:
        for art in _company_news(t, hours_back=hours_back)[:MAX_PER_TICKER]:
            if art["url"] in seen_urls:
                continue
            seen_urls.add(art["url"])
            book.append(art)
    book.sort(key=lambda a: a["datetime"], reverse=True)

    macro = _macro_feed(hours_back=hours_back)
    counts: dict[str, int] = {c: 0 for c in MACRO_CATEGORIES}
    for a in macro:
        counts[a["category"]] = counts.get(a["category"], 0) + 1

    return {
        "configured": True,
        "book_headlines": book,
        "macro": macro,
        "macro_category_counts": counts,
        "generated_at": time.time(),
    }


def _macro_feed(*, hours_back: int) -> list[dict[str, Any]]:
    """Finnhub general-market news, earnings-stripped, categorized, capped."""
    from src.tools.finnhub import get_finnhub_client

    client = get_finnhub_client()
    if client is None:
        return []
    try:
        raw = client.market_news("general")
    except Exception as exc:  # noqa: BLE001
        logger.info("Finnhub macro news failed: %s", exc)
        return []

    cutoff = time.time() - hours_back * 3600
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in raw:
        art = _normalize_finnhub(r)
        if not art or art["url"] in seen:
            continue
        if art["datetime"] and art["datetime"] < cutoff:
            continue
        haystack = f"{art['headline']} {art['summary']}"
        if is_earnings_noise(haystack):
            continue
        seen.add(art["url"])
        art["category"] = categorize_macro(haystack)
        out.append(art)
    out.sort(key=lambda a: a["datetime"], reverse=True)
    return out[:MAX_MACRO_ARTICLES]


def ticker_feed(ticker: str, *, hours_back: int = 168) -> list[dict[str, Any]]:
    """News for a single searched ticker."""
    return _company_news(ticker.upper(), hours_back=hours_back)[:20]


# ─── Per-article summary ─────────────────────────────────────────────────────

_NEWS_SUMMARIZE_SYSTEM = (
    "You are an analyst summarizing news for a discretionary investor running real "
    "risk. The investor's book is organized into portfolios of tickers — adapt your "
    "relevance lens to whatever sectors the related ticker (or macro topic) touches.\n\n"
    "Output JSON ONLY — no prose, no fences:\n"
    "{\n"
    '  "summary": ["3 bullets, single sentence each. Focus on the WHAT and the '
    'WHY-it-matters. Skip headlines they already know."],\n'
    '  "relevance": "high" | "medium" | "low",\n'
    '  "relevanceReason": "1 sentence: why does this matter (or not) to this '
    'investor\'s book?"\n'
    "}\n\n"
    "Be terse. Skip generic disclaimers."
)


def summarize_article(
    *, title: str, description: str, related: str | None, sleeve: str | None
) -> dict[str, Any]:
    import json as _json

    from langchain_core.messages import HumanMessage, SystemMessage

    from app.backend.services.llm_preferences import create_selected_chat_model

    book_line = (
        f"RELATED TICKER: {related}"
        + (f" (held in the user's {sleeve} sleeve)" if sleeve else "")
        if related
        else "RELATED TICKER: none — macro/sector article"
    )
    user_lines = [book_line, f"ARTICLE TITLE: {title}"]
    if description:
        user_lines.append(f"DESCRIPTION: {description}")
    user_lines.append("\nProduce the JSON summary now.")
    user = "\n".join(user_lines)

    llm = create_selected_chat_model(temperature=0.3, max_tokens=500)
    try:
        resp = llm.invoke(
            [SystemMessage(content=_NEWS_SUMMARIZE_SYSTEM), HumanMessage(content=user)]
        )
        txt = (resp.content or "").strip()
        start, end = txt.find("{"), txt.rfind("}")
        data = _json.loads(txt[start : end + 1]) if start >= 0 and end > start else {}
        summary = data.get("summary") or []
        if isinstance(summary, str):
            summary = [summary]
        return {
            "summary": [str(s) for s in summary][:5],
            "relevance": data.get("relevance", "medium"),
            "relevanceReason": data.get("relevanceReason", ""),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("News summarize failed: %s", exc)
        return {
            "summary": ["Could not generate a summary - check the LLM connection."],
            "relevance": "low",
            "relevanceReason": "",
        }
