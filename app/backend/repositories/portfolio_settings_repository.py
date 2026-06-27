"""DB-backed per-ticker portfolio settings — the Postgres replacement for
``portfolio_settings_service`` (``app/data/portfolio_settings.json``).

Returns the same nested ``{sleeve: {ticker: {allocation_pct, agents}}}`` shape
the file service exposes. ``agents`` of None means "inherit the sleeve default".
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.backend.database.app_models import DEFAULT_USER_ID, PortfolioSetting


class PortfolioSettingsRepository:
    def __init__(self, db: Session, user_id: str = DEFAULT_USER_ID):
        self.db = db
        self.user_id = user_id

    def _query(self):
        return self.db.query(PortfolioSetting).filter(PortfolioSetting.user_id == self.user_id)

    def get_all(self) -> dict[str, dict[str, dict[str, Any]]]:
        out: dict[str, dict[str, dict[str, Any]]] = {}
        for s in self._query().order_by(PortfolioSetting.id).all():
            out.setdefault(s.sleeve_name, {})[s.ticker] = {
                "allocation_pct": s.allocation_pct,
                "agents": s.agents,
            }
        return out

    def get_sleeve(self, sleeve: str) -> dict[str, dict[str, Any]]:
        return self.get_all().get(sleeve, {})

    def put_all(self, settings: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
        """Replace this user's entire settings map."""
        self._query().delete(synchronize_session=False)
        self.db.flush()
        for sleeve, tickers in (settings or {}).items():
            for ticker, cfg in (tickers or {}).items():
                self.db.add(PortfolioSetting(
                    user_id=self.user_id,
                    sleeve_name=sleeve,
                    ticker=ticker,
                    allocation_pct=float((cfg or {}).get("allocation_pct", 0.0)),
                    agents=(cfg or {}).get("agents"),
                ))
        self.db.commit()
        return self.get_all()

    def upsert_ticker(
        self, sleeve: str, ticker: str, allocation_pct: float, agents: list[str] | None
    ) -> dict[str, Any]:
        row = self._query().filter(
            PortfolioSetting.sleeve_name == sleeve, PortfolioSetting.ticker == ticker
        ).first()
        if row is None:
            row = PortfolioSetting(user_id=self.user_id, sleeve_name=sleeve, ticker=ticker)
            self.db.add(row)
        row.allocation_pct = float(allocation_pct)
        row.agents = agents
        self.db.commit()
        return self.get_all()

    def delete_ticker(self, sleeve: str, ticker: str) -> None:
        self._query().filter(
            PortfolioSetting.sleeve_name == sleeve, PortfolioSetting.ticker == ticker
        ).delete(synchronize_session=False)
        self.db.commit()
