"""DB-backed P&L position persistence — the Postgres replacement for the file
store in ``pnl_service`` (``app/data/pnl_positions.json``).

This is a pure persistence layer: id generation, timestamps, and the P&L math
(``summarize``, multipliers, realized/unrealized) stay in ``pnl_service``; the
repository just stores and returns position dicts in the exact file shape
(no ``user_id`` leaks into the returned dict).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.backend.database.app_models import DEFAULT_USER_ID, PnlPosition

# Columns that make up a position's public shape (everything except user_id).
_COLS = (
    "id", "kind", "ticker", "side", "qty", "option", "entry_price", "entry_date",
    "status", "exit_price", "exit_date", "source", "real", "notes",
    "import_key", "closing_import_key", "created_at", "updated_at",
)


class PnlRepository:
    def __init__(self, db: Session, user_id: str = DEFAULT_USER_ID):
        self.db = db
        self.user_id = user_id

    def _query(self):
        return self.db.query(PnlPosition).filter(PnlPosition.user_id == self.user_id)

    def _get(self, position_id: str) -> PnlPosition | None:
        return self._query().filter(PnlPosition.id == position_id).first()

    @staticmethod
    def _to_dict(p: PnlPosition) -> dict[str, Any]:
        return {col: getattr(p, col) for col in _COLS}

    def get_all(self) -> list[dict[str, Any]]:
        rows = self._query().order_by(PnlPosition.created_at, PnlPosition.id).all()
        return [self._to_dict(p) for p in rows]

    def get(self, position_id: str) -> dict[str, Any] | None:
        p = self._get(position_id)
        return self._to_dict(p) if p else None

    def insert(self, record: dict[str, Any]) -> dict[str, Any]:
        """Persist a fully-formed position record (id + timestamps already set
        by the service)."""
        p = PnlPosition(user_id=self.user_id, **{c: record.get(c) for c in _COLS})
        self.db.add(p)
        self.db.commit()
        self.db.refresh(p)
        return self._to_dict(p)

    def bulk_insert(self, records: list[dict[str, Any]]) -> int:
        for record in records:
            self.db.add(PnlPosition(user_id=self.user_id, **{c: record.get(c) for c in _COLS}))
        self.db.commit()
        return len(records)

    def update(self, position_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        p = self._get(position_id)
        if p is None:
            return None
        for col, value in fields.items():
            if col in _COLS and col != "id":
                setattr(p, col, value)
        self.db.commit()
        self.db.refresh(p)
        return self._to_dict(p)

    def delete(self, position_id: str) -> bool:
        p = self._get(position_id)
        if p is None:
            return False
        self.db.delete(p)
        self.db.commit()
        return True

    def existing_import_keys(self) -> set[str]:
        """All import keys in use (open + closing), for CSV dedupe."""
        keys: set[str] = set()
        for p in self._query().all():
            if p.import_key:
                keys.add(p.import_key)
            if p.closing_import_key:
                keys.add(p.closing_import_key)
        return keys
