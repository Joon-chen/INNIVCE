"""add tool configs

Revision ID: 0012_tool_configs
Revises: 0011_v5_resource_domain_backfill
Create Date: 2026-06-12 14:30:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0012_tool_configs"
down_revision = "0011_v5_resource_domain_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tool_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_name", sa.String(length=120), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=True),
        sa.Column("required_permissions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("supports_write", sa.Boolean(), nullable=True),
        sa.Column("audit_action", sa.String(length=120), nullable=False),
        sa.Column("config_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "tool_name", name="uq_tool_config_company_tool"),
    )
    op.create_index("ix_tool_configs_company_enabled", "tool_configs", ["company_id", "enabled"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tool_configs_company_enabled", table_name="tool_configs")
    op.drop_table("tool_configs")
