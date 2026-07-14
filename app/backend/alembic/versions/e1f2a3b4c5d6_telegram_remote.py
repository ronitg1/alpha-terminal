"""add user_settings.telegram_remote_enabled (two-way Telegram remote control)

Per-user boolean gating the inbound Telegram poller. When on, the app runs the
commands the user texts their own bot (agentic assistant + /scan, /portfolio,
/help) and replies in Telegram — scoped to their paired chat_id ONLY. Defaults
to false so existing users (one-way alerts) are unaffected until they opt in.

Additive: a single nullable-false Boolean column with a server_default, so the
migration is a pure ADD COLUMN with no backfill and no lock on existing reads.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-07-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_settings",
        sa.Column(
            "telegram_remote_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("user_settings", "telegram_remote_enabled")
