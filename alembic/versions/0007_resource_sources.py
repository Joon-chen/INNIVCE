"""add resource sources and event visibility scope

Revision ID: 0007_resource_sources
Revises: 0006_bot_context_memory
Create Date: 2026-06-12 16:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0007_resource_sources"
down_revision: Union[str, None] = "0006_bot_context_memory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "resource_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("source_account_id", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("source_account_label", sa.String(length=300), nullable=True),
        sa.Column("access_level", sa.String(length=80), nullable=False, server_default="read"),
        sa.Column("can_sync", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("visibility_scope", sa.String(length=80), nullable=False, server_default="company"),
        sa.Column("allowed_user_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("allowed_roles", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("allowed_departments", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("limitations", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["resource_id"], ["resources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resource_id", "source_type", "source_account_id", name="uq_resource_source_identity"),
    )
    op.create_index("ix_resource_sources_company_type", "resource_sources", ["company_id", "source_type"])
    op.create_index("ix_resource_sources_resource_sync", "resource_sources", ["resource_id", "can_sync"])

    op.add_column("work_events", sa.Column("source_type", sa.String(length=80), nullable=False, server_default="unknown"))
    op.add_column("work_events", sa.Column("source_account_id", sa.String(length=300), nullable=True))
    op.add_column("work_events", sa.Column("visibility_scope", sa.String(length=80), nullable=False, server_default="company"))
    op.add_column("work_events", sa.Column("allowed_user_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("work_events", sa.Column("allowed_roles", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("work_events", sa.Column("allowed_departments", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.alter_column("resource_sources", "source_account_id", server_default=None)
    op.alter_column("resource_sources", "access_level", server_default=None)
    op.alter_column("resource_sources", "can_sync", server_default=None)
    op.alter_column("resource_sources", "priority", server_default=None)
    op.alter_column("resource_sources", "visibility_scope", server_default=None)
    op.alter_column("resource_sources", "allowed_user_ids", server_default=None)
    op.alter_column("resource_sources", "allowed_roles", server_default=None)
    op.alter_column("resource_sources", "allowed_departments", server_default=None)
    op.alter_column("resource_sources", "limitations", server_default=None)
    op.alter_column("resource_sources", "settings", server_default=None)
    op.alter_column("resource_sources", "last_seen_at", server_default=None)
    op.alter_column("work_events", "source_type", server_default=None)
    op.alter_column("work_events", "visibility_scope", server_default=None)
    op.alter_column("work_events", "allowed_user_ids", server_default=None)
    op.alter_column("work_events", "allowed_roles", server_default=None)
    op.alter_column("work_events", "allowed_departments", server_default=None)


def downgrade() -> None:
    op.drop_column("work_events", "allowed_departments")
    op.drop_column("work_events", "allowed_roles")
    op.drop_column("work_events", "allowed_user_ids")
    op.drop_column("work_events", "visibility_scope")
    op.drop_column("work_events", "source_account_id")
    op.drop_column("work_events", "source_type")
    op.drop_index("ix_resource_sources_resource_sync", table_name="resource_sources")
    op.drop_index("ix_resource_sources_company_type", table_name="resource_sources")
    op.drop_table("resource_sources")
