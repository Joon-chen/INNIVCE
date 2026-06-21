"""operational advisor tables

Revision ID: 0002_operational_advisor_tables
Revises: 0001_initial_schema
Create Date: 2026-06-09 00:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_operational_advisor_tables"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sync_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("sync_type", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("saved_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("cursor", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sync_runs_company_started", "sync_runs", ["company_id", "started_at"])
    op.create_index("ix_sync_runs_provider_type", "sync_runs", ["provider", "sync_type"])

    op.create_table(
        "bot_user_access",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("open_id", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=True),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.Column("access_scope", sa.String(length=40), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "open_id", name="uq_bot_user_company_open_id"),
    )
    op.create_index("ix_bot_user_open_id", "bot_user_access", ["open_id"])

    op.create_table(
        "feishu_resources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("app_config_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("external_id", sa.String(length=500), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=True),
        sa.Column("sync_enabled", sa.Boolean(), nullable=False),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["app_config_id"], ["feishu_app_configs.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "resource_type", "external_id", name="uq_feishu_resource_external"),
    )
    op.create_index("ix_feishu_resources_company_type", "feishu_resources", ["company_id", "resource_type"])

    op.create_table(
        "memory_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fact_type", sa.String(length=80), nullable=False),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("confidence", sa.String(length=40), nullable=False),
        sa.Column("source_work_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["source_work_event_id"], ["work_events.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_facts_company_type", "memory_facts", ["company_id", "fact_type"])
    op.create_index("ix_memory_facts_subject", "memory_facts", ["subject"])

    op.create_table(
        "people",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("external_id", sa.String(length=300), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("role_title", sa.String(length=200), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_people_company_name", "people", ["company_id", "name"])

    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=80), nullable=False),
        sa.Column("owner", sa.String(length=200), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_projects_company_status", "projects", ["company_id", "status"])

    op.create_table(
        "customers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=80), nullable=False),
        sa.Column("owner", sa.String(length=200), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_customers_company_status", "customers", ["company_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_customers_company_status", table_name="customers")
    op.drop_table("customers")
    op.drop_index("ix_projects_company_status", table_name="projects")
    op.drop_table("projects")
    op.drop_index("ix_people_company_name", table_name="people")
    op.drop_table("people")
    op.drop_index("ix_memory_facts_subject", table_name="memory_facts")
    op.drop_index("ix_memory_facts_company_type", table_name="memory_facts")
    op.drop_table("memory_facts")
    op.drop_index("ix_feishu_resources_company_type", table_name="feishu_resources")
    op.drop_table("feishu_resources")
    op.drop_index("ix_bot_user_open_id", table_name="bot_user_access")
    op.drop_table("bot_user_access")
    op.drop_index("ix_sync_runs_provider_type", table_name="sync_runs")
    op.drop_index("ix_sync_runs_company_started", table_name="sync_runs")
    op.drop_table("sync_runs")
