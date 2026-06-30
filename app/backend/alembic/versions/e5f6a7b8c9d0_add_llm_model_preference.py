from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
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
