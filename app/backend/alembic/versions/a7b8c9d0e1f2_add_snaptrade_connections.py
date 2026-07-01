"""add snaptrade_connections (read-only brokerage sync via SnapTrade)

Per-user SnapTrade registration for the read-only Fidelity pull. Stores the
SnapTrade ``user_secret`` encrypted at rest (Fernet), one row per app user.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-01
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "snaptrade_connections",
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("snaptrade_user_id", sa.String(length=255), nullable=False),
        sa.Column("user_secret", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("snaptrade_connections")
