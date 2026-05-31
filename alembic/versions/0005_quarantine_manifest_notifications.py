"""quarantine_manifest and notifications tables

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── quarantine_manifest ───────────────────────────────────────────────────
    op.create_table(
        "quarantine_manifest",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("table_id", sa.String(255), nullable=False),
        sa.Column("source_s3_path", sa.String(1000), nullable=False),
        sa.Column("quarantine_s3_path", sa.String(1000), nullable=False),
        sa.Column("flagged_categories", JSONB, nullable=True),
        sa.Column("file_count", sa.Integer(), nullable=True),
        sa.Column("total_bytes", sa.BigInteger(), nullable=True),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default="quarantined",
        ),
        sa.Column(
            "quarantined_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String(255), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )

    op.create_index("ix_quarantine_manifest_table_id", "quarantine_manifest", ["table_id"])
    op.create_index("ix_quarantine_manifest_status", "quarantine_manifest", ["status"])

    op.execute(
        """
        ALTER TABLE quarantine_manifest
        ADD CONSTRAINT chk_quarantine_manifest_status
        CHECK (status IN ('quarantined', 'under_review', 'released', 'anonymized'))
        """
    )

    # ── notifications ─────────────────────────────────────────────────────────
    op.create_table(
        "notifications",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("notification_type", sa.String(50), nullable=False),  # email | slack
        sa.Column("recipient", sa.String(500), nullable=False),
        sa.Column("subject", sa.String(500), nullable=True),
        sa.Column("table_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
    )

    op.create_index("ix_notifications_table_id", "notifications", ["table_id"])
    op.create_index("ix_notifications_status", "notifications", ["status"])

    op.execute(
        """
        ALTER TABLE notifications
        ADD CONSTRAINT chk_notifications_type
        CHECK (notification_type IN ('email', 'slack'))
        """
    )
    op.execute(
        """
        ALTER TABLE notifications
        ADD CONSTRAINT chk_notifications_status
        CHECK (status IN ('pending', 'sent', 'failed'))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE notifications DROP CONSTRAINT IF EXISTS chk_notifications_status")
    op.execute("ALTER TABLE notifications DROP CONSTRAINT IF EXISTS chk_notifications_type")
    op.drop_index("ix_notifications_status", table_name="notifications")
    op.drop_index("ix_notifications_table_id", table_name="notifications")
    op.drop_table("notifications")

    op.execute(
        "ALTER TABLE quarantine_manifest DROP CONSTRAINT IF EXISTS chk_quarantine_manifest_status"
    )
    op.drop_index("ix_quarantine_manifest_status", table_name="quarantine_manifest")
    op.drop_index("ix_quarantine_manifest_table_id", table_name="quarantine_manifest")
    op.drop_table("quarantine_manifest")
