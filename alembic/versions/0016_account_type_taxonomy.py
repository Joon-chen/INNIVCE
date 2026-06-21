"""add account type taxonomy

Revision ID: 0016_account_type_taxonomy
Revises: 0015_memory_fact_scope_constraints
Create Date: 2026-06-13 20:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0016_account_type_taxonomy"
down_revision = "0015_memory_fact_scope_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("account_type", sa.String(length=80), nullable=False, server_default="external_mail"),
    )
    op.execute(
        """
        update accounts
        set account_type = case
            when provider in ('feishu_app', 'lark_app', 'tenant_app') then 'company_feishu_app'
            when provider in ('feishu_user', 'lark_user') then 'personal_feishu_user'
            when provider in ('feishu_mail', 'feishu_mailbox', 'lark_mail', 'lark_mailbox') then 'feishu_mail'
            when provider in ('dingtalk', 'dingtalk_user') then 'personal_dingtalk'
            when provider in ('imap', 'gmail', 'graph', 'outlook', 'mail', 'email', 'mail_account') then 'external_mail'
            else 'external_mail'
        end
        """
    )
    op.create_check_constraint(
        "ck_accounts_account_type_v5",
        "accounts",
        "account_type in ("
        "'company_feishu_app', 'personal_feishu_user', 'feishu_mail', "
        "'external_mail', 'personal_dingtalk'"
        ")",
    )


def downgrade() -> None:
    op.drop_constraint("ck_accounts_account_type_v5", "accounts", type_="check")
    op.drop_column("accounts", "account_type")
