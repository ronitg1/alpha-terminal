"""Earnings Transcripts routes — paste text, paste URL, or upload a PDF.

All three input modes converge on POST /transcripts/analyze, which runs the
single-shot LLM analysis. URL/PDF extraction live in their own endpoints so the
frontend can show the extracted text before analyzing.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.backend.services import transcript_analysis as ta

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transcripts")

MAX_PDF_BYTES = 20 * 1024 * 1024  # 20 MB


class _ExtractUrlRequest(BaseModel):
    url: str


@router.post("/extract-url")
async def extract_url(req: _ExtractUrlRequest) -> dict[str, Any]:
    """Fetch a URL (HTML or PDF) and return the extracted transcript text."""
    if not req.url.strip():
        raise HTTPException(status_code=400, detail="URL is required.")
    try:
        text = await asyncio.to_thread(ta.extract_from_url, req.url.strip())
    except ta.TranscriptError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"text": text, "chars": len(text)}


@router.post("/upload")
async def upload_pdf(file: UploadFile = File(...)) -> dict[str, Any]:
    """Extract transcript text from an uploaded PDF."""
    data = await file.read()
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="PDF exceeds the 20 MB limit.")
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")
    try:
        text = await asyncio.to_thread(ta.extract_pdf_bytes, data)
    except ta.TranscriptError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"text": text, "chars": len(text), "filename": file.filename}


class _AnalyzeRequest(BaseModel):
    ticker: str = ""
    transcript: str
    current_thesis: str | None = None
    report_date: str | None = None


@router.post("/analyze")
async def analyze(req: _AnalyzeRequest) -> dict[str, Any]:
    """Run the 9-section transcript analysis."""
    if not req.transcript or len(req.transcript.strip()) < ta.MIN_TRANSCRIPT_CHARS:
        raise HTTPException(
            status_code=400,
            detail="Transcript is too short to analyze (need 500+ characters).",
        )
    try:
        return await asyncio.to_thread(
            ta.analyze_transcript,
            ticker=req.ticker or "—",
            transcript=req.transcript,
            current_thesis=req.current_thesis,
            report_date=req.report_date,
        )
    except ta.TranscriptError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
