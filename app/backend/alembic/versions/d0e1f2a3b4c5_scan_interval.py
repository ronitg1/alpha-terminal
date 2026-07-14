"""recurring interval scans: scan_schedules.interval_minutes + last_run_at

Adds an optional recurring-interval mode to scheduled scans. interval_minutes
NULL keeps the classic once-daily schedule; when set, the scan runs every N
minutes on/after time_of_day, gated by last_run_at. Both additive/nullable.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-07-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("scan_schedules", sa.Column("interval_minutes", sa.Integer(), nullable=True))
    op.add_column("scan_schedules", sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("scan_schedules", "last_run_at")
    op.drop_column("scan_schedules", "interval_minutes")
