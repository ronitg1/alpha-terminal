"""add scan_schedules + prescan_results (scheduled background pre-scans)

Per-user scheduled times for automatic background pattern scans, plus the latest
pre-computed results per user so the Pattern Scanner loads instantly.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scan_schedules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("time_of_day", sa.String(length=5), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="America/New_York"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_run_on", sa.String(length=10), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "time_of_day", name="uq_scan_schedule_user_time"),
    )
    op.create_index(op.f("ix_scan_schedules_id"), "scan_schedules", ["id"], unique=False)
    op.create_index(op.f("ix_scan_schedules_user_id"), "scan_schedules", ["user_id"], unique=False)

    op.create_table(
        "prescan_results",
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False, server_default="day"),
        sa.Column("ticker_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("prescan_results")
    op.drop_index(op.f("ix_scan_schedules_user_id"), table_name="scan_schedules")
    op.drop_index(op.f("ix_scan_schedules_id"), table_name="scan_schedules")
    op.drop_table("scan_schedules")
