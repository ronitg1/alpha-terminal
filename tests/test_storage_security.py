"""Regression tests for the /storage/save-json path sanitizer.

Pins the fix for the arbitrary-file-write hole: the client-supplied filename must
be a plain ``*.json`` basename that resolves inside ``outputs/`` — path
separators, ``..``, absolute paths, and non-json names are rejected with a 400.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.backend.routes.storage import _safe_output_path


def test_accepts_plain_json_basename(tmp_path: Path) -> None:
    out = _safe_output_path("2026-07-14_morning_scan.json", tmp_path)
    assert out.parent == tmp_path.resolve()
    assert out.name == "2026-07-14_morning_scan.json"


@pytest.mark.parametrize(
    "bad",
    [
        "../secrets.json",          # parent traversal
        "../../etc/cron.json",      # deeper traversal
        "/etc/passwd.json",         # absolute path
        "sub/dir/file.json",        # nested path
        "scan.txt",                 # not .json
        "scan.json.txt",            # not .json
        "",                         # empty
    ],
)
def test_rejects_unsafe_filenames(bad: str, tmp_path: Path) -> None:
    with pytest.raises(HTTPException) as ei:
        _safe_output_path(bad, tmp_path)
    assert ei.value.status_code == 400
