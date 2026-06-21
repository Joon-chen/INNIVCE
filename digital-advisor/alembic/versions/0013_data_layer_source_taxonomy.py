"""normalize data layer source taxonomy

Revision ID: 0013_data_layer_source_taxonomy
Revises: 0012_tool_configs
Create Date: 2026-06-13 09:25:00.000000
"""

from alembic import op


revision = "0013_data_layer_source_taxonomy"
down_revision = "0012_tool_configs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        update resource_sources
        set source_type = case
            when source_type in ('feishu_app', 'lark_app', 'tenant_app', 'app_identity') then 'feishu_app_identity'
            when source_type in ('feishu_user', 'lark_user', 'user_identity') then 'feishu_user_identity'
            when source_type in ('mail_account', 'gmail', 'graph', 'imap') then 'external_mail_account'
            when source_type in ('dingtalk', 'dingtalk_user') then 'personal_dingtalk_account'
            when source_type in ('manual_import', 'csv_import') then 'local_import'
            when source_type = 'web' then 'external_web'
            else source_type
        end
        """
    )
    op.execute(
        """
        update work_events
        set source_type = case
            when source_type in ('feishu_app', 'lark_app', 'tenant_app', 'app_identity') then 'feishu_app_identity'
            when source_type in ('feishu_user', 'lark_user', 'user_identity') then 'feishu_user_identity'
            when source_type in ('mail_account', 'gmail', 'graph', 'imap') then 'external_mail_account'
            when source_type in ('dingtalk', 'dingtalk_user') then 'personal_dingtalk_account'
            when source_type in ('manual_import', 'csv_import') then 'local_import'
            when source_type = 'web' then 'external_web'
            else source_type
        end
        """
    )


def downgrade() -> None:
    op.execute(
        """
        update resource_sources
        set source_type = case
            when source_type = 'feishu_app_identity' then 'feishu_app'
            when source_type = 'feishu_user_identity' then 'feishu_user'
            when source_type = 'external_mail_account' then 'mail_account'
            when source_type = 'personal_dingtalk_account' then 'dingtalk'
            when source_type = 'external_web' then 'web'
            else source_type
        end
        """
    )
    op.execute(
        """
        update work_events
        set source_type = case
            when source_type = 'feishu_app_identity' then 'feishu_app'
            when source_type = 'feishu_user_identity' then 'feishu_user'
            when source_type = 'external_mail_account' then 'mail_account'
            when source_type = 'personal_dingtalk_account' then 'dingtalk'
            when source_type = 'external_web' then 'web'
            else source_type
        end
        """
    )
