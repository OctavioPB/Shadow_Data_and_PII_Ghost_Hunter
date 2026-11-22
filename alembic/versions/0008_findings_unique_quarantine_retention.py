"""pii_findings uniqueness + quarantine retention columns

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-22

1. pii_findings had no natural key, so the classifier's
   ``INSERT ... ON CONFLICT DO NOTHING`` never conflicted and every DAG retry
   duplicated findings (inflating flagged_column_count and exposure figures).
   Duplicates are collapsed (newest row wins) and a unique constraint on
   (scanner_event_id, column_name) is added.

2. dag_quarantine_expiry reads/writes warning_sent_at and expired_at, which
   did not exist, so the 30-day retention policy never ran.
"""

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM pii_findings pf
        USING (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY scanner_event_id, column_name
                       ORDER BY created_at DESC, id DESC
                   ) AS rn
            FROM pii_findings
        ) dup
        WHERE pf.id = dup.id AND dup.rn > 1
        """
    )
    op.create_unique_constraint(
        "uq_pii_findings_event_col", "pii_findings", ["scanner_event_id", "column_name"]
    )

    op.add_column(
        "quarantine_manifest",
        sa.Column("warning_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "quarantine_manifest",
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Retention sweeps filter on "not yet expired, oldest first".
    op.create_index(
        "ix_quarantine_manifest_active",
        "quarantine_manifest",
        ["quarantined_at"],
        postgresql_where=sa.text("expired_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_quarantine_manifest_active", table_name="quarantine_manifest")
    op.drop_column("quarantine_manifest", "expired_at")
    op.drop_column("quarantine_manifest", "warning_sent_at")
    op.drop_constraint("uq_pii_findings_event_col", "pii_findings", type_="unique")
