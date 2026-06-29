"""move feishu realtime tool configs to mcp

Revision ID: 0017_tool_configs_feishu_mcp_defaults
Revises: 0016_account_type_taxonomy
Create Date: 2026-06-13 22:20:00.000000
"""

from alembic import op


revision = "0017_tool_configs_feishu_mcp_defaults"
down_revision = "0016_account_type_taxonomy"
branch_labels = None
depends_on = None


FEISHU_REALTIME_TOOL_NAMES = (
    "bitable_qa",
    "calendar_qa",
    "feishu_approval_instance_cancel",
    "feishu_approval_instance_cc",
    "feishu_approval_instance_get",
    "feishu_approval_instance_remind",
    "feishu_approval_task_add_sign",
    "feishu_approval_task_approve",
    "feishu_approval_task_query",
    "feishu_approval_task_reject",
    "feishu_approval_task_rollback",
    "feishu_approval_task_transfer",
    "feishu_bitable_field_create",
    "feishu_bitable_field_delete",
    "feishu_bitable_field_list",
    "feishu_bitable_field_update",
    "feishu_bitable_record_batch_create",
    "feishu_bitable_record_batch_delete",
    "feishu_bitable_record_batch_update",
    "feishu_bitable_record_create",
    "feishu_bitable_record_delete",
    "feishu_bitable_record_remove_attachment",
    "feishu_bitable_record_update",
    "feishu_bitable_record_upload_attachment",
    "feishu_bitable_record_upsert",
    "feishu_bitable_table_create",
    "feishu_bitable_table_delete",
    "feishu_bitable_table_update",
    "feishu_bitable_view_create",
    "feishu_bitable_view_delete",
    "feishu_bitable_view_get_card",
    "feishu_bitable_view_get_timebar",
    "feishu_bitable_view_get_visible_fields",
    "feishu_bitable_view_rename",
    "feishu_bitable_view_set_card",
    "feishu_bitable_view_set_filter",
    "feishu_bitable_view_set_group",
    "feishu_bitable_view_set_sort",
    "feishu_bitable_view_set_timebar",
    "feishu_bitable_view_set_visible_fields",
    "feishu_calendar_create_event",
    "feishu_contact_department_children",
    "feishu_contact_department_users",
    "feishu_contact_organization_snapshot",
    "feishu_contact_scope_list",
    "feishu_drive_file_list",
    "feishu_im_auto_join_public_chats",
    "feishu_im_chat_search",
    "feishu_im_create_chat",
    "feishu_im_message_list",
    "feishu_im_send_message",
    "feishu_mail_folder_list",
    "feishu_mail_message_get",
    "feishu_okr_cycle_list",
    "feishu_okr_objective_list",
    "feishu_vc_meeting_search",
    "feishu_wiki_node_list",
    "feishu_wiki_space_list",
    "feishu_task_add_to_tasklist",
    "feishu_task_assign_members",
    "feishu_task_clear_ancestor",
    "feishu_task_comment",
    "feishu_task_complete",
    "feishu_task_create",
    "feishu_task_delete",
    "feishu_task_reopen",
    "feishu_task_section_create",
    "feishu_task_section_delete",
    "feishu_task_section_update",
    "feishu_task_set_ancestor",
    "feishu_task_subtask_create",
    "feishu_task_update",
    "feishu_task_update_followers",
    "feishu_task_update_reminders",
    "feishu_task_upload_attachment",
    "feishu_tasklist_create",
    "feishu_tasklist_delete",
    "feishu_tasklist_set_members",
    "feishu_tasklist_update",
    "feishu_tasklist_update_members",
    "mail_qa",
    "task_qa",
)


def _tool_names_sql() -> str:
    return ", ".join(f"'{name}'" for name in FEISHU_REALTIME_TOOL_NAMES)


def upgrade() -> None:
    op.execute(
        f"""
        update tool_configs
        set provider = 'feishu_mcp'
        where provider = 'feishu_api'
          and tool_name in ({_tool_names_sql()})
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        update tool_configs
        set provider = 'feishu_api'
        where provider = 'feishu_mcp'
          and tool_name in ({_tool_names_sql()})
        """
    )
