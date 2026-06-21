"""bot context and user memory scope

Revision ID: 0006_bot_context_memory
Revises: 0005_resource_sync_runs
Create Date: 2026-06-12 00:30:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0006_bot_context_memory"
down_revision: Union[str, None] = "0005_resource_sync_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bot_user_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_open_id", sa.String(length=200), nullable=False),
        sa.Column("chat_id", sa.String(length=300), nullable=True),
        sa.Column("session_key", sa.String(length=600), nullable=False),
        sa.Column("last_intent", sa.String(length=120), nullable=True),
        sa.Column("last_route", sa.String(length=120), nullable=True),
        sa.Column("last_scope", sa.String(length=120), nullable=True),
        sa.Column("short_context", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "session_key", name="uq_bot_user_session_company_key"),
    )
    op.create_index("ix_bot_user_sessions_user", "bot_user_sessions", ["company_id", "user_open_id"])
    op.create_index("ix_bot_user_sessions_chat", "bot_user_sessions", ["company_id", "chat_id"])

    op.create_table(
        "bot_user_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_open_id", sa.String(length=200), nullable=False),
        sa.Column("answer_style", sa.Text(), nullable=True),
        sa.Column("preferred_language", sa.String(length=40), nullable=False, server_default="zh-CN"),
        sa.Column("favorite_modules", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "user_open_id", name="uq_bot_user_preference_company_user"),
    )
    op.create_index("ix_bot_user_preferences_user", "bot_user_preferences", ["company_id", "user_open_id"])

    op.add_column("memory_facts", sa.Column("scope", sa.String(length=80), nullable=False, server_default="company"))
    op.add_column("memory_facts", sa.Column("user_open_id", sa.String(length=200), nullable=True))
    op.add_column("memory_facts", sa.Column("chat_id", sa.String(length=300), nullable=True))
    op.add_column("memory_facts", sa.Column("source_kind", sa.String(length=80), nullable=False, server_default="extracted_fact"))
    op.add_column("memory_facts", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_memory_facts_user_scope", "memory_facts", ["company_id", "user_open_id", "scope"])
    op.create_index("ix_memory_facts_chat_scope", "memory_facts", ["company_id", "chat_id", "scope"])


def downgrade() -> None:
    op.drop_index("ix_memory_facts_chat_scope", table_name="memory_facts")
    op.drop_index("ix_memory_facts_user_scope", table_name="memory_facts")
    op.drop_column("memory_facts", "expires_at")
    op.drop_column("memory_facts", "source_kind")
    op.drop_column("memory_facts", "chat_id")
    op.drop_column("memory_facts", "user_open_id")
    op.drop_column("memory_facts", "scope")

    op.drop_index("ix_bot_user_preferences_user", table_name="bot_user_preferences")
    op.drop_table("bot_user_preferences")

    op.drop_index("ix_bot_user_sessions_chat", table_name="bot_user_sessions")
    op.drop_index("ix_bot_user_sessions_user", table_name="bot_user_sessions")
    op.drop_table("bot_user_sessions")
