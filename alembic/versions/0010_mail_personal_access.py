"""mark mail resources and events as personal access

Revision ID: 0010_mail_personal_access
Revises: 0009_data_classification
Create Date: 2026-06-12 19:00:00
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0010_mail_personal_access"
down_revision: Union[str, None] = "0009_data_classification"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        update resources
        set
            data_classification = 'personal',
            business_domain = 'communications',
            permission_level = 'owner',
            updated_at = now()
        where resource_type in ('mail_folder', 'mailbox')
           or platform in ('mail', 'email')
        """
    )
    op.execute(
        """
        update resource_sources
        set
            visibility_scope = 'owner',
            updated_at = now()
        where source_type = 'mail_account'
        """
    )
    op.execute(
        """
        update work_events
        set
            data_classification = 'personal',
            visibility_scope = 'owner',
            business_domain = coalesce(nullif(business_domain, ''), 'communications'),
            updated_at = now()
        where source_type = 'mail_account'
           or source in ('imap', 'gmail', 'graph', 'mail', 'email')
           or event_type like 'mail.%'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        update resources
        set
            data_classification = 'company',
            permission_level = 'company_management',
            updated_at = now()
        where resource_type in ('mail_folder', 'mailbox')
           or platform in ('mail', 'email')
        """
    )
    op.execute("update resource_sources set visibility_scope = 'company_management' where source_type = 'mail_account'")
    op.execute(
        """
        update work_events
        set data_classification = 'company', visibility_scope = 'company_management', updated_at = now()
        where source_type = 'mail_account'
           or source in ('imap', 'gmail', 'graph', 'mail', 'email')
           or event_type like 'mail.%'
        """
    )
