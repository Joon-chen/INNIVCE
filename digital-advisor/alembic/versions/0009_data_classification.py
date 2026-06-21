"""add data classification and business domain

Revision ID: 0009_data_classification
Revises: 0008_backfill_resource_sources
Create Date: 2026-06-12 18:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0009_data_classification"
down_revision: Union[str, None] = "0008_backfill_resource_sources"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("resources", sa.Column("data_classification", sa.String(length=80), nullable=False, server_default="company"))
    op.add_column("resources", sa.Column("business_domain", sa.String(length=120), nullable=False, server_default="general"))
    op.add_column(
        "work_events",
        sa.Column("data_classification", sa.String(length=80), nullable=False, server_default="company"),
    )
    op.add_column("work_events", sa.Column("business_domain", sa.String(length=120), nullable=False, server_default="general"))
    op.create_index("ix_resources_company_classification", "resources", ["company_id", "data_classification"])
    op.create_index("ix_work_events_company_classification", "work_events", ["company_id", "data_classification"])

    op.execute(
        """
        update resources
        set
            data_classification = case
                when platform = 'feishu' and resource_id like 'feishu:user:%' then 'personal'
                else 'company'
            end,
            business_domain = case
                when resource_type in ('approval_code', 'approval') then 'approval'
                when resource_type in ('mail_folder', 'mailbox') then 'communications'
                when resource_type = 'chat' then 'communications'
                when resource_type in ('bitable_app', 'bitable_table') then 'operations'
                when resource_type in ('drive_file', 'wiki_space') then 'knowledge'
                else 'general'
            end,
            permission_level = case
                when resource_type in ('mail_folder', 'mailbox') and permission_level = 'private' then 'company_management'
                else permission_level
            end
        """
    )
    op.execute(
        """
        update resource_sources s
        set visibility_scope = 'company_management'
        from resources r
        where s.resource_id = r.id
          and s.source_type = 'mail_account'
          and s.visibility_scope = 'private'
        """
    )
    op.execute(
        """
        update work_events
        set
            data_classification = case
                when source_type = 'feishu_user' and visibility_scope in ('private', 'owner', 'user') then 'personal'
                else 'company'
            end,
            business_domain = case
                when event_type like '%approval%' then 'approval'
                when event_type like 'mail.%' then 'communications'
                when event_type like '%message%' then 'communications'
                when event_type like '%calendar%' or event_type like '%meeting%' then 'meeting'
                else coalesce(payload ->> 'business_domain', 'general')
            end,
            visibility_scope = case
                when source_type = 'mail_account' and visibility_scope = 'private' then 'company_management'
                else visibility_scope
            end
        """
    )

    op.alter_column("resources", "data_classification", server_default=None)
    op.alter_column("resources", "business_domain", server_default=None)
    op.alter_column("work_events", "data_classification", server_default=None)
    op.alter_column("work_events", "business_domain", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_work_events_company_classification", table_name="work_events")
    op.drop_index("ix_resources_company_classification", table_name="resources")
    op.drop_column("work_events", "business_domain")
    op.drop_column("work_events", "data_classification")
    op.drop_column("resources", "business_domain")
    op.drop_column("resources", "data_classification")
