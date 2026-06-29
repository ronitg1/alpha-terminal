"""user-scope the api_keys table (Phase 3 BYOK)

Adds a ``user_id`` column and swaps the global ``UNIQUE(provider)`` for
``UNIQUE(user_id, provider)`` so each user has their own key per provider. Key
values are encrypted at the application layer (``app/backend/crypto.py``); the
column type is unchanged (Text).

Implemented as drop + recreate. This is safe because the legacy ``api_keys``
table is an unused dormant scaffold: there was no UI for it, and the deployed
app reads provider keys from environment variables, not this table — so it is
empty in every real environment. Recreating avoids cross-dialect pain renaming
the old unnamed ``UNIQUE(provider)`` constraint. (Local SQLite/test DBs build the
schema from the model via ``create_all`` and do not run this migration.)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Safety guard: the drop is only safe because the table is empty in every real
    # environment (no UI ever wrote to it; deployed keys live in env vars). If that
    # assumption is ever wrong, HALT the deploy loudly rather than silently dropping
    # someone's stored secrets — a failed preDeploy keeps the previous release live.
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT COUNT(*) FROM api_keys")).scalar()
    if count:
        raise RuntimeError(
            f"Refusing to drop api_keys: it has {count} row(s). This migration assumes "
            "an empty legacy table. Migrate the rows manually (add user_id, backfill, "
            "encrypt key_value) before re-running."
        )
    op.drop_table("api_keys")
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("key_value", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("last_used", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider", name="uq_apikey_user_provider"),
    )
    op.create_index(op.f("ix_api_keys_id"), "api_keys", ["id"], unique=False)
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"], unique=False)
    op.create_index(op.f("ix_api_keys_provider"), "api_keys", ["provider"], unique=False)


def downgrade() -> None:
    op.drop_table("api_keys")
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("key_value", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("last_used", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider"),
    )
    op.create_index(op.f("ix_api_keys_id"), "api_keys", ["id"], unique=False)
    op.create_index(op.f("ix_api_keys_provider"), "api_keys", ["provider"], unique=False)
