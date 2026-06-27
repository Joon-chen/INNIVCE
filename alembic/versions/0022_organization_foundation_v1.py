"""Add organization foundation V1 tables.

Revision ID: 0022_organization_foundation_v1
Revises: 0021_enterprise_cognitive_foundation_v1
Create Date: 2026-06-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0022_organization_foundation_v1"
down_revision: str | None = "0021_enterprise_cognitive_foundation_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organization_departments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system", sa.String(length=80), nullable=False),
        sa.Column("source_department_id", sa.String(length=300), nullable=False),
        sa.Column("open_department_id", sa.String(length=300), nullable=True),
        sa.Column("parent_source_department_id", sa.String(length=300), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("unit_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("path_names", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("path_source_department_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("leader_source_user_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "source_system", "source_department_id", name="uq_org_department_source"),
    )
    op.create_index("ix_org_departments_company_name", "organization_departments", ["company_id", "normalized_name"])
    op.create_index(
        "ix_org_departments_company_parent",
        "organization_departments",
        ["company_id", "parent_source_department_id"],
    )

    op.create_table(
        "organization_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system", sa.String(length=80), nullable=False),
        sa.Column("source_user_id", sa.String(length=300), nullable=True),
        sa.Column("open_id", sa.String(length=300), nullable=False),
        sa.Column("union_id", sa.String(length=300), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("mobile", sa.String(length=120), nullable=True),
        sa.Column("job_title", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("source_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "source_system", "open_id", name="uq_org_user_source_open_id"),
    )
    op.create_index("ix_org_users_company_name", "organization_users", ["company_id", "normalized_name"])
    op.create_index("ix_org_users_company_source_user", "organization_users", ["company_id", "source_user_id"])

    op.create_table(
        "organization_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_department_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system", sa.String(length=80), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("role_in_department", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("source_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["organization_department_id"], ["organization_departments.id"]),
        sa.ForeignKeyConstraint(["organization_user_id"], ["organization_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "organization_user_id",
            "organization_department_id",
            name="uq_org_membership_user_department",
        ),
    )
    op.create_index(
        "ix_org_memberships_company_department",
        "organization_memberships",
        ["company_id", "organization_department_id"],
    )
    op.create_index("ix_org_memberships_company_user", "organization_memberships", ["company_id", "organization_user_id"])

    op.create_table(
        "organization_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alias", sa.String(length=200), nullable=False),
        sa.Column("normalized_alias", sa.String(length=200), nullable=False),
        sa.Column("target_type", sa.String(length=80), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system", sa.String(length=80), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "normalized_alias", "target_type", "target_id", name="uq_org_alias_target"),
    )
    op.create_index(
        "ix_org_aliases_company_alias",
        "organization_aliases",
        ["company_id", "normalized_alias", "is_active"],
    )

    op.create_table(
        "organization_sync_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system", sa.String(length=80), nullable=False),
        sa.Column("sync_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("department_count", sa.Integer(), nullable=False),
        sa.Column("user_count", sa.Integer(), nullable=False),
        sa.Column("membership_count", sa.Integer(), nullable=False),
        sa.Column("cursor", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_org_sync_runs_company_started", "organization_sync_runs", ["company_id", "started_at"])
    op.create_index("ix_org_sync_runs_source_status", "organization_sync_runs", ["source_system", "status"])


def downgrade() -> None:
    op.drop_index("ix_org_sync_runs_source_status", table_name="organization_sync_runs")
    op.drop_index("ix_org_sync_runs_company_started", table_name="organization_sync_runs")
    op.drop_table("organization_sync_runs")

    op.drop_index("ix_org_aliases_company_alias", table_name="organization_aliases")
    op.drop_table("organization_aliases")

    op.drop_index("ix_org_memberships_company_user", table_name="organization_memberships")
    op.drop_index("ix_org_memberships_company_department", table_name="organization_memberships")
    op.drop_table("organization_memberships")

    op.drop_index("ix_org_users_company_source_user", table_name="organization_users")
    op.drop_index("ix_org_users_company_name", table_name="organization_users")
    op.drop_table("organization_users")

    op.drop_index("ix_org_departments_company_parent", table_name="organization_departments")
    op.drop_index("ix_org_departments_company_name", table_name="organization_departments")
    op.drop_table("organization_departments")
