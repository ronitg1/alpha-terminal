"""Task-aware DeepSeek model router.

The agents in this project run two flavors of LLM work:

* **Reasoning** — long chain-of-thought analysis (Damodaran valuation walks,
  Burry contrarian theses, custom alpha-seeker variant-perception writeups).
  These benefit from `deepseek-reasoner` (R1), which is slower but produces
  better structured arguments.

* **Parsing / cheap** — extracting JSON from a fixed schema, summarizing a
  news headline, normalizing a ticker. These should run on `deepseek-chat`
  (V3) which is ~10x cheaper and faster.

`pick_model(task)` returns the (model_name, provider) tuple expected by
`src.llm.models.get_model`. Helper `call_with_backoff` wraps a callable in
exponential backoff with jitter — DeepSeek rate-limits aggressively under
load and a single retry is not enough.
"""
from __future__ import annotations

import logging
import random
import time
from enum import Enum
from typing import Callable, TypeVar

from src.llm.models import ModelProvider

logger = logging.getLogger(__name__)

# ─── Model identifiers ────────────────────────────────────────────────────────
# DeepSeek's API names. R1 = deepseek-reasoner, V3 = deepseek-chat.
DEEPSEEK_REASONER = "deepseek-reasoner"
DEEPSEEK_CHAT = "deepseek-chat"


class TaskType(str, Enum):
    """Coarse classification of LLM work to route to the right DeepSeek model."""

    # Heavy chain-of-thought: investment theses, variant perception, valuation walks.
    REASONING = "reasoning"
    # Structured-output extraction: parsing JSON from a known schema.
    PARSING = "parsing"
    # Quick natural-language tasks: summarization, normalization, headline scoring.
    CHEAP = "cheap"


def pick_model(task: TaskType) -> tuple[str, str]:
    """Return ``(model_name, provider)`` for a given task type.

    The provider string matches ``ModelProvider`` enum values so it plugs
    straight into the existing `get_model` factory.
    """
    if task is TaskType.REASONING:
        return DEEPSEEK_REASONER, ModelProvider.DEEPSEEK.value
    return DEEPSEEK_CHAT, ModelProvider.DEEPSEEK.value


# ─── Retry / backoff ──────────────────────────────────────────────────────────

T = TypeVar("T")

# Default backoff schedule (in seconds): 1, 2, 4, 8, 16, with ±25% jitter.
_DEFAULT_BASE_DELAY = 1.0
_DEFAULT_MAX_DELAY = 30.0
_DEFAULT_MAX_ATTEMPTS = 5


def call_with_backoff(
    fn: Callable[[], T],
    *,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    base_delay: float = _DEFAULT_BASE_DELAY,
    max_delay: float = _DEFAULT_MAX_DELAY,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    label: str = "deepseek",
) -> T:
    """Call ``fn`` with exponential backoff + jitter on transient errors.

    Re-raises the last exception after ``max_attempts``. Logs each retry so
    failures are visible without enabling DEBUG.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except retry_on as exc:
            if attempt == max_attempts:
                logger.error("%s: giving up after %d attempts: %s", label, attempt, exc)
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            jitter = delay * 0.25
            sleep_for = delay + random.uniform(-jitter, jitter)
            logger.warning(
                "%s: attempt %d/%d failed (%s); sleeping %.2fs",
                label, attempt, max_attempts, exc.__class__.__name__, sleep_for,
            )
            time.sleep(max(0.0, sleep_for))
    # Unreachable — the loop either returns or re-raises.
    raise RuntimeError("call_with_backoff exited loop without returning")
