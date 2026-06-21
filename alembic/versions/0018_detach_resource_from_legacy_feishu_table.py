"""Detach resources from legacy feishu_resources table.

Revision ID: 0018_detach_resource_from_legacy_feishu_table
Revises: 0017_tool_configs_feishu_mcp_defaults
Create Date: 2026-06-13
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import inspect


revision: str = "0018_detach_resource_from_legacy_feishu_table"
down_revision: str | None = "0017_tool_configs_feishu_mcp_defaults"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    for foreign_key in inspector.get_foreign_keys("resources"):
        if foreign_key.get("constrained_columns") == ["legacy_feishu_resource_id"]:
            constraint_name = foreign_key.get("name")
            if constraint_name:
                op.drop_constraint(constraint_name, "resources", type_="foreignkey")
            break


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    table_names = set(inspector.get_table_names())
    if "feishu_resources" not in table_names:
        return
    for foreign_key in inspector.get_foreign_keys("resources"):
        if foreign_key.get("constrained_columns") == ["legacy_feishu_resource_id"]:
            return
    op.create_foreign_key(
        "resources_legacy_feishu_resource_id_fkey",
        "resources",
        "feishu_resources",
        ["legacy_feishu_resource_id"],
        ["id"],
    )
