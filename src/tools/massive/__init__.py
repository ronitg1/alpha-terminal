"""Massive (Polygon.io) data backend.

This package implements the subset of `src.tools.api` interfaces required by
the agents, sourced from the Massive REST API. Conversions to the
financialdatasets.ai-shape pydantic models live in :mod:`converters`.

Public entry points are exported here so callers do not need to know which
submodule a helper lives in.
"""
from src.tools.massive.client import MassiveClient, MassiveError
from src.tools.massive.converters import (
    convert_company_facts,
    convert_company_news,
    convert_financial_metrics,
    convert_line_items,
    convert_prices,
)

__all__ = [
    "MassiveClient",
    "MassiveError",
    "convert_company_facts",
    "convert_company_news",
    "convert_financial_metrics",
    "convert_line_items",
    "convert_prices",
]
