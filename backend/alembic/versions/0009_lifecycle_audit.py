"""Lifecycle columns, append-only audit events, tombstones, worker heartbeats,
and hard-delete requests."""

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def fk(target: str, ondelete: str = "CASCADE") -> sa.ForeignKey:
    return sa.ForeignKey(target, ondelete=ondelete)


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "applications",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_applications_lifecycle_state", "applications", ["lifecycle_state"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_type", sa.String(80), nullable=False, index=True),
        sa.Column(
            "actor_id",
            sa.String(36),
            sa.ForeignKey("users.id"),
            nullable=True,
            index=True,
        ),
        sa.Column("actor_username", sa.String(100), nullable=True),
        sa.Column("resource_type", sa.String(40), nullable=False, index=True),
        sa.Column("resource_id", sa.String(40), nullable=True, index=True),
        sa.Column("correlation_id", sa.String(40), nullable=True, index=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
    )
    op.create_table(
        "application_tombstones",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "deleted_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("remaining_object_keys", sa.JSON(), nullable=False),
    )
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(100), primary_key=True),
        sa.Column("hostname", sa.String(100), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "hard_delete_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "application_id",
            sa.String(36),
            fk("applications.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("hard_delete_requests")
    op.drop_table("worker_heartbeats")
    op.drop_table("application_tombstones")
    op.drop_table("audit_events")
    op.drop_index("ix_applications_lifecycle_state", table_name="applications")
    op.drop_column("applications", "archived_at")
    op.drop_column("applications", "completed_at")
