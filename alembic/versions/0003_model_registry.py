"""model_registry table

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_registry",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("version", sa.String(100), nullable=False, unique=True),
        sa.Column("s3_uri", sa.String(1000), nullable=False),
        sa.Column("macro_f1", sa.Float(), nullable=True),
        sa.Column("weighted_f1", sa.Float(), nullable=True),
        sa.Column("accuracy", sa.Float(), nullable=True),
        sa.Column("fixture_accuracy", sa.Float(), nullable=True),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default="candidate",
        ),
        sa.Column(
            "trained_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "approved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
    )

    op.create_index("ix_model_registry_status", "model_registry", ["status"])
    op.create_index("ix_model_registry_trained_at", "model_registry", ["trained_at"])

    # Ensure only valid status values
    op.execute(
        """
        ALTER TABLE model_registry
        ADD CONSTRAINT chk_model_registry_status
        CHECK (status IN ('candidate', 'approved', 'deprecated'))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE model_registry DROP CONSTRAINT IF EXISTS chk_model_registry_status")
    op.drop_index("ix_model_registry_trained_at", table_name="model_registry")
    op.drop_index("ix_model_registry_status", table_name="model_registry")
    op.drop_table("model_registry")
