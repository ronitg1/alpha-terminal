"""add user_settings.onboarding_completed (first-login walkthrough flag)

Per-user boolean recording whether the user has finished/skipped the first-login
onboarding walkthrough. Server-side source of truth so the walkthrough shows
exactly once per account (survives a browser/localStorage clear). Defaults to
false so existing users are treated as not-yet-onboarded — acceptable: they see
the walkthrough once, then it's recorded.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_settings",
        sa.Column(
            "onboarding_completed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("user_settings", "onboarding_completed")
