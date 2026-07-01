"""add user_settings LLM model preference columns (OpenRouter BYOK)

Per-user LLM model selection (provider + model name + whether saved). Re-chained
onto the scan_schedules head: PR #1 originally used revision id e5f6a7b8c9d0,
which collided with the scan_schedules migration shipped in v1.8.0 — renamed here
and pointed at that migration so both apply in a single chain.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-01
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_settings",
        sa.Column(
            "llm_model_provider",
            sa.String(length=64),
            nullable=False,
            server_default="DeepSeek",
        ),
    )
    op.add_column(
        "user_settings",
        sa.Column(
            "llm_model_name",
            sa.String(length=200),
            nullable=False,
            server_default="deepseek-reasoner",
        ),
    )
    op.add_column(
        "user_settings",
        sa.Column(
            "llm_preference_saved",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("user_settings", "llm_preference_saved")
    op.drop_column("user_settings", "llm_model_name")
    op.drop_column("user_settings", "llm_model_provider")
