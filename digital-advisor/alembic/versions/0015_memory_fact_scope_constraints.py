"""add memory fact scope constraints

Revision ID: 0015_memory_fact_scope_constraints
Revises: 0014_source_type_constraints
Create Date: 2026-06-13 19:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0015_memory_fact_scope_constraints"
down_revision = "0014_source_type_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("alembic_version", "version_num", existing_type=sa.String(length=32), type_=sa.String(length=128))
    op.execute(
        """
        update memory_facts
        set scope = 'company'
        where scope is null or scope not in ('company', 'domain', 'personal', 'user', 'chat')
        """
    )
    op.execute(
        """
        update memory_facts
        set scope = 'company'
        where scope in ('personal', 'user') and user_open_id is null
        """
    )
    op.execute(
        """
        update memory_facts
        set scope = 'company'
        where scope = 'chat' and chat_id is null
        """
    )
    op.create_check_constraint(
        "ck_memory_facts_scope_v5",
        "memory_facts",
        "scope in ('company', 'domain', 'personal', 'user', 'chat')",
    )
    op.create_check_constraint(
        "ck_memory_facts_personal_owner",
        "memory_facts",
        "(scope not in ('personal', 'user') or user_open_id is not null)",
    )
    op.create_check_constraint(
        "ck_memory_facts_chat_context",
        "memory_facts",
        "(scope != 'chat' or chat_id is not null)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_memory_facts_chat_context", "memory_facts", type_="check")
    op.drop_constraint("ck_memory_facts_personal_owner", "memory_facts", type_="check")
    op.drop_constraint("ck_memory_facts_scope_v5", "memory_facts", type_="check")
