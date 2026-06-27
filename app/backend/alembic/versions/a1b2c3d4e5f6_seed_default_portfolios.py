"""seed the default user's portfolios + cash reserve from the shipped config

The table-creation migration (f1a2b3c4d5e6) seeds only the ``default`` user and
its ``user_settings`` row, leaving ``portfolios`` empty. With
``STORAGE_BACKEND=db`` that empties the sleeve config the dashboard and the scan
engine depend on — ``read_sleeves()`` returns ``{}`` and every scan 400s. This
data migration copies the shipped ``PORTFOLIO_SLEEVES`` (and ``CASH_RESERVE_PCT``)
into the ``default`` user's rows so a fresh DB boots with the same content the
local file backend ships with.

Idempotent: if the ``default`` user already owns any portfolio (e.g. the user
has edited their sleeves, or this ran before), the seed is skipped entirely —
it never overwrites live data. Phase 3 will seed per-user defaults at signup;
until then this one-time copy is what makes "flip the flag" a no-op for content.

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-06-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None

_DEFAULT_USER_ID = "default"

# Lightweight table handles for portable (SQLite + Postgres) bulk insert. The
# sa.JSON column type serializes the Python list/dict values per-dialect.
_portfolios = sa.table(
    "portfolios",
    sa.column("user_id", sa.String),
    sa.column("name", sa.String),
    sa.column("allocation_pct", sa.Float),
    sa.column("agents", sa.JSON),
    sa.column("agent_weights", sa.JSON),
    sa.column("tickers", sa.JSON),
)


def upgrade() -> None:
    conn = op.get_bind()

    # Idempotency guard: never touch a user who already has sleeves.
    existing = conn.execute(
        sa.text("SELECT COUNT(*) FROM portfolios WHERE user_id = :uid"),
        {"uid": _DEFAULT_USER_ID},
    ).scalar()
    if existing:
        return

    # Import the shipped config at migration time so the DB seed matches exactly
    # what the file backend ships. Repo root is on PYTHONPATH when alembic runs
    # (see HANDOFF run/test cheat sheet).
    from src.config.portfolio_config import CASH_RESERVE_PCT, PORTFOLIO_SLEEVES

    rows = [
        {
            "user_id": _DEFAULT_USER_ID,
            "name": name,
            "allocation_pct": float(sleeve["allocation_pct"]),
            "agents": list(sleeve["agents"]),
            "agent_weights": {k: float(v) for k, v in sleeve["agent_weights"].items()},
            "tickers": list(sleeve["tickers"]),
        }
        for name, sleeve in PORTFOLIO_SLEEVES.items()
    ]
    if rows:
        op.bulk_insert(_portfolios, rows)

    # Align the seeded cash reserve with the shipped value (the base migration
    # hard-coded 10.0; keep them in sync if the shipped default ever differs).
    conn.execute(
        sa.text(
            "UPDATE user_settings SET cash_reserve_pct = :pct WHERE user_id = :uid"
        ),
        {"pct": float(CASH_RESERVE_PCT), "uid": _DEFAULT_USER_ID},
    )


def downgrade() -> None:
    # Remove only the seeded default-user portfolios; user_settings is left as-is
    # (the base migration owns its lifecycle).
    op.execute(
        sa.text("DELETE FROM portfolios WHERE user_id = 'default'")
    )
