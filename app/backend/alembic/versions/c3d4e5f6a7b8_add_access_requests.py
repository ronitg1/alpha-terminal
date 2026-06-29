"""add access_requests table (Phase 3 shared-data access requests)

Stores users' requests to use the owner's shared market-data keys for free; an
approved row grants the requester's email shared access (alongside the static
SHARED_DATA_EMAILS env allowlist).

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "access_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_access_request_user"),
    )
    op.create_index(op.f("ix_access_requests_id"), "access_requests", ["id"], unique=False)
    op.create_index(op.f("ix_access_requests_email"), "access_requests", ["email"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_access_requests_email"), table_name="access_requests")
    op.drop_index(op.f("ix_access_requests_id"), table_name="access_requests")
    op.drop_table("access_requests")
