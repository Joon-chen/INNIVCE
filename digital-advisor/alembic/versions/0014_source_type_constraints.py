"""add source type constraints

Revision ID: 0014_source_type_constraints
Revises: 0013_data_layer_source_taxonomy
Create Date: 2026-06-13 18:40:00.000000
"""

from alembic import op


revision = "0014_source_type_constraints"
down_revision = "0013_data_layer_source_taxonomy"
branch_labels = None
depends_on = None


ALLOWED_SOURCE_TYPES = (
    "feishu_app_identity",
    "feishu_user_identity",
    "external_mail_account",
    "personal_dingtalk_account",
    "external_web",
    "local_import",
    "external_connector",
    "unknown",
)


def _source_type_check_sql(column: str = "source_type") -> str:
    allowed = ", ".join(f"'{item}'" for item in ALLOWED_SOURCE_TYPES)
    return f"{column} in ({allowed})"


def upgrade() -> None:
    op.create_check_constraint(
        "ck_resource_sources_source_type_v5",
        "resource_sources",
        _source_type_check_sql(),
    )
    op.create_check_constraint(
        "ck_work_events_source_type_v5",
        "work_events",
        _source_type_check_sql(),
    )


def downgrade() -> None:
    op.drop_constraint("ck_work_events_source_type_v5", "work_events", type_="check")
    op.drop_constraint("ck_resource_sources_source_type_v5", "resource_sources", type_="check")
