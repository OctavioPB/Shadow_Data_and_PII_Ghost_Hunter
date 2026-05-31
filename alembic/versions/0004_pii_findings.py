"""pii_findings table

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pii_findings",
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
        sa.Column(
            "column_sample_id",
            UUID(as_uuid=True),
            sa.ForeignKey("column_samples.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("table_id", sa.String(255), nullable=False),
        sa.Column("column_name", sa.String(255), nullable=False),
        sa.Column("pii_category", sa.String(50), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("flagged", sa.Boolean(), nullable=False, server_default="false"),
        # sampled → classified → flagged | clean
        sa.Column("status", sa.String(50), nullable=False, server_default="classified"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index("ix_pii_findings_scanner_event_id", "pii_findings", ["scanner_event_id"])
    op.create_index("ix_pii_findings_table_id", "pii_findings", ["table_id"])
    op.create_index("ix_pii_findings_flagged", "pii_findings", ["flagged"])
    op.create_index("ix_pii_findings_pii_category", "pii_findings", ["pii_category"])

    # Status constraint
    op.execute(
        """
        ALTER TABLE pii_findings
        ADD CONSTRAINT chk_pii_findings_status
        CHECK (status IN ('classified', 'flagged', 'clean', 'remediated'))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE pii_findings DROP CONSTRAINT IF EXISTS chk_pii_findings_status")
    op.drop_index("ix_pii_findings_pii_category", table_name="pii_findings")
    op.drop_index("ix_pii_findings_flagged", table_name="pii_findings")
    op.drop_index("ix_pii_findings_table_id", table_name="pii_findings")
    op.drop_index("ix_pii_findings_scanner_event_id", table_name="pii_findings")
    op.drop_table("pii_findings")
