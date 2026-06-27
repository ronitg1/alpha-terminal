"""DB-backed watchlist persistence — the Postgres replacement for
``watchlists_service`` (``app/data/watchlists.json``).

Returns the same ``{name, tickers}`` dicts the file service returns (tickers are
``[{ticker, comment}]``), so it's drop-in at cutover. Every operation is scoped
to ``user_id`` (defaults to the single pre-auth owner).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.backend.database.app_models import DEFAULT_USER_ID, Watchlist


class WatchlistRepository:
    """CRUD for a user's named watchlists."""

    def __init__(self, db: Session, user_id: str = DEFAULT_USER_ID):
        self.db = db
        self.user_id = user_id

    def _query(self):
        return self.db.query(Watchlist).filter(Watchlist.user_id == self.user_id)

    def _get(self, name: str) -> Watchlist | None:
        return self._query().filter(Watchlist.name == name).first()

    @staticmethod
    def _to_dict(w: Watchlist) -> dict[str, Any]:
        return {"name": w.name, "tickers": list(w.entries or [])}

    def get_all(self) -> list[dict[str, Any]]:
        """All watchlists for this user, in creation order."""
        return [self._to_dict(w) for w in self._query().order_by(Watchlist.id).all()]

    def get_one(self, name: str) -> dict[str, Any] | None:
        w = self._get(name)
        return self._to_dict(w) if w else None

    def upsert(self, name: str, tickers: list[dict[str, Any]]) -> dict[str, Any]:
        """Create or replace the watchlist with this name."""
        w = self._get(name)
        if w:
            w.entries = tickers
        else:
            w = Watchlist(user_id=self.user_id, name=name, entries=tickers)
            self.db.add(w)
        self.db.commit()
        self.db.refresh(w)
        return self._to_dict(w)

    def rename(self, old_name: str, new_name: str) -> bool:
        """Rename a watchlist. Returns True if found, False if not. Raises
        ValueError if ``new_name`` is already taken."""
        w = self._get(old_name)
        if not w:
            return False
        if old_name != new_name and self._get(new_name) is not None:
            raise ValueError(f"A watchlist named '{new_name}' already exists.")
        w.name = new_name
        self.db.commit()
        return True

    def delete(self, name: str) -> bool:
        """Delete a watchlist by name. Returns True if found, False otherwise."""
        w = self._get(name)
        if not w:
            return False
        self.db.delete(w)
        self.db.commit()
        return True
