"""add resource sync run audit table

Revision ID: 0005_resource_sync_runs
Revises: 0004_v5_core
Create Date: 2026-06-10 00:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_resource_sync_runs"
down_revision: Union[str, None] = "0004_v5_core"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "resource_sync_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sync_action", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("items_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_indexed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["resource_id"], ["resources.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_resource_sync_runs_company_started",
        "resource_sync_runs",
        ["company_id", "started_at"],
    )
    op.create_index(
        "ix_resource_sync_runs_resource_started",
        "resource_sync_runs",
        ["resource_id", "started_at"],
    )
    op.create_index(
        "ix_resource_sync_runs_action_status",
        "resource_sync_runs",
        ["sync_action", "status"],
    )
    op.alter_column("resource_sync_runs", "status", server_default=None)
    op.alter_column("resource_sync_runs", "items_seen", server_default=None)
    op.alter_column("resource_sync_runs", "items_indexed", server_default=None)
    op.alter_column("resource_sync_runs", "items_skipped", server_default=None)
    op.alter_column("resource_sync_runs", "summary", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_resource_sync_runs_action_status", table_name="resource_sync_runs")
    op.drop_index("ix_resource_sync_runs_resource_started", table_name="resource_sync_runs")
    op.drop_index("ix_resource_sync_runs_company_started", table_name="resource_sync_runs")
    op.drop_table("resource_sync_runs")
