"""Add quarantined to pii_findings status constraint

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-31

The original constraint omitted 'quarantined', preventing the quarantine
ETL job from marking findings as such. The risk inventory CASE WHEN query
already expects this value -- this migration aligns the constraint with it.
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE pii_findings DROP CONSTRAINT IF EXISTS chk_pii_findings_status")
    op.execute("""
        ALTER TABLE pii_findings
        ADD CONSTRAINT chk_pii_findings_status
        CHECK (status IN ('classified', 'flagged', 'clean', 'remediated', 'quarantined'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE pii_findings DROP CONSTRAINT IF EXISTS chk_pii_findings_status")
    op.execute("""
        ALTER TABLE pii_findings
        ADD CONSTRAINT chk_pii_findings_status
        CHECK (status IN ('classified', 'flagged', 'clean', 'remediated'))
    """)
