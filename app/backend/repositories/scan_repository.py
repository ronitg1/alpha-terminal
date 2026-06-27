"""DB-backed scan-result persistence — the Postgres replacement for the
``outputs/YYYY-MM-DD_morning_scan.json`` sidecars.

One row per (user, date); ``payload`` is the full UI scan blob
(``{date, rows, ...}``). This stores the JSON the UI reads; the CSV remains a
CLI-only artifact and is out of scope for the DB layer.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.backend.database.app_models import DEFAULT_USER_ID, ScanResult


class ScanRepository:
    def __init__(self, db: Session, user_id: str = DEFAULT_USER_ID):
        self.db = db
        self.user_id = user_id

    def _query(self):
        return self.db.query(ScanResult).filter(ScanResult.user_id == self.user_id)

    def get(self, scan_date: str) -> dict[str, Any] | None:
        row = self._query().filter(ScanResult.scan_date == scan_date).first()
        return dict(row.payload) if row else None

    def list_dates(self, limit: int | None = None) -> list[str]:
        """Scan dates, newest first."""
        q = self._query().order_by(ScanResult.scan_date.desc())
        if limit:
            q = q.limit(limit)
        return [r.scan_date for r in q.all()]

    def list_scans(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Scan list entries shaped like the file route's ``/scans`` response:
        ``{date, path, size_bytes}``. There's no file on disk, so ``path`` is a
        synthetic ``db://`` reference and ``size_bytes`` is None — the route
        should drop/ignore those at cutover."""
        return [
            {"date": d, "path": f"db://scan/{d}", "size_bytes": None}
            for d in self.list_dates(limit)
        ]

    def latest(self) -> dict[str, Any] | None:
        row = self._query().order_by(ScanResult.scan_date.desc()).first()
        return dict(row.payload) if row else None

    def upsert(self, scan_date: str, payload: dict[str, Any]) -> dict[str, Any]:
        row = self._query().filter(ScanResult.scan_date == scan_date).first()
        if row is None:
            row = ScanResult(user_id=self.user_id, scan_date=scan_date, payload=payload)
            self.db.add(row)
        else:
            row.payload = payload
        self.db.commit()
        return payload
