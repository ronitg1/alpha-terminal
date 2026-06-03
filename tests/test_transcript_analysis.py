"""Tests for transcript parsing/normalization (no network, no LLM)."""

from __future__ import annotations

import pytest

from app.backend.services.transcript_analysis import (
    MIN_TRANSCRIPT_CHARS,
    TranscriptError,
    _normalize_pdf_text,
    _parse_analysis,
    extract_pdf_bytes,
)


def test_parse_clamps_and_normalizes() -> None:
    raw = {
        "sentimentScore": 42,  # out of range → clamp to 10
        "toneDelta": "more cautious",
        "keyThemes": [
            {"topic": "ASP pressure", "quote": "prices are falling", "bookRelevance": "WRONG"},
            *[{"topic": f"t{i}", "quote": "q", "bookRelevance": "high"} for i in range(12)],
        ],
        "dodgedQuestions": [{"analyst": "MS", "question": "margins?", "pivot": "we feel good", "importance": "x"}],
        "competitiveMentions": [{"competitor": "Chinese modules", "context": "pricing war", "signal": "??"}],
        "thesisImpact": {"direction": "invalid", "narrative": "n"},
        "watchNextQuarter": [f"w{i}" for i in range(9)],
    }
    out = _parse_analysis(raw)
    assert out["sentimentScore"] == 10                      # clamped
    assert out["keyThemes"][0]["bookRelevance"] == "medium"  # normalized enum
    assert len(out["keyThemes"]) == 8                        # capped at 8
    assert out["dodgedQuestions"][0]["importance"] == "medium"
    assert out["competitiveMentions"][0]["signal"] == "neutral"
    assert out["thesisImpact"]["direction"] == "confirms"    # invalid → default
    assert len(out["watchNextQuarter"]) == 5                 # capped at 5


def test_parse_handles_empty_input() -> None:
    out = _parse_analysis({})
    assert out["sentimentScore"] == 0
    assert out["keyThemes"] == []
    assert out["thesisImpact"]["direction"] == "confirms"


def test_normalize_pdf_joins_hyphenation() -> None:
    assert "management" in _normalize_pdf_text("manage-\nment guidance")
    assert "  " not in _normalize_pdf_text("a    b\t\tc")


def test_extract_pdf_rejects_garbage() -> None:
    with pytest.raises(TranscriptError):
        extract_pdf_bytes(b"not a real pdf")


def test_min_chars_constant() -> None:
    assert MIN_TRANSCRIPT_CHARS == 500
