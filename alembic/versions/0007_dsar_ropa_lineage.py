"""DSAR searches, ROPA annotations, lineage edges

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── DSAR search audit log ─────────────────────────────────────────────────
    op.create_table(
        "dsar_searches",
        sa.Column("search_id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("initiated_by", sa.Text(), nullable=False),
        sa.Column("identifier_type", sa.String(50), nullable=False),
        sa.Column("search_hash", sa.Text(), nullable=False),
        sa.Column("tables_searched_count", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("tables_matched_count", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_dsar_searches_initiated_by", "dsar_searches",
                    ["initiated_by"])
    op.create_index("ix_dsar_searches_created_at", "dsar_searches",
                    ["created_at"])
    op.execute("""
        ALTER TABLE dsar_searches
        ADD CONSTRAINT chk_dsar_identifier_type
        CHECK (identifier_type IN ('email', 'national_id', 'phone'))
    """)

    # ── ROPA annotations ───────────────────────────────────────────────────────
    op.create_table(
        "ropa_annotations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("legal_basis", sa.String(100), nullable=True),
        sa.Column("cross_border_transfer", sa.Boolean(), nullable=False,
                  server_default="false"),
        sa.Column("annotated_by", sa.Text(), nullable=False),
        sa.Column("annotated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_ropa_annotations_source_name", "ropa_annotations",
                    ["source_name"])

    # ── Lineage edges ──────────────────────────────────────────────────────────
    op.create_table(
        "lineage_edges",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("parent_table_id", sa.Text(), nullable=False),
        sa.Column("child_table_id", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("inference_method", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_lineage_edges_parent", "lineage_edges",
                    ["parent_table_id"])
    op.create_index("ix_lineage_edges_child", "lineage_edges",
                    ["child_table_id"])
    op.create_unique_constraint("uq_lineage_edge", "lineage_edges",
                                ["parent_table_id", "child_table_id"])
    op.execute("""
        ALTER TABLE lineage_edges
        ADD CONSTRAINT chk_lineage_method
        CHECK (inference_method IN (
            's3_copy_event', 'path_heuristic',
            'column_profile_similarity', 'analyst_confirmed'
        ))
    """)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE lineage_edges DROP CONSTRAINT IF EXISTS chk_lineage_method"
    )
    op.drop_constraint("uq_lineage_edge", "lineage_edges", type_="unique")
    op.drop_index("ix_lineage_edges_child", table_name="lineage_edges")
    op.drop_index("ix_lineage_edges_parent", table_name="lineage_edges")
    op.drop_table("lineage_edges")

    op.drop_index("ix_ropa_annotations_source_name",
                  table_name="ropa_annotations")
    op.drop_table("ropa_annotations")

    op.execute(
        "ALTER TABLE dsar_searches DROP CONSTRAINT IF EXISTS chk_dsar_identifier_type"
    )
    op.drop_index("ix_dsar_searches_created_at", table_name="dsar_searches")
    op.drop_index("ix_dsar_searches_initiated_by", table_name="dsar_searches")
    op.drop_table("dsar_searches")
