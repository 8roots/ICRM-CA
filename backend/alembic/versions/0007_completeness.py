"""Completeness templates, classification confirmations, mappings, waivers, runs."""

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def fk(target: str) -> sa.ForeignKey:
    return sa.ForeignKey(target, ondelete="CASCADE")


def upgrade() -> None:
    op.create_table(
        "completeness_templates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(60), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("product", sa.String(100), nullable=False, index=True),
        sa.Column("borrower_type", sa.String(20), nullable=False, index=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, index=True),
        sa.Column("demo_only", sa.Boolean(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("code", "version", name="uq_completeness_template_code_version"),
    )
    op.create_table(
        "checklist_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "template_id",
            sa.String(36),
            fk("completeness_templates.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("code", sa.String(60), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("category", sa.String(30), nullable=False, index=True),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("requires_seal", sa.Boolean(), nullable=False),
        sa.Column("requires_signature", sa.Boolean(), nullable=False),
        sa.Column("condition", sa.JSON(), nullable=True),
        sa.UniqueConstraint("template_id", "code", name="uq_checklist_item_template_code"),
    )
    op.create_table(
        "material_classification_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(36),
            fk("documents.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("category", sa.String(30), nullable=False, index=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("method", sa.String(30), nullable=False),
        sa.Column("method_version", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "document_id",
            "category",
            "method",
            name="uq_classification_candidate_document",
        ),
    )
    op.create_table(
        "classification_confirmations",
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
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", name="uq_classification_confirmation_document"),
    )
    op.create_table(
        "document_checklist_mappings",
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
            "item_id",
            sa.String(36),
            fk("checklist_items.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "application_id",
            "document_id",
            "item_id",
            name="uq_document_checklist_mapping",
        ),
    )
    op.create_table(
        "waiver_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "application_id",
            sa.String(36),
            fk("applications.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "item_id",
            sa.String(36),
            fk("checklist_items.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("application_id", "item_id", name="uq_waiver_application_item"),
    )
    op.create_table(
        "completeness_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "application_id",
            sa.String(36),
            fk("applications.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "template_id",
            sa.String(36),
            fk("completeness_templates.id"),
            nullable=False,
        ),
        sa.Column("template_snapshot", sa.JSON(), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("result_snapshot", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, index=True),
        sa.Column("stale_reason", sa.String(40), nullable=True),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("completeness_runs")
    op.drop_table("waiver_records")
    op.drop_table("document_checklist_mappings")
    op.drop_table("classification_confirmations")
    op.drop_table("material_classification_candidates")
    op.drop_table("checklist_items")
    op.drop_table("completeness_templates")
