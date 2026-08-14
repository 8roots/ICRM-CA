"""Rule packages, rule context confirmations, LPR imports/entries, redline runs."""

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def fk(target: str, ondelete: str = "CASCADE") -> sa.ForeignKey:
    return sa.ForeignKey(target, ondelete=ondelete)


def upgrade() -> None:
    op.create_table(
        "rule_packages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(60), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, index=True),
        sa.Column("lender_qualification", sa.String(60), nullable=False, index=True),
        sa.Column("rule_context", sa.String(100), nullable=False, index=True),
        sa.Column("product", sa.String(100), nullable=False, index=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_until", sa.Date(), nullable=True),
        sa.Column("calc_type", sa.String(40), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("legal_basis", sa.Text(), nullable=False),
        sa.Column("reviewer", sa.String(100), nullable=False),
        sa.Column("reviewed_at", sa.Date(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, index=True),
        sa.Column("demo_only", sa.Boolean(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("code", "version", name="uq_rule_package_code_version"),
    )
    op.create_table(
        "rule_context_confirmations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "application_id",
            sa.String(36),
            fk("applications.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("context", sa.String(100), nullable=False),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("application_id", name="uq_rule_context_confirmation_application"),
    )
    op.create_table(
        "lpr_imports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("source_authority", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, index=True),
        sa.Column("demo_only", sa.Boolean(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "lpr_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "import_id",
            sa.String(36),
            fk("lpr_imports.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("tenor", sa.String(10), nullable=False),
        sa.Column("value", sa.String(40), nullable=False),
        sa.Column("publication_date", sa.Date(), nullable=False),
        sa.Column("source_url", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "import_id", "effective_date", "tenor", name="uq_lpr_entry_import_date_tenor"
        ),
    )
    op.create_table(
        "redline_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "application_id",
            sa.String(36),
            fk("applications.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "rule_id",
            sa.String(36),
            fk("rule_packages.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("rule_snapshot", sa.JSON(), nullable=True),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("result_snapshot", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, index=True),
        sa.Column("stale_reason", sa.String(40), nullable=True),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("redline_runs")
    op.drop_table("lpr_entries")
    op.drop_table("lpr_imports")
    op.drop_table("rule_context_confirmations")
    op.drop_table("rule_packages")
