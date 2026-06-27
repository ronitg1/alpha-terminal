"""add multi-tenant app tables (users, portfolios, watchlists, settings, pnl, theses, scans)

Creates the Postgres home for application state that previously lived in JSON
files and Python config modules. Every table is user-scoped; a single
``default`` user is seeded so pre-auth (Phase 2) behavior maps onto one owner.

Revision ID: f1a2b3c4d5e6
Revises: d5e78f9a1b2c
Create Date: 2026-06-27
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f1a2b3c4d5e6"
down_revision = "d5e78f9a1b2c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("cash_reserve_pct", sa.Float(), nullable=False, server_default="10.0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "portfolios",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("allocation_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("agents", sa.JSON(), nullable=False),
        sa.Column("agent_weights", sa.JSON(), nullable=False),
        sa.Column("tickers", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_portfolio_user_name"),
    )
    op.create_index("ix_portfolios_user_id", "portfolios", ["user_id"])

    op.create_table(
        "watchlists",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("entries", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_watchlist_user_name"),
    )
    op.create_index("ix_watchlists_user_id", "watchlists", ["user_id"])

    op.create_table(
        "portfolio_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("sleeve_name", sa.String(length=64), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("allocation_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("agents", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "sleeve_name", "ticker", name="uq_psetting_user_sleeve_ticker"),
    )
    op.create_index("ix_portfolio_settings_user_id", "portfolio_settings", ["user_id"])

    op.create_table(
        "pnl_positions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("ticker", sa.String(length=16), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("option", sa.JSON(), nullable=True),
        sa.Column("entry_price", sa.Float(), nullable=True),
        sa.Column("entry_date", sa.String(length=10), nullable=True),
        sa.Column("status", sa.String(length=8), nullable=False, server_default="open"),
        sa.Column("exit_price", sa.Float(), nullable=True),
        sa.Column("exit_date", sa.String(length=10), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("real", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("import_key", sa.String(length=255), nullable=True),
        sa.Column("closing_import_key", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=True),
        sa.Column("updated_at", sa.String(length=40), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pnl_positions_user_id", "pnl_positions", ["user_id"])
    op.create_index("ix_pnl_positions_import_key", "pnl_positions", ["import_key"])

    op.create_table(
        "theses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "key", name="uq_thesis_user_key"),
    )
    op.create_index("ix_theses_user_id", "theses", ["user_id"])

    op.create_table(
        "scan_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("scan_date", sa.String(length=10), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "scan_date", name="uq_scan_user_date"),
    )
    op.create_index("ix_scan_results_user_id", "scan_results", ["user_id"])

    # Seed the single pre-auth owner so Phase-2 (no auth yet) data has a home.
    op.execute("INSERT INTO users (id, email) VALUES ('default', NULL)")
    op.execute("INSERT INTO user_settings (user_id, cash_reserve_pct) VALUES ('default', 10.0)")


def downgrade() -> None:
    op.drop_index("ix_scan_results_user_id", table_name="scan_results")
    op.drop_table("scan_results")
    op.drop_index("ix_theses_user_id", table_name="theses")
    op.drop_table("theses")
    op.drop_index("ix_pnl_positions_import_key", table_name="pnl_positions")
    op.drop_index("ix_pnl_positions_user_id", table_name="pnl_positions")
    op.drop_table("pnl_positions")
    op.drop_index("ix_portfolio_settings_user_id", table_name="portfolio_settings")
    op.drop_table("portfolio_settings")
    op.drop_index("ix_watchlists_user_id", table_name="watchlists")
    op.drop_table("watchlists")
    op.drop_index("ix_portfolios_user_id", table_name="portfolios")
    op.drop_table("portfolios")
    op.drop_table("user_settings")
    op.drop_table("users")
