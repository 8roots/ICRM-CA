"""Protect job results with a unique claim token."""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("document_jobs", sa.Column("claim_token", sa.String(36), nullable=True))


def downgrade() -> None:
    op.drop_column("document_jobs", "claim_token")
