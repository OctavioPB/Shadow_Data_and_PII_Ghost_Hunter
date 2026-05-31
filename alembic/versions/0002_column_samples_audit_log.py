"""column_samples and audit_log tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── column_samples ────────────────────────────────────────────────────────
    op.create_table(
        "column_samples",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "scanner_event_id",
            UUID(as_uuid=True),
            sa.ForeignKey("scanner_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("table_id", sa.String(255), nullable=False),
        sa.Column("column_name", sa.String(255), nullable=False),
        sa.Column("column_dtype", sa.String(100), nullable=True),
        sa.Column("sample_count", sa.Integer, nullable=True),
        sa.Column("sample_s3_path", sa.String(1000), nullable=True),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default=sa.text("'sampled'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Idempotent upsert key: one row per (scanner_event, column_name)
        sa.UniqueConstraint("scanner_event_id", "column_name", name="uq_column_samples_event_col"),
    )
    op.create_index("idx_column_samples_event_id", "column_samples", ["scanner_event_id"])
    op.create_index("idx_column_samples_table_id", "column_samples", ["table_id"])

    # ── audit_log ─────────────────────────────────────────────────────────────
    op.create_table(
        "audit_log",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("table_id", sa.String(255), nullable=True),
        sa.Column(
            "actor",
            sa.String(255),
            nullable=False,
            server_default=sa.text("'system'"),
        ),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("details_json", JSONB, nullable=True),
    )
    op.create_index("idx_audit_log_timestamp", "audit_log", ["timestamp"])
    op.create_index("idx_audit_log_table_id", "audit_log", ["table_id"])

    # Enforce append-only at the DB level: block UPDATE and DELETE on audit_log
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_audit_log_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only — UPDATE and DELETE are not permitted';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_log_immutable
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_immutable ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_log_mutation")
    op.drop_index("idx_audit_log_table_id", "audit_log")
    op.drop_index("idx_audit_log_timestamp", "audit_log")
    op.drop_table("audit_log")
    op.drop_index("idx_column_samples_table_id", "column_samples")
    op.drop_index("idx_column_samples_event_id", "column_samples")
    op.drop_table("column_samples")
