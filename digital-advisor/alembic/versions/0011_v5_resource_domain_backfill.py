"""backfill V5 resource domains

Revision ID: 0011_v5_resource_domain_backfill
Revises: 0010_mail_personal_access
Create Date: 2026-06-12 11:10:00.000000
"""

from alembic import op


revision = "0011_v5_resource_domain_backfill"
down_revision = "0010_mail_personal_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        update resources
        set data_classification = 'company',
            business_domain = 'communications',
            permission_level = 'team'
        where platform = 'feishu'
          and resource_type = 'chat'
        """
    )
    op.execute(
        """
        update resources
        set data_classification = 'personal',
            business_domain = 'communications',
            permission_level = 'owner'
        where resource_type in ('mail_folder', 'mailbox')
           or platform in ('mail', 'email')
        """
    )
    op.execute(
        """
        update resources
        set data_classification = 'company',
            business_domain = 'administration',
            permission_level = 'department'
        where platform = 'feishu'
          and resource_type = 'approval_code'
        """
    )
    op.execute(
        """
        update resources
        set data_classification = 'company',
            business_domain = 'operations',
            permission_level = 'department'
        where platform = 'feishu'
          and resource_type in ('bitable_app', 'bitable_table')
        """
    )
    op.execute(
        """
        update resources
        set data_classification = 'company',
            business_domain = 'knowledge',
            permission_level = 'department'
        where platform = 'feishu'
          and resource_type in ('drive_file', 'wiki_space')
        """
    )
    op.execute(
        """
        update resources
        set data_classification = 'company',
            business_domain = case
                when resource_id = 'feishu:contacts' then 'administration'
                when resource_id in ('feishu:calendar', 'feishu:meetings') then 'meeting'
                when resource_id = 'feishu:tasks' then 'task'
                else business_domain
            end,
            permission_level = 'company'
        where platform = 'feishu'
          and resource_type = 'capability'
        """
    )
    op.execute(
        """
        update resource_sources rs
        set visibility_scope = r.permission_level
        from resources r
        where rs.resource_id = r.id
          and rs.source_type = 'feishu_app'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        update resources
        set data_classification = 'company',
            business_domain = 'general'
        where platform = 'feishu'
          and resource_type in (
            'chat',
            'approval_code',
            'bitable_app',
            'bitable_table',
            'drive_file',
            'wiki_space',
            'capability'
          )
        """
    )
