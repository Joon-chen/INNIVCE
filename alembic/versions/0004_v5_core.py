"""v5 core workspace resource binding

Revision ID: 0004_v5_core
Revises: 0003_v4_phase1
Create Date: 2026-06-10 00:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_v5_core"
down_revision: Union[str, None] = "0003_v4_phase1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("status", sa.String(length=40), nullable=False, server_default="active"))
    op.add_column("work_events", sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "work_events",
        sa.Column(
            "raw_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "work_events",
        sa.Column("importance_score", sa.Float(), nullable=False, server_default="0"),
    )
    op.execute("UPDATE work_events SET raw_json = COALESCE(payload, '{}'::jsonb) WHERE raw_json = '{}'::jsonb")
    op.create_foreign_key("fk_work_events_resource_id_resources", "work_events", "resources", ["resource_id"], ["id"])
    op.create_index("ix_work_events_resource_occurred", "work_events", ["resource_id", "occurred_at"])
    op.alter_column("companies", "status", server_default=None)
    op.alter_column("work_events", "raw_json", server_default=None)
    op.alter_column("work_events", "importance_score", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_work_events_resource_occurred", table_name="work_events")
    op.drop_constraint("fk_work_events_resource_id_resources", "work_events", type_="foreignkey")
    op.drop_column("work_events", "importance_score")
    op.drop_column("work_events", "raw_json")
    op.drop_column("work_events", "resource_id")
    op.drop_column("companies", "status")
