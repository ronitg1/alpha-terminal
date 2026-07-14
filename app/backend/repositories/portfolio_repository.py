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

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.backend.database.app_models import (
    DEFAULT_CASH_RESERVE_PCT,
    DEFAULT_LLM_MODEL_NAME,
    DEFAULT_LLM_MODEL_PROVIDER,
    DEFAULT_USER_ID,
    NotifiedSignal,
    Portfolio,
    UserSettings,
)


def _csv_to_list(csv: str | None) -> list[str]:
    return [t.strip() for t in (csv or "").split(",") if t.strip()]


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

    def get_llm_preference(self) -> dict[str, Any]:
        row = self.db.query(UserSettings).filter(UserSettings.user_id == self.user_id).first()
        if row is None:
            return {
                "model_provider": DEFAULT_LLM_MODEL_PROVIDER,
                "model_name": DEFAULT_LLM_MODEL_NAME,
                "preference_saved": False,
            }
        return {
            "model_provider": row.llm_model_provider or DEFAULT_LLM_MODEL_PROVIDER,
            "model_name": row.llm_model_name or DEFAULT_LLM_MODEL_NAME,
            "preference_saved": bool(row.llm_preference_saved),
        }

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

    def set_llm_preference(self, model_provider: str, model_name: str) -> dict[str, Any]:
        row = self.db.query(UserSettings).filter(UserSettings.user_id == self.user_id).first()
        if row is None:
            row = UserSettings(
                user_id=self.user_id,
                llm_model_provider=model_provider,
                llm_model_name=model_name,
                llm_preference_saved=True,
            )
            self.db.add(row)
        else:
            row.llm_model_provider = model_provider
            row.llm_model_name = model_name
            row.llm_preference_saved = True
        self.db.commit()
        return {
            "model_provider": model_provider,
            "model_name": model_name,
            "preference_saved": True,
        }

    # ─── Telegram alert prefs + dedup ─────────────────────────────────────────

    def get_alert_settings(self) -> dict[str, Any]:
        row = self.db.query(UserSettings).filter(UserSettings.user_id == self.user_id).first()
        if row is None:
            return {"chat_id": None, "enabled": False, "min_confidence": 90.0,
                    "timeframes": ["day", "1h"], "remote_enabled": False}
        return {
            "chat_id": row.telegram_chat_id,
            "enabled": bool(row.telegram_alerts_enabled),
            "min_confidence": float(row.telegram_min_confidence if row.telegram_min_confidence is not None else 90.0),
            "timeframes": _csv_to_list(row.telegram_timeframes) or ["day", "1h"],
            "remote_enabled": bool(row.telegram_remote_enabled),
        }

    def set_alert_settings(
        self, *, chat_id: str | None, enabled: bool, min_confidence: float,
        timeframes: list[str], remote_enabled: bool = False,
    ) -> dict[str, Any]:
        row = self.db.query(UserSettings).filter(UserSettings.user_id == self.user_id).first()
        if row is None:
            row = UserSettings(user_id=self.user_id)
            self.db.add(row)
        row.telegram_chat_id = chat_id
        row.telegram_alerts_enabled = enabled
        row.telegram_min_confidence = min_confidence
        row.telegram_timeframes = ",".join(timeframes)
        row.telegram_remote_enabled = remote_enabled
        self.db.commit()
        return self.get_alert_settings()

    def filter_unnotified(self, keys: list[str]) -> list[str]:
        """Return the subset of ``keys`` not already recorded as notified."""
        if not keys:
            return []
        existing = {
            r.signal_key
            for r in self.db.query(NotifiedSignal.signal_key)
            .filter(NotifiedSignal.user_id == self.user_id, NotifiedSignal.signal_key.in_(keys))
            .all()
        }
        return [k for k in keys if k not in existing]

    def mark_notified(self, keys: list[str]) -> None:
        for k in keys:
            self.db.add(NotifiedSignal(user_id=self.user_id, signal_key=k))
        try:
            self.db.commit()
        except IntegrityError:
            # A concurrent run already recorded one of these — harmless.
            self.db.rollback()
