"""Structured (DOCX/XLSX/CSV/Markdown) outputs with native locators."""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in ("number", "width", "height"):
        op.alter_column("document_pages", column, nullable=True)
    for column in ("x0", "y0", "x1", "y1"):
        op.alter_column("document_blocks", column, nullable=True)
    op.add_column("document_blocks", sa.Column("locator", sa.JSON(), nullable=True))
    op.add_column("table_cells", sa.Column("locator", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("table_cells", "locator")
    op.drop_column("document_blocks", "locator")
    for column in ("x0", "y0", "x1", "y1"):
        op.alter_column("document_blocks", column, nullable=False)
    for column in ("number", "width", "height"):
        op.alter_column("document_pages", column, nullable=False)
