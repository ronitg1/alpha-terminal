"""per-schedule timeframe + lookback; key prescan_results by (user_id, timeframe)

Scheduled pre-scans can now specify their own chart timeframe + lookback (e.g. a
daily 2yr premarket scan alongside a 1h 30d intraday scan), so:
  - scan_schedules gains ``timeframe`` + ``lookback_days`` (defaults match the old
    hardcoded runner values, so existing schedules keep scanning daily/180d);
  - prescan_results is re-keyed from (user_id) to (user_id, timeframe) so multiple
    differing-timeframe pre-scans coexist instead of the last one clobbering the
    rest. ``timeframe`` already exists on the table (default 'day'), so existing
    rows become the (user_id, 'day') slot with no data migration.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-07-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scan_schedules",
        sa.Column("timeframe", sa.String(length=8), nullable=False, server_default="day"),
    )
    op.add_column(
        "scan_schedules",
        sa.Column("lookback_days", sa.Integer(), nullable=False, server_default="180"),
    )
    # Re-key prescan_results on (user_id, timeframe). Existing rows already carry a
    # non-null timeframe (default 'day'), so the composite key is satisfied.
    op.drop_constraint("prescan_results_pkey", "prescan_results", type_="primary")
    op.create_primary_key("prescan_results_pkey", "prescan_results", ["user_id", "timeframe"])


def downgrade() -> None:
    # Collapse back to one row per user: keep the 'day' slot (or an arbitrary one)
    # and drop the rest so the single-column PK can be restored.
    op.execute(
        """
        DELETE FROM prescan_results a
        USING prescan_results b
        WHERE a.user_id = b.user_id
          AND a.timeframe <> b.timeframe
          AND (b.timeframe = 'day' OR (a.timeframe <> 'day' AND a.ctid < b.ctid))
        """
    )
    op.drop_constraint("prescan_results_pkey", "prescan_results", type_="primary")
    op.create_primary_key("prescan_results_pkey", "prescan_results", ["user_id"])
    op.drop_column("scan_schedules", "lookback_days")
    op.drop_column("scan_schedules", "timeframe")
