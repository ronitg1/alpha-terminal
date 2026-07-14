"""telegram alert prefs on user_settings + notified_signals dedup table

Adds per-user Telegram high-confidence alert routing/rules to user_settings
(the bot token itself stays in the encrypted api_keys store, not here) and a
notified_signals ledger so a recurring scheduled scan doesn't re-push the same
signal. Column defaults keep existing rows valid; the new table is additive.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-07-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("telegram_chat_id", sa.String(length=64), nullable=True))
    op.add_column(
        "user_settings",
        sa.Column("telegram_alerts_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "user_settings",
        sa.Column("telegram_min_confidence", sa.Float(), nullable=False, server_default="90"),
    )
    op.add_column(
        "user_settings",
        sa.Column("telegram_timeframes", sa.String(length=64), nullable=False, server_default="day,1h"),
    )
    op.create_table(
        "notified_signals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("signal_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "signal_key", name="uq_notified_signal"),
    )
    op.create_index("ix_notified_signals_user_id", "notified_signals", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_notified_signals_user_id", table_name="notified_signals")
    op.drop_table("notified_signals")
    op.drop_column("user_settings", "telegram_timeframes")
    op.drop_column("user_settings", "telegram_min_confidence")
    op.drop_column("user_settings", "telegram_alerts_enabled")
    op.drop_column("user_settings", "telegram_chat_id")
