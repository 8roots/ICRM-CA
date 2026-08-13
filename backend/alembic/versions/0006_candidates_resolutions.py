"""Immutable candidate facts, resolutions, and restricted cloud extraction audit."""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def fk(target: str) -> sa.ForeignKey:
    return sa.ForeignKey(target, ondelete="CASCADE")


def upgrade() -> None:
    op.create_table(
        "candidate_facts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(36),
            fk("documents.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "output_id",
            sa.String(36),
            fk("document_outputs.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("block_id", sa.String(36), fk("document_blocks.id"), nullable=True),
        sa.Column("cell_id", sa.String(36), fk("table_cells.id"), nullable=True),
        sa.Column("subject_role", sa.String(30), nullable=True, index=True),
        sa.Column("field_key", sa.String(60), nullable=False, index=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("typed_value", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("extractor", sa.String(30), nullable=False),
        sa.Column("extractor_version", sa.String(100), nullable=False),
        sa.Column("model_version", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=True),
        sa.Column("source_refs", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "output_id",
            "field_key",
            "subject_role",
            "raw_text",
            "extractor",
            name="uq_candidate_fact_signature",
        ),
    )
    op.create_table(
        "resolutions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "application_id",
            sa.String(36),
            fk("applications.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "candidate_id",
            sa.String(36),
            fk("candidate_facts.id"),
            nullable=True,
        ),
        sa.Column("field_key", sa.String(60), nullable=False, index=True),
        sa.Column("subject_role", sa.String(30), nullable=True),
        sa.Column("resolution_type", sa.String(20), nullable=False),
        sa.Column("typed_value", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "actor_id",
            sa.String(36),
            sa.ForeignKey("users.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "cloud_extraction_calls",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "application_id",
            sa.String(36),
            fk("applications.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "document_id",
            sa.String(36),
            fk("documents.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "output_id",
            sa.String(36),
            fk("document_outputs.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=False),
        sa.Column("redaction_version", sa.String(100), nullable=False),
        sa.Column("source_refs", sa.JSON(), nullable=False),
        sa.Column("redacted_request", sa.JSON(), nullable=False),
        sa.Column("redacted_response", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("cloud_extraction_calls")
    op.drop_table("resolutions")
    op.drop_table("candidate_facts")
