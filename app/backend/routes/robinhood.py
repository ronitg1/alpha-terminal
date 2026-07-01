from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from app.backend.models.schemas import ErrorResponse
from app.backend.services.robinhood_mcp import (
    RobinhoodMcpAuthRequired,
    RobinhoodMcpError,
    RobinhoodMcpToolNotFound,
    fetch_portfolio,
)

router = APIRouter(prefix="/robinhood", tags=["robinhood"])
logger = logging.getLogger(__name__)
_MCP_FAILURE_DETAIL = "Robinhood MCP request failed. Check your token and try again."


@router.get(
    "/portfolio",
    responses={
        200: {"description": "Robinhood portfolio payload from MCP"},
        400: {"model": ErrorResponse, "description": "Missing authorization or no portfolio tool"},
        502: {"model": ErrorResponse, "description": "Robinhood MCP error"},
    },
)
async def get_robinhood_portfolio() -> dict[str, Any]:
    try:
        return await fetch_portfolio()
    except RobinhoodMcpAuthRequired as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RobinhoodMcpToolNotFound as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "message": str(exc),
                "available_tools": exc.tool_names,
            },
        )
    except (RobinhoodMcpError, httpx.HTTPError) as exc:
        logger.warning("Robinhood MCP request failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail=_MCP_FAILURE_DETAIL)
