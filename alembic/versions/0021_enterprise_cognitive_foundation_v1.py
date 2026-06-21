"""Add enterprise cognitive foundation V1 tables.

Revision ID: 0021_enterprise_cognitive_foundation_v1
Revises: 0020_resource_identity_partial_unique_indexes
Create Date: 2026-06-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0021_enterprise_cognitive_foundation_v1"
down_revision: str | None = "0020_resource_identity_partial_unique_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("work_events", sa.Column("object_type", sa.String(length=80), nullable=False, server_default="unknown"))
    op.add_column("work_events", sa.Column("object_id", sa.String(length=500), nullable=False, server_default=""))
    op.add_column("work_events", sa.Column("actor", sa.String(length=300), nullable=False, server_default="system"))
    op.create_index("ix_work_events_company_object", "work_events", ["company_id", "object_type", "object_id"])
    op.alter_column("work_events", "object_type", server_default=None)
    op.alter_column("work_events", "object_id", server_default=None)
    op.alter_column("work_events", "actor", server_default=None)

    op.create_table(
        "snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("object_type", sa.String(length=80), nullable=False),
        sa.Column("object_id", sa.String(length=500), nullable=False),
        sa.Column("snapshot_type", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("recommendation", sa.String(length=120), nullable=False),
        sa.Column("risk_level", sa.String(length=40), nullable=False),
        sa.Column("reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_event_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "object_type", "object_id", "snapshot_type", name="uq_snapshots_company_object_type"),
    )
    op.create_index("ix_snapshots_company_object", "snapshots", ["company_id", "object_type", "object_id"])
    op.create_index("ix_snapshots_company_status", "snapshots", ["company_id", "snapshot_type", "status"])

    op.create_table(
        "memory_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("memory_type", sa.String(length=120), nullable=False),
        sa.Column("object_type", sa.String(length=80), nullable=False),
        sa.Column("object_id", sa.String(length=500), nullable=False),
        sa.Column("evidence_event_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("candidate_text", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_candidates_company_object", "memory_candidates", ["company_id", "object_type", "object_id"])
    op.create_index("ix_memory_candidates_company_status", "memory_candidates", ["company_id", "memory_type", "status"])


def downgrade() -> None:
    op.drop_index("ix_memory_candidates_company_status", table_name="memory_candidates")
    op.drop_index("ix_memory_candidates_company_object", table_name="memory_candidates")
    op.drop_table("memory_candidates")

    op.drop_index("ix_snapshots_company_status", table_name="snapshots")
    op.drop_index("ix_snapshots_company_object", table_name="snapshots")
    op.drop_table("snapshots")

    op.drop_index("ix_work_events_company_object", table_name="work_events")
    op.drop_column("work_events", "actor")
    op.drop_column("work_events", "object_id")
    op.drop_column("work_events", "object_type")
