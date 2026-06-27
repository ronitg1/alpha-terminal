"""DB-backed thesis persistence — the Postgres replacement for ``thesis_store``
(``app/data/theses.json``).

``key`` is the scope string ('portfolio' | 'sleeve:<name>' |
'ticker:<SYM>:<depth>'); ``payload`` is the full thesis dict (including its own
``saved_at``, added by the service before calling). ``get_all`` returns
``{key: payload}`` exactly like the file store.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.backend.database.app_models import DEFAULT_USER_ID, Thesis


class ThesisRepository:
    def __init__(self, db: Session, user_id: str = DEFAULT_USER_ID):
        self.db = db
        self.user_id = user_id

    def _query(self):
        return self.db.query(Thesis).filter(Thesis.user_id == self.user_id)

    def get_all(self) -> dict[str, Any]:
        # Copy each payload so callers can't mutate the ORM-attached dict.
        return {t.key: dict(t.payload) for t in self._query().all()}

    def get(self, key: str) -> dict[str, Any] | None:
        row = self._query().filter(Thesis.key == key).first()
        return dict(row.payload) if row else None

    def upsert(self, key: str, payload: dict[str, Any]) -> None:
        row = self._query().filter(Thesis.key == key).first()
        if row is None:
            row = Thesis(user_id=self.user_id, key=key, payload=payload)
            self.db.add(row)
        else:
            row.payload = payload
        self.db.commit()
