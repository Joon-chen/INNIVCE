"""Drop retired feishu_resources table after V5 migration.

Revision ID: 0019_drop_retired_feishu_resources
Revises: 0018_detach_resource_from_legacy_feishu_table
Create Date: 2026-06-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision: str = "0019_drop_retired_feishu_resources"
down_revision: str | None = "0018_detach_resource_from_legacy_feishu_table"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names())
    if "feishu_resources" not in table_names:
        return
    if "resources" in table_names:
        unmapped_count = bind.scalar(
            sa.text(
                """
                select count(*)
                from feishu_resources legacy
                where not exists (
                    select 1
                    from resources resource
                    where resource.legacy_feishu_resource_id = legacy.id
                )
                """
            )
        )
        if unmapped_count:
            raise RuntimeError(
                "Cannot drop feishu_resources before all legacy rows are mapped to V5 resources. "
                "Run V5 administration bootstrap and verify legacy_retirement.unmapped_legacy_resources=0."
            )
    index_names = {index.get("name") for index in inspector.get_indexes("feishu_resources")}
    if "ix_feishu_resources_company_type" in index_names:
        op.drop_index("ix_feishu_resources_company_type", table_name="feishu_resources")
    op.drop_table("feishu_resources")


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    if "feishu_resources" in set(inspector.get_table_names()):
        return
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
