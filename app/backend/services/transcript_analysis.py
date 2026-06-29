"""Earnings-transcript analysis + extraction.

Three input paths (text, URL, PDF) converge on one LLM analysis that returns a
9-section structured read: sentiment vs prior quarter, tone delta, key themes
with quotes, hedging-language flags, dodged questions, competitive + regulatory
mentions, an explicit thesis-impact verdict, and watch-next-quarter items.

The analysis prompt is adapted from the-terminal but rewritten to reference
this repo's sleeve/thesis concepts. Extraction uses pypdf (PDF) and
httpx + BeautifulSoup (HTML) instead of unpdf/Tavily.
"""

from __future__ import annotations

import io
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

MIN_TRANSCRIPT_CHARS = 500


class TranscriptError(RuntimeError):
    """Raised when extraction fails or input is too short to analyze."""


# ─── Extraction ──────────────────────────────────────────────────────────────


def _normalize_pdf_text(text: str) -> str:
    text = re.sub(r"-\n", "", text)            # join hyphenated line breaks
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pdf_bytes(data: bytes) -> str:
    """Extract text from PDF bytes with pypdf. Raises if it looks scanned."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # noqa: BLE001
        raise TranscriptError(f"Could not read PDF: {exc}") from exc
    text = _normalize_pdf_text("\n".join(pages))
    if len(text) < MIN_TRANSCRIPT_CHARS:
        raise TranscriptError(
            "Extracted under 500 characters — this looks like a scanned/image PDF "
            "that needs OCR. Paste the transcript text manually instead."
        )
    return text


_UA = "Mozilla/5.0 (compatible; AlphaEngine/1.0; +https://localhost)"


def extract_from_url(url: str) -> str:
    """Fetch a URL and extract transcript text.

    PDF URLs are parsed with pypdf; HTML pages are stripped with BeautifulSoup
    (drops script/style/nav/footer, joins paragraph text).
    """
    import httpx

    try:
        resp = httpx.get(
            url,
            headers={"user-agent": _UA, "accept": "text/html,application/pdf,*/*"},
            timeout=30.0,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise TranscriptError(f"Could not fetch the URL: {exc}") from exc

    content_type = resp.headers.get("content-type", "").lower()
    is_pdf = url.lower().split("?")[0].endswith(".pdf") or "application/pdf" in content_type
    if is_pdf:
        return extract_pdf_bytes(resp.content)

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        tag.decompose()
    # Prefer paragraph text; fall back to the whole body.
    paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    text = "\n\n".join(p for p in paragraphs if p) or soup.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < MIN_TRANSCRIPT_CHARS:
        raise TranscriptError(
            "Could not extract enough text from that page. Try the Motley Fool / "
            "Seeking Alpha transcript page, or paste the text manually."
        )
    return text


# ─── Analysis ────────────────────────────────────────────────────────────────

_TRANSCRIPT_SYSTEM = (
    "You are a senior equity analyst breaking down an earnings call transcript for "
    "a discretionary portfolio manager who runs several portfolios of positions. Your reader "
    "is sophisticated — do not explain industry mechanics, just flag and interpret "
    "what's signal vs noise. Adapt your sector focus to whatever industry the "
    "company operates in.\n\n"
    "Extract structured information from the transcript. Be terse, specific, and "
    "quote exact language whenever possible.\n\n"
    "Output JSON ONLY — no prose, no markdown fences. Schema:\n\n"
    "{\n"
    '  "sentimentScore": <-10..+10 integer — vs prior quarter if provided, else absolute 0=neutral>,\n'
    '  "toneDelta": "1-2 sentences. More cautious / more confident / similar, with exact phrases that show it.",\n'
    '  "keyThemes": [{"topic": "short label", "quote": "<15-word representative quote", "bookRelevance": "high|medium|low"}],\n'
    '  "guidanceLanguage": "Exact forward-guidance language. Flag hedging (\'approximately\', \'subject to\', \'assuming\', \'we expect, if...\') vs confident language. Cite the words.",\n'
    '  "dodgedQuestions": [{"analyst": "firm/name or \'Analyst\'", "question": "1-line paraphrase", "pivot": "what management said instead", "importance": "high|medium|low"}],\n'
    '  "competitiveMentions": [{"competitor": "name", "context": "what was said and why it matters", "signal": "bullish|bearish|neutral"}],\n'
    '  "policyMentions": [{"topic": "IRA 45X / FEOC / tariff / etc.", "quote": "exact words", "interpretation": "what it implies for the position"}],\n'
    '  "thesisImpact": {"direction": "confirms|strengthens|weakens|breaks", "narrative": "1 paragraph citing the deciding evidence"},\n'
    '  "watchNextQuarter": ["2-3 specific things to monitor"]\n'
    "}\n\n"
    "Rules:\n"
    "- Include the top 3-5 key themes (not 10+); the PM wants signal density.\n"
    "- Dodged questions are HIGH value — include any analyst question that got a non-answer or pivot.\n"
    "- If prior-quarter analysis is provided, sentimentScore is the delta vs that; else absolute tone.\n"
    "- Quotes must be verbatim from the transcript; do NOT paraphrase quotes."
)


def _clamp_int(v: Any, lo: int, hi: int, default: int = 0) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def _norm_enum(v: Any, allowed: set[str], default: str) -> str:
    return v if isinstance(v, str) and v in allowed else default


def _parse_analysis(raw: dict[str, Any]) -> dict[str, Any]:
    """Defensively clamp/normalize the model output into the stable schema."""
    themes = []
    for t in (raw.get("keyThemes") or [])[:8]:
        if isinstance(t, dict):
            themes.append(
                {
                    "topic": str(t.get("topic", ""))[:120],
                    "quote": str(t.get("quote", ""))[:300],
                    "bookRelevance": _norm_enum(t.get("bookRelevance"), {"high", "medium", "low"}, "medium"),
                }
            )
    dodged = []
    for d in (raw.get("dodgedQuestions") or [])[:10]:
        if isinstance(d, dict):
            dodged.append(
                {
                    "analyst": str(d.get("analyst", "Analyst"))[:80],
                    "question": str(d.get("question", ""))[:300],
                    "pivot": str(d.get("pivot", ""))[:300],
                    "importance": _norm_enum(d.get("importance"), {"high", "medium", "low"}, "medium"),
                }
            )
    competitive = []
    for c in (raw.get("competitiveMentions") or [])[:10]:
        if isinstance(c, dict):
            competitive.append(
                {
                    "competitor": str(c.get("competitor", ""))[:120],
                    "context": str(c.get("context", ""))[:300],
                    "signal": _norm_enum(c.get("signal"), {"bullish", "bearish", "neutral"}, "neutral"),
                }
            )
    policy = []
    for p in (raw.get("policyMentions") or [])[:10]:
        if isinstance(p, dict):
            policy.append(
                {
                    "topic": str(p.get("topic", ""))[:120],
                    "quote": str(p.get("quote", ""))[:300],
                    "interpretation": str(p.get("interpretation", ""))[:300],
                }
            )
    impact = raw.get("thesisImpact") or {}
    return {
        "sentimentScore": _clamp_int(raw.get("sentimentScore"), -10, 10, 0),
        "toneDelta": str(raw.get("toneDelta", ""))[:500],
        "keyThemes": themes,
        "guidanceLanguage": str(raw.get("guidanceLanguage", ""))[:800],
        "dodgedQuestions": dodged,
        "competitiveMentions": competitive,
        "policyMentions": policy,
        "thesisImpact": {
            "direction": _norm_enum(
                impact.get("direction") if isinstance(impact, dict) else None,
                {"confirms", "strengthens", "weakens", "breaks"},
                "confirms",
            ),
            "narrative": str(impact.get("narrative", "") if isinstance(impact, dict) else "")[:1000],
        },
        "watchNextQuarter": [str(w)[:200] for w in (raw.get("watchNextQuarter") or [])[:5]],
    }


def analyze_transcript(
    *, ticker: str, transcript: str, current_thesis: str | None = None, report_date: str | None = None
) -> dict[str, Any]:
    """Run the single-shot LLM analysis over a transcript and return the schema."""
    import os as _os

    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    from app.backend.services.key_resolver import resolve_key

    if len(transcript.strip()) < MIN_TRANSCRIPT_CHARS:
        raise TranscriptError("Transcript is too short to analyze (need 500+ characters).")

    user_lines = [
        f"TICKER: {ticker}",
        f"AS OF: {report_date or 'unspecified'}",
        "",
        "CURRENT THESIS:",
        (current_thesis or "(no thesis on record)").strip(),
        "",
        "TRANSCRIPT:",
        transcript[:60000],  # cap to keep within context budget
        "",
        "Produce the structured JSON analysis now.",
    ]
    user = "\n".join(user_lines)

    llm = ChatOpenAI(
        model="deepseek-chat",
        openai_api_key=(resolve_key("deepseek") or ""),
        openai_api_base="https://api.deepseek.com/v1",
        temperature=0.2,
        max_tokens=3000,
    )
    try:
        resp = llm.invoke(
            [SystemMessage(content=_TRANSCRIPT_SYSTEM), HumanMessage(content=user)]
        )
        txt = (resp.content or "").strip()
        start, end = txt.find("{"), txt.rfind("}")
        raw = json.loads(txt[start : end + 1]) if start >= 0 and end > start else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Transcript analysis failed for %s: %s", ticker, exc)
        raise TranscriptError(f"Analysis failed: {exc}") from exc

    result = _parse_analysis(raw)
    result["ticker"] = ticker.upper()
    result["report_date"] = report_date
    return result
