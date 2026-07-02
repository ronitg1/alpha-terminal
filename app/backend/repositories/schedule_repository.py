"""DB persistence for scheduled background pre-scans.

Two concerns:
- per-user schedule CRUD (the times a user wants their scan run), scoped to
  ``self.user_id`` like the other repositories;
- the cross-user reads the scheduler needs (every enabled schedule, plus writing
  any user's pre-scan results), which take an explicit ``user_id``.

HTTP-agnostic: conflicts raise ``ValueError``, missing rows ``LookupError`` — the
service/route maps those to status codes.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.backend.database.app_models import (
    DEFAULT_USER_ID,
    PrescanResult,
    ScanSchedule,
)


class ScheduleRepository:
    def __init__(self, db: Session, user_id: str = DEFAULT_USER_ID):
        self.db = db
        self.user_id = user_id

    # ─── per-user schedule CRUD ───────────────────────────────────────────────

    @staticmethod
    def _to_dict(s: ScanSchedule) -> dict[str, Any]:
        return {
            "id": s.id,
            "time_of_day": s.time_of_day,
            "timezone": s.timezone,
            "enabled": bool(s.enabled),
            "last_run_on": s.last_run_on,
            "timeframe": s.timeframe,
            "lookback_days": s.lookback_days,
        }

    def list_schedules(self) -> list[dict[str, Any]]:
        rows = (
            self.db.query(ScanSchedule)
            .filter(ScanSchedule.user_id == self.user_id)
            .order_by(ScanSchedule.time_of_day)
            .all()
        )
        return [self._to_dict(s) for s in rows]

    def add_schedule(
        self, time_of_day: str, timezone: str, timeframe: str = "day", lookback_days: int = 180
    ) -> dict[str, Any]:
        existing = (
            self.db.query(ScanSchedule)
            .filter(ScanSchedule.user_id == self.user_id, ScanSchedule.time_of_day == time_of_day)
            .first()
        )
        if existing is not None:
            raise ValueError(f"A scan is already scheduled at {time_of_day}.")
        row = ScanSchedule(
            user_id=self.user_id, time_of_day=time_of_day, timezone=timezone, enabled=True,
            timeframe=timeframe, lookback_days=lookback_days,
        )
        self.db.add(row)
        self.db.commit()
        return self._to_dict(row)

    def set_enabled(self, schedule_id: int, enabled: bool) -> dict[str, Any]:
        row = self._owned(schedule_id)
        row.enabled = enabled
        self.db.commit()
        return self._to_dict(row)

    def update_schedule(self, schedule_id: int, timeframe: str, lookback_days: int) -> dict[str, Any]:
        row = self._owned(schedule_id)
        row.timeframe = timeframe
        row.lookback_days = lookback_days
        self.db.commit()
        return self._to_dict(row)

    def delete_schedule(self, schedule_id: int) -> None:
        row = self._owned(schedule_id)
        self.db.delete(row)
        self.db.commit()

    def _owned(self, schedule_id: int) -> ScanSchedule:
        row = (
            self.db.query(ScanSchedule)
            .filter(ScanSchedule.id == schedule_id, ScanSchedule.user_id == self.user_id)
            .first()
        )
        if row is None:
            raise LookupError(f"No schedule {schedule_id} for this user.")
        return row

    # ─── per-user pre-scan results ────────────────────────────────────────────

    def get_prescan(self, timeframe: str | None = None) -> dict[str, Any] | None:
        q = self.db.query(PrescanResult).filter(PrescanResult.user_id == self.user_id)
        if timeframe:
            row = q.filter(PrescanResult.timeframe == timeframe).first()
        else:  # most recently computed across timeframes (initial-load default)
            row = q.order_by(PrescanResult.computed_at.desc()).first()
        if row is None:
            return None
        return {
            "results": list(row.results or []),
            "timeframe": row.timeframe,
            "ticker_count": row.ticker_count,
            "computed_at": row.computed_at.isoformat() if row.computed_at else None,
        }

    # ─── cross-user (the scheduler) ───────────────────────────────────────────

    def all_enabled_schedules(self) -> list[dict[str, Any]]:
        """Every enabled schedule across all users (for the due-check)."""
        rows = self.db.query(ScanSchedule).filter(ScanSchedule.enabled.is_(True)).all()
        return [
            {
                "id": s.id,
                "user_id": s.user_id,
                "time_of_day": s.time_of_day,
                "timezone": s.timezone,
                "last_run_on": s.last_run_on,
                "timeframe": s.timeframe,
                "lookback_days": s.lookback_days,
            }
            for s in rows
        ]

    def mark_run(self, schedule_id: int, ran_on: str) -> None:
        row = self.db.query(ScanSchedule).filter(ScanSchedule.id == schedule_id).first()
        if row is not None:
            row.last_run_on = ran_on
            self.db.commit()

    def set_prescan_for(
        self, user_id: str, results: list[dict], timeframe: str, ticker_count: int
    ) -> None:
        row = (
            self.db.query(PrescanResult)
            .filter(PrescanResult.user_id == user_id, PrescanResult.timeframe == timeframe)
            .first()
        )
        if row is None:
            row = PrescanResult(user_id=user_id, timeframe=timeframe)
            self.db.add(row)
        row.results = results
        row.ticker_count = ticker_count
        self.db.commit()
