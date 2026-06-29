"""DB-backed portfolio (sleeve) persistence — the Postgres replacement for
``sleeve_config_service`` + the ``PORTFOLIO_SLEEVES`` / ``CASH_RESERVE_PCT``
module globals in ``src/config/portfolio_config.py``.

``read_sleeves`` returns the same ``{name: {allocation_pct, agents,
agent_weights, tickers}}`` shape the config module exposes, so it's drop-in for
the scan engine and the routes. Mutations raise ``ValueError`` (conflict) /
``LookupError`` (missing) for the calling layer to translate to HTTP errors —
the repository stays HTTP-agnostic.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.backend.database.app_models import (
    DEFAULT_CASH_RESERVE_PCT,
    DEFAULT_USER_ID,
    Portfolio,
    UserSettings,
)


class PortfolioRepository:
    """CRUD for a user's sleeves plus the cash-reserve setting."""

    def __init__(self, db: Session, user_id: str = DEFAULT_USER_ID):
        self.db = db
        self.user_id = user_id

    def _query(self):
        return self.db.query(Portfolio).filter(Portfolio.user_id == self.user_id)

    def _get(self, name: str) -> Portfolio | None:
        return self._query().filter(Portfolio.name == name).first()

    @staticmethod
    def _to_dict(p: Portfolio) -> dict[str, Any]:
        return {
            "allocation_pct": p.allocation_pct,
            "agents": list(p.agents or []),
            "agent_weights": dict(p.agent_weights or {}),
            "tickers": list(p.tickers or []),
        }

    # ─── reads ──────────────────────────────────────────────────────────────

    def read_sleeves(self) -> dict[str, dict[str, Any]]:
        """All sleeves as ``{name: sleeve_dict}`` in creation order."""
        return {p.name: self._to_dict(p) for p in self._query().order_by(Portfolio.id).all()}

    def get_cash_reserve(self) -> float:
        row = self.db.query(UserSettings).filter(UserSettings.user_id == self.user_id).first()
        return row.cash_reserve_pct if row else DEFAULT_CASH_RESERVE_PCT

    def get_onboarding_completed(self) -> bool:
        row = self.db.query(UserSettings).filter(UserSettings.user_id == self.user_id).first()
        return bool(row.onboarding_completed) if row else False

    # ─── mutations ──────────────────────────────────────────────────────────

    def _apply(self, p: Portfolio, sleeve: dict[str, Any]) -> None:
        p.allocation_pct = float(sleeve.get("allocation_pct", 0.0))
        p.agents = list(sleeve.get("agents", []))
        p.agent_weights = dict(sleeve.get("agent_weights", {}))
        p.tickers = list(sleeve.get("tickers", []))

    def create_sleeve(self, name: str, sleeve: dict[str, Any]) -> dict[str, dict[str, Any]]:
        if self._get(name) is not None:
            raise ValueError(f"A portfolio named '{name}' already exists.")
        p = Portfolio(user_id=self.user_id, name=name)
        self._apply(p, sleeve)
        self.db.add(p)
        self.db.commit()
        return self.read_sleeves()

    def update_sleeve(self, name: str, sleeve: dict[str, Any]) -> dict[str, dict[str, Any]]:
        p = self._get(name)
        if p is None:
            raise LookupError(f"No portfolio named '{name}'.")
        self._apply(p, sleeve)
        self.db.commit()
        return self.read_sleeves()

    def delete_sleeve(self, name: str) -> dict[str, dict[str, Any]]:
        p = self._get(name)
        if p is None:
            raise LookupError(f"No portfolio named '{name}'.")
        if self._query().count() <= 1:
            raise ValueError("Cannot delete the last portfolio.")
        self.db.delete(p)
        self.db.commit()
        return self.read_sleeves()

    def rename_sleeve(self, old_name: str, new_name: str) -> dict[str, dict[str, Any]]:
        p = self._get(old_name)
        if p is None:
            raise LookupError(f"No portfolio named '{old_name}'.")
        if old_name != new_name and self._get(new_name) is not None:
            raise ValueError(f"A portfolio named '{new_name}' already exists.")
        p.name = new_name
        self.db.commit()
        return self.read_sleeves()

    def replace_all_sleeves(self, sleeves: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Atomically replace this user's entire sleeve set."""
        # Bulk delete bypasses the unit of work; flush it before inserting the
        # replacements so the DELETE lands before the INSERTs and can't collide
        # with the per-user unique name constraint on Postgres.
        self._query().delete(synchronize_session=False)
        self.db.flush()
        for name, sleeve in sleeves.items():
            p = Portfolio(user_id=self.user_id, name=name)
            self._apply(p, sleeve)
            self.db.add(p)
        self.db.commit()
        return self.read_sleeves()

    def set_cash_reserve(self, pct: float) -> float:
        row = self.db.query(UserSettings).filter(UserSettings.user_id == self.user_id).first()
        if row is None:
            row = UserSettings(user_id=self.user_id, cash_reserve_pct=pct)
            self.db.add(row)
        else:
            row.cash_reserve_pct = pct
        self.db.commit()
        return pct

    def set_onboarding_completed(self, flag: bool) -> bool:
        row = self.db.query(UserSettings).filter(UserSettings.user_id == self.user_id).first()
        if row is None:
            row = UserSettings(user_id=self.user_id, onboarding_completed=flag)
            self.db.add(row)
        else:
            row.onboarding_completed = flag
        self.db.commit()
        return flag
