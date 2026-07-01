"""Unified cross-brokerage portfolio overview for the Portfolio tab.

  GET /portfolio/overview — merged accounts (SnapTrade + Robinhood) + an
  "All combined" aggregate, enriched with quotes and display metrics.

Returns ``connected: False`` (and empty accounts) when the user has no brokerage
connected, so the frontend can show a connect prompt instead of an error.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

from app.backend.services import portfolio_overview

router = APIRouter(prefix="/portfolio", tags=["portfolio"])
logger = logging.getLogger(__name__)


@router.get("/overview")
async def get_overview() -> dict[str, Any]:
    """The current user's portfolio across all connected brokerages."""
    return await portfolio_overview.build_overview()
