"""Tests for the chat news-trigger heuristic.

Live news is only worth fetching when the user is asking about a catalyst or a
recent move. These tests pin which phrasings trip the fetch so we don't quietly
start hitting the news API on every mechanical question.
"""

from __future__ import annotations

import pytest

from app.backend.routes.sleeves import _question_wants_news


@pytest.mark.parametrize(
    "text",
    [
        "why did NVDA drop today?",
        "any news on this name?",
        "what happened to the stock recently",
        "did they report earnings yet?",
        "was there an upgrade or downgrade?",
        "what's the latest catalyst here",
    ],
)
def test_news_questions_trigger_fetch(text: str) -> None:
    assert _question_wants_news(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "what is the P/E ratio?",
        "how much debt does it carry",
        "summarize the balance sheet",
        "what's the dividend yield",
        "is this a good long-term hold based on fundamentals",
    ],
)
def test_mechanical_questions_skip_fetch(text: str) -> None:
    assert _question_wants_news(text) is False
