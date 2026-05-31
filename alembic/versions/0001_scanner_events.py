"""scanner_events table

Revision ID: 0001
Revises:
Create Date: 2026-05-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scanner_events",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("event_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("source_name", sa.String(1000), nullable=False),
        sa.Column("data_source_type", sa.String(50), nullable=False),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("raw_event", JSONB, nullable=False),
        sa.Column("owner_email", sa.String(255), nullable=True),
        sa.Column("bucket", sa.String(255), nullable=True),
        sa.Column("file_format", sa.String(50), nullable=True),
        sa.Column("estimated_row_count", sa.BigInteger, nullable=True),
        sa.Column("column_count", sa.Integer, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Unique constraint for idempotency
    op.create_unique_constraint("uq_scanner_events_event_id", "scanner_events", ["event_id"])

    # Indexes required by Airflow patrol queries and time-range scans
    op.create_index("idx_scanner_events_created_at", "scanner_events", ["created_at"])
    op.create_index("idx_scanner_events_status", "scanner_events", ["status"])
    op.create_index("idx_scanner_events_event_type", "scanner_events", ["event_type"])

    # Auto-update updated_at on any row change
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_scanner_events_updated_at
        BEFORE UPDATE ON scanner_events
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_scanner_events_updated_at ON scanner_events")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at")
    op.drop_index("idx_scanner_events_event_type", "scanner_events")
    op.drop_index("idx_scanner_events_status", "scanner_events")
    op.drop_index("idx_scanner_events_created_at", "scanner_events")
    op.drop_constraint("uq_scanner_events_event_id", "scanner_events")
    op.drop_table("scanner_events")
