"""Enforce resource identity uniqueness with nullable sub ids.

Revision ID: 0020_resource_identity_partial_unique_indexes
Revises: 0019_drop_retired_feishu_resources
Create Date: 2026-06-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0020_resource_identity_partial_unique_indexes"
down_revision: str | None = "0019_drop_retired_feishu_resources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        create temporary table duplicate_resource_ids on commit drop as
        select id, keep_id
        from (
            select
                id,
                first_value(id) over (
                    partition by company_id, platform, resource_type, resource_id, resource_sub_id
                    order by created_at asc, id asc
                ) as keep_id,
                row_number() over (
                    partition by company_id, platform, resource_type, resource_id, resource_sub_id
                    order by created_at asc, id asc
                ) as row_number
            from resources
        ) ranked
        where row_number > 1
        """
    )
    op.execute(
        """
        update work_events
        set resource_id = duplicate_resource_ids.keep_id
        from duplicate_resource_ids
        where work_events.resource_id = duplicate_resource_ids.id
        """
    )
    op.execute(
        """
        update resource_sync_runs
        set resource_id = duplicate_resource_ids.keep_id
        from duplicate_resource_ids
        where resource_sync_runs.resource_id = duplicate_resource_ids.id
        """
    )
    op.execute(
        """
        delete from resource_sources
        using duplicate_resource_ids
        where resource_sources.resource_id = duplicate_resource_ids.id
        """
    )
    op.execute(
        """
        delete from resource_permissions
        using duplicate_resource_ids
        where resource_permissions.resource_id = duplicate_resource_ids.id
        """
    )
    op.execute(
        """
        delete from resources
        using duplicate_resource_ids
        where resources.id = duplicate_resource_ids.id
        """
    )
    op.create_index(
        "uq_resources_identity_null_sub_id",
        "resources",
        ["company_id", "platform", "resource_type", "resource_id"],
        unique=True,
        postgresql_where=sa.text("resource_sub_id IS NULL"),
    )
    op.create_index(
        "uq_resources_identity_with_sub_id",
        "resources",
        ["company_id", "platform", "resource_type", "resource_id", "resource_sub_id"],
        unique=True,
        postgresql_where=sa.text("resource_sub_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_resources_identity_with_sub_id", table_name="resources")
    op.drop_index("uq_resources_identity_null_sub_id", table_name="resources")
