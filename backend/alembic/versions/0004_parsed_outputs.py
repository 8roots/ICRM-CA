"""Versioned PDF/image parsing outputs and evidence coordinates."""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def bbox_columns(*, nullable: bool = False) -> list[sa.Column]:
    return [
        sa.Column("x0", sa.Float(), nullable=nullable),
        sa.Column("y0", sa.Float(), nullable=nullable),
        sa.Column("x1", sa.Float(), nullable=nullable),
        sa.Column("y1", sa.Float(), nullable=nullable),
    ]


def upgrade() -> None:
    op.create_table(
        "document_outputs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(36),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("parser_version", sa.String(100), nullable=False),
        sa.Column("model_version", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", "version"),
    )
    op.create_index("ix_document_outputs_document_id", "document_outputs", ["document_id"])
    op.create_table(
        "document_pages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "output_id",
            sa.String(36),
            sa.ForeignKey("document_outputs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("width", sa.Float(), nullable=False),
        sa.Column("height", sa.Float(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.UniqueConstraint("output_id", "number"),
    )
    op.create_index("ix_document_pages_output_id", "document_pages", ["output_id"])
    op.create_table(
        "document_blocks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "page_id",
            sa.String(36),
            sa.ForeignKey("document_pages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        *bbox_columns(),
        sa.Column("extraction_method", sa.String(30), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.UniqueConstraint("page_id", "order"),
    )
    op.create_index("ix_document_blocks_page_id", "document_blocks", ["page_id"])
    op.create_table(
        "table_cells",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "block_id",
            sa.String(36),
            sa.ForeignKey("document_blocks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("column_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        *bbox_columns(nullable=True),
        sa.UniqueConstraint("block_id", "row_index", "column_index"),
    )
    op.create_index("ix_table_cells_block_id", "table_cells", ["block_id"])
    op.create_table(
        "seal_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "page_id",
            sa.String(36),
            sa.ForeignKey("document_pages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        *bbox_columns(),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("model_version", sa.String(100), nullable=False),
    )
    op.create_index("ix_seal_candidates_page_id", "seal_candidates", ["page_id"])
    op.create_table(
        "evidence_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "output_id",
            sa.String(36),
            sa.ForeignKey("document_outputs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "seal_candidate_id",
            sa.String(36),
            sa.ForeignKey("seal_candidates.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_evidence_reviews_output_id", "evidence_reviews", ["output_id"])
    op.create_index("ix_evidence_reviews_actor_id", "evidence_reviews", ["actor_id"])


def downgrade() -> None:
    op.drop_table("evidence_reviews")
    op.drop_table("seal_candidates")
    op.drop_table("table_cells")
    op.drop_table("document_blocks")
    op.drop_table("document_pages")
    op.drop_table("document_outputs")
