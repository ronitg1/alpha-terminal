"""Persist a scan's JSON payload under the project ``outputs/`` directory.

The filename comes from the client, so it is sanitized to a bare ``*.json``
basename and the resolved path is confirmed to stay inside ``outputs/`` — an
un-sanitized ``outputs_dir / filename`` would let ``../`` or an absolute path
escape the directory and write anywhere the process can (arbitrary file write).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.backend.models.schemas import ErrorResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/storage")


class SaveJsonRequest(BaseModel):
    filename: str
    data: dict


def _safe_output_path(filename: str, outputs_dir: Path) -> Path:
    """Resolve ``filename`` to a path guaranteed to live directly inside
    ``outputs_dir``. Rejects path separators, ``..``, absolute paths, and any
    non-``.json`` name with a 400."""
    name = Path(filename).name
    if not name or name != filename or not name.endswith(".json"):
        raise HTTPException(
            status_code=400,
            detail="filename must be a plain '.json' basename (no path separators).",
        )
    resolved = (outputs_dir / name).resolve()
    if resolved.parent != outputs_dir.resolve():
        raise HTTPException(status_code=400, detail="Invalid filename.")
    return resolved


@router.post(
    path="/save-json",
    responses={
        200: {"description": "File saved successfully"},
        400: {"model": ErrorResponse, "description": "Invalid request parameters"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def save_json_file(request: SaveJsonRequest) -> dict:
    """Save JSON data to the project's ``outputs/`` directory (validated filename)."""
    project_root = Path(__file__).parent.parent.parent.parent
    outputs_dir = project_root / "outputs"
    outputs_dir.mkdir(exist_ok=True)
    file_path = _safe_output_path(request.filename, outputs_dir)
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(request.data, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        logger.warning("Failed to save %s: %s", file_path.name, type(exc).__name__)
        raise HTTPException(status_code=500, detail="Failed to save file.")
    return {"success": True, "message": "File saved.", "filename": file_path.name}
