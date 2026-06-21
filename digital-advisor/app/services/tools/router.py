from dataclasses import replace
import json
from time import perf_counter
from urllib.parse import urlencode
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.models.entities import BotUserAccess
from app.services.audit import write_audit_log
from app.services.agent.policies import BotActor
from app.services.tools.base import (
    DATA_BOUNDARY_POLICY,
    DATA_PERMISSION_MODEL,
    DIGITAL_ADVISOR_PERMISSION_POLICY,
    ENTERPRISE_IDENTITY_CONSTRAINTS,
    ENTERPRISE_IDENTITY_RESOURCE_OWNER,
    TOOL_ACCESS_POLICY,
    USER_IDENTITY_BUNDLE_RESOURCE,
    USER_IDENTITY_CONSTRAINTS,
    USER_IDENTITY_RESOURCES,
    ToolContext,
    ToolDefinition,
    ToolExecutionStatus,
    ToolProvider,
    ToolRequest,
    ToolResult,
    TOOL_SHARING_MODEL,
    SHARED_TOOL_COUNT,
    identity_permission_contract,
)
from app.services.tools.providers.devops import execute_devops_tool
from app.services.tools.providers.feishu_api import (
    FEISHU_API_CAPABILITIES,
    feishu_write_confirmation_token,
)
from app.services.tools.providers.feishu_mcp import execute_feishu_mcp_tool, feishu_mcp_bound_tool_names
from app.services.tools.providers.local import execute_local_tool
from app.services.tools.providers.report import execute_report_tool
from app.services.tools.write_audit import write_target_metadata, write_target_summary
from app.services.tools.personal import personal_task_access_boundary
from app.services.user_identity_authorizations import user_identity_authorization_url_status


FEISHU_REALTIME_RESPONSIBILITY_BOUNDARY = {
    "tool_router": "business_capability",
    "mcp": "tool_scheduling",
    "cli": "action_execution",
    "api": "sync_engine_data_sync",
}

FEISHU_REALTIME_EXECUTION_CHAIN = ["agent_runtime", "tool_router", "tool", "mcp", "cli", "feishu"]

AGENT_RUNTIME_CHAIN = [
    "feishu_message_entrypoint",
    "message_gateway",
    "agent_runtime",
    "tool_router",
    "tool",
    "data_or_execution_source",
    "tool_structured_result",
    "agent_runtime_final_answer",
    "message_gateway_reply",
]


TOOL_REGISTRY: dict[str, ToolDefinition] = {
    "feishu_approval_task_approve": ToolDefinition(
        capabilities=["approval"],
        family="TaskTool",
        name="feishu_approval_task_approve",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("approval:write",),
        audit_action="tool.approval_task.approve",
        supports_write=True,
    ),
    "feishu_approval_task_reject": ToolDefinition(
        capabilities=["approval"],
        family="TaskTool",
        name="feishu_approval_task_reject",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("approval:write",),
        audit_action="tool.approval_task.reject",
        supports_write=True,
    ),
    "feishu_approval_task_rollback": ToolDefinition(
        capabilities=["approval"],
        family="TaskTool",
        name="feishu_approval_task_rollback",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("approval:write",),
        audit_action="tool.approval_task.rollback",
        supports_write=True,
    ),
    "feishu_approval_instance_cancel": ToolDefinition(
        capabilities=["approval"],
        family="TaskTool",
        name="feishu_approval_instance_cancel",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("approval:write",),
        audit_action="tool.approval_instance.cancel",
        supports_write=True,
    ),
    "feishu_approval_instance_cc": ToolDefinition(
        capabilities=["approval"],
        family="TaskTool",
        name="feishu_approval_instance_cc",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("approval:write",),
        audit_action="tool.approval_instance.cc",
        supports_write=True,
    ),
    "feishu_approval_instance_remind": ToolDefinition(
        capabilities=["approval"],
        family="TaskTool",
        name="feishu_approval_instance_remind",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("approval:write",),
        audit_action="tool.approval_instance.remind",
        supports_write=True,
    ),
    "feishu_approval_task_add_sign": ToolDefinition(
        capabilities=["approval"],
        family="TaskTool",
        name="feishu_approval_task_add_sign",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("approval:write",),
        audit_action="tool.approval_task.add_sign",
        supports_write=True,
    ),
    "feishu_approval_task_transfer": ToolDefinition(
        capabilities=["approval"],
        family="TaskTool",
        name="feishu_approval_task_transfer",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("approval:write",),
        audit_action="tool.approval_task.transfer",
        supports_write=True,
    ),
    "feishu_approval_instance_get": ToolDefinition(
        capabilities=["approval"],
        family="TaskTool",
        name="feishu_approval_instance_get",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("approval:read",),
        audit_action="tool.approval_instance.read",
    ),
    "feishu_approval_instance_initiated": ToolDefinition(
        capabilities=["approval"],
        family="TaskTool",
        name="feishu_approval_instance_initiated",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("approval:read",),
        audit_action="tool.approval_instance.initiated",
    ),
    "feishu_approval_task_query": ToolDefinition(
        capabilities=["approval"],
        family="TaskTool",
        name="feishu_approval_task_query",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("approval:read",),
        audit_action="tool.approval_task.query",
    ),
    "feishu_approval_attachment_download": ToolDefinition(
        capabilities=["approval"],
        family="TaskTool",
        name="feishu_approval_attachment_download",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("approval:read",),
        audit_action="tool.approval_attachment.download",
    ),
    "feishu_doc_fetch": ToolDefinition(
        family="KnowledgeTool",
        capabilities=["doc"],
        name="feishu_doc_fetch",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("knowledge:read",),
        audit_action="tool.feishu_doc.fetch",
    ),
    "feishu_drive_search": ToolDefinition(
        family="KnowledgeTool",
        capabilities=["doc"],
        name="feishu_drive_search",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("knowledge:read",),
        audit_action="tool.feishu_drive.search",
    ),
    "feishu_drive_file_list": ToolDefinition(
        family="KnowledgeTool",
        capabilities=["drive"],
        name="feishu_drive_file_list",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("knowledge:read",),
        audit_action="tool.feishu_drive.file_list",
    ),
    "feishu_okr_cycle_list": ToolDefinition(
        family="PeopleTool",
        capabilities=["okr"],
        name="feishu_okr_cycle_list",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("okr:read",),
        audit_action="tool.okr_cycle.list",
    ),
    "feishu_okr_objective_list": ToolDefinition(
        family="PeopleTool",
        capabilities=["okr"],
        name="feishu_okr_objective_list",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("okr:read",),
        audit_action="tool.okr_objective.list",
    ),
    "feishu_task_create": ToolDefinition(
        capabilities=["feishu_task"],
        family="TaskTool",
        name="feishu_task_create",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("task:write",),
        audit_action="tool.task.create",
        supports_write=True,
    ),
    "feishu_task_complete": ToolDefinition(
        capabilities=["feishu_task"],
        family="TaskTool",
        name="feishu_task_complete",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("task:write",),
        audit_action="tool.task.complete",
        supports_write=True,
    ),
    "feishu_task_subtask_create": ToolDefinition(
        capabilities=["feishu_task"],
        family="TaskTool",
        name="feishu_task_subtask_create",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("task:write",),
        audit_action="tool.task.subtask_create",
        supports_write=True,
    ),
    "feishu_task_reopen": ToolDefinition(
        capabilities=["feishu_task"],
        family="TaskTool",
        name="feishu_task_reopen",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("task:write",),
        audit_action="tool.task.reopen",
        supports_write=True,
    ),
    "feishu_task_update": ToolDefinition(
        capabilities=["feishu_task"],
        family="TaskTool",
        name="feishu_task_update",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("task:write",),
        audit_action="tool.task.update",
        supports_write=True,
    ),
    "feishu_task_delete": ToolDefinition(
        capabilities=["feishu_task"],
        family="TaskTool",
        name="feishu_task_delete",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("task:write",),
        audit_action="tool.task.delete",
        supports_write=True,
    ),
    "feishu_task_update_reminders": ToolDefinition(
        capabilities=["feishu_task"],
        family="TaskTool",
        name="feishu_task_update_reminders",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("task:write",),
        audit_action="tool.task.update_reminders",
        supports_write=True,
    ),
    "feishu_task_assign_members": ToolDefinition(
        capabilities=["feishu_task"],
        family="TaskTool",
        name="feishu_task_assign_members",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("task:write",),
        audit_action="tool.task.assign_members",
        supports_write=True,
    ),
    "feishu_task_update_followers": ToolDefinition(
        capabilities=["feishu_task"],
        family="TaskTool",
        name="feishu_task_update_followers",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("task:write",),
        audit_action="tool.task.update_followers",
        supports_write=True,
    ),
    "feishu_task_comment": ToolDefinition(
        capabilities=["feishu_task"],
        family="TaskTool",
        name="feishu_task_comment",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("task:write",),
        audit_action="tool.task.comment",
        supports_write=True,
    ),
    "feishu_task_upload_attachment": ToolDefinition(
        capabilities=["feishu_task"],
        family="TaskTool",
        name="feishu_task_upload_attachment",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("task:write",),
        audit_action="tool.task.upload_attachment",
        supports_write=True,
    ),
    "feishu_tasklist_create": ToolDefinition(
        capabilities=["feishu_task"],
        family="TaskTool",
        name="feishu_tasklist_create",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("task:write",),
        audit_action="tool.tasklist.create",
        supports_write=True,
    ),
    "feishu_tasklist_delete": ToolDefinition(
        capabilities=["feishu_task"],
        family="TaskTool",
        name="feishu_tasklist_delete",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("task:write",),
        audit_action="tool.tasklist.delete",
        supports_write=True,
    ),
    "feishu_tasklist_update": ToolDefinition(
        capabilities=["feishu_task"],
        family="TaskTool",
        name="feishu_tasklist_update",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("task:write",),
        audit_action="tool.tasklist.update",
        supports_write=True,
    ),
    "feishu_task_add_to_tasklist": ToolDefinition(
        capabilities=["feishu_task"],
        family="TaskTool",
        name="feishu_task_add_to_tasklist",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("task:write",),
        audit_action="tool.task.add_to_tasklist",
        supports_write=True,
    ),
    "feishu_task_set_ancestor": ToolDefinition(
        capabilities=["feishu_task"],
        family="TaskTool",
        name="feishu_task_set_ancestor",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("task:write",),
        audit_action="tool.task.set_ancestor",
        supports_write=True,
    ),
    "feishu_task_clear_ancestor": ToolDefinition(
        capabilities=["feishu_task"],
        family="TaskTool",
        name="feishu_task_clear_ancestor",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("task:write",),
        audit_action="tool.task.clear_ancestor",
        supports_write=True,
    ),
    "feishu_tasklist_update_members": ToolDefinition(
        capabilities=["feishu_task"],
        family="TaskTool",
        name="feishu_tasklist_update_members",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("task:write",),
        audit_action="tool.tasklist.update_members",
        supports_write=True,
    ),
    "feishu_tasklist_set_members": ToolDefinition(
        capabilities=["feishu_task"],
        family="TaskTool",
        name="feishu_tasklist_set_members",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("task:write",),
        audit_action="tool.tasklist.set_members",
        supports_write=True,
    ),
    "feishu_task_section_create": ToolDefinition(
        capabilities=["feishu_task"],
        family="TaskTool",
        name="feishu_task_section_create",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("task:write",),
        audit_action="tool.task_section.create",
        supports_write=True,
    ),
    "feishu_task_section_update": ToolDefinition(
        capabilities=["feishu_task"],
        family="TaskTool",
        name="feishu_task_section_update",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("task:write",),
        audit_action="tool.task_section.update",
        supports_write=True,
    ),
    "feishu_task_section_delete": ToolDefinition(
        capabilities=["feishu_task"],
        family="TaskTool",
        name="feishu_task_section_delete",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("task:write",),
        audit_action="tool.task_section.delete",
        supports_write=True,
    ),
    "bitable_qa": ToolDefinition(
        family="BitableTool",
        capabilities=["bitable"],
        name="bitable_qa",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:read",),
        audit_action="tool.bitable_qa.read",
    ),
    "feishu_bitable_base_create": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_base_create",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:write",),
        audit_action="tool.bitable.base.create",
        supports_write=True,
    ),
    "calendar_qa": ToolDefinition(
        family="CalendarTool",
        capabilities=["schedule"],
        name="calendar_qa",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("calendar:read",),
        audit_action="tool.calendar_qa.read",
    ),
    "feishu_bitable_record_create": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_record_create",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:write",),
        audit_action="tool.bitable.record.create",
        supports_write=True,
    ),
    "feishu_bitable_record_batch_create": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_record_batch_create",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:write",),
        audit_action="tool.bitable.record.batch_create",
        supports_write=True,
    ),
    "feishu_bitable_record_batch_delete": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_record_batch_delete",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:write",),
        audit_action="tool.bitable.record.batch_delete",
        supports_write=True,
    ),
    "feishu_bitable_record_batch_update": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_record_batch_update",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:write",),
        audit_action="tool.bitable.record.batch_update",
        supports_write=True,
    ),
    "feishu_bitable_record_update": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_record_update",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:write",),
        audit_action="tool.bitable.record.update",
        supports_write=True,
    ),
    "feishu_bitable_record_delete": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_record_delete",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:write",),
        audit_action="tool.bitable.record.delete",
        supports_write=True,
    ),
    "feishu_bitable_record_upsert": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_record_upsert",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:write",),
        audit_action="tool.bitable.record.upsert",
        supports_write=True,
    ),
    "feishu_bitable_record_upload_attachment": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_record_upload_attachment",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:write",),
        audit_action="tool.bitable.record.upload_attachment",
        supports_write=True,
    ),
    "feishu_bitable_record_remove_attachment": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_record_remove_attachment",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:write",),
        audit_action="tool.bitable.record.remove_attachment",
        supports_write=True,
    ),
    "feishu_bitable_field_list": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_field_list",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:read",),
        audit_action="tool.bitable.field.list",
    ),
    "feishu_bitable_table_create": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_table_create",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:write",),
        audit_action="tool.bitable.table.create",
        supports_write=True,
    ),
    "feishu_bitable_table_update": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_table_update",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:write",),
        audit_action="tool.bitable.table.update",
        supports_write=True,
    ),
    "feishu_bitable_table_delete": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_table_delete",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:write",),
        audit_action="tool.bitable.table.delete",
        supports_write=True,
    ),
    "feishu_bitable_field_create": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_field_create",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:write",),
        audit_action="tool.bitable.field.create",
        supports_write=True,
    ),
    "feishu_bitable_field_delete": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_field_delete",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:write",),
        audit_action="tool.bitable.field.delete",
        supports_write=True,
    ),
    "feishu_bitable_field_update": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_field_update",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:write",),
        audit_action="tool.bitable.field.update",
        supports_write=True,
    ),
    "feishu_bitable_view_create": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_view_create",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:write",),
        audit_action="tool.bitable.view.create",
        supports_write=True,
    ),
    "feishu_bitable_view_delete": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_view_delete",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:write",),
        audit_action="tool.bitable.view.delete",
        supports_write=True,
    ),
    "feishu_bitable_view_rename": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_view_rename",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:write",),
        audit_action="tool.bitable.view.rename",
        supports_write=True,
    ),
    "feishu_bitable_view_set_filter": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_view_set_filter",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:write",),
        audit_action="tool.bitable.view.set_filter",
        supports_write=True,
    ),
    "feishu_bitable_view_set_sort": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_view_set_sort",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:write",),
        audit_action="tool.bitable.view.set_sort",
        supports_write=True,
    ),
    "feishu_bitable_view_set_group": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_view_set_group",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:write",),
        audit_action="tool.bitable.view.set_group",
        supports_write=True,
    ),
    "feishu_bitable_view_get_visible_fields": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_view_get_visible_fields",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:read",),
        audit_action="tool.bitable.view.get_visible_fields",
    ),
    "feishu_bitable_view_get_card": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_view_get_card",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:read",),
        audit_action="tool.bitable.view.get_card",
    ),
    "feishu_bitable_view_get_timebar": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_view_get_timebar",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:read",),
        audit_action="tool.bitable.view.get_timebar",
    ),
    "feishu_bitable_view_set_visible_fields": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_view_set_visible_fields",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:write",),
        audit_action="tool.bitable.view.set_visible_fields",
        supports_write=True,
    ),
    "feishu_bitable_view_set_card": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_view_set_card",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:write",),
        audit_action="tool.bitable.view.set_card",
        supports_write=True,
    ),
    "feishu_bitable_view_set_timebar": ToolDefinition(
        capabilities=["bitable"],
        family="BitableTool",
        name="feishu_bitable_view_set_timebar",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("bitable:write",),
        audit_action="tool.bitable.view.set_timebar",
        supports_write=True,
    ),
    "feishu_calendar_create_event": ToolDefinition(
        family="CalendarTool",
        capabilities=["schedule"],
        name="feishu_calendar_create_event",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("calendar:write",),
        audit_action="tool.calendar.event.create",
        supports_write=True,
    ),
    "feishu_im_send_message": ToolDefinition(
        family="ChatTool",
        capabilities=["im"],
        name="feishu_im_send_message",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:write",),
        audit_action="tool.im.message.send",
        supports_write=True,
    ),
    "feishu_im_create_chat": ToolDefinition(
        family="ChatTool",
        capabilities=["im"],
        name="feishu_im_create_chat",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:write",),
        audit_action="tool.im.chat.create",
        supports_write=True,
    ),
    "feishu_im_auto_join_public_chats": ToolDefinition(
        family="ChatTool",
        capabilities=["im"],
        name="feishu_im_auto_join_public_chats",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:write",),
        audit_action="tool.im.public_chat.auto_join",
        supports_write=True,
    ),
    "feishu_contact_department_children": ToolDefinition(
        family="PeopleTool",
        capabilities=["contact"],
        name="feishu_contact_department_children",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("contact:read",),
        audit_action="tool.contact.department.children",
    ),
    "feishu_contact_department_users": ToolDefinition(
        family="PeopleTool",
        capabilities=["contact"],
        name="feishu_contact_department_users",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("contact:read",),
        audit_action="tool.contact.department.users",
    ),
    "feishu_contact_scope_list": ToolDefinition(
        family="PeopleTool",
        capabilities=["contact"],
        name="feishu_contact_scope_list",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("contact:read",),
        audit_action="tool.contact.scope.list",
    ),
        "feishu_contact_user_search": ToolDefinition(
        family="PeopleTool",
        capabilities=["contact"],
        name="feishu_contact_user_search",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("contact:read",),
        audit_action="tool.contact.user_search.read",
    ),
    "feishu_contact_user_get": ToolDefinition(
        family="PeopleTool",
        capabilities=["contact"],
        name="feishu_contact_user_get",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("contact:read",),
        audit_action="tool.contact.user_get.read",
    ),
    "feishu_contact_organization_snapshot": ToolDefinition(
        family="PeopleTool",
        capabilities=["contact"],
        name="feishu_contact_organization_snapshot",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("contact:read",),
        audit_action="tool.contact.organization.snapshot",
    ),
    "feishu_cli_status": ToolDefinition(
        family="AutomationTool",
        capabilities=["cli_diagnose"],
        name="feishu_cli_status",
        provider=ToolProvider.DEVOPS,
        required_permissions=("system:admin",),
        audit_action="tool.feishu_cli.status",
    ),
    "feishu_cli_doctor": ToolDefinition(
        family="AutomationTool",
        capabilities=["cli_diagnose"],
        name="feishu_cli_doctor",
        provider=ToolProvider.DEVOPS,
        required_permissions=("system:admin",),
        audit_action="tool.feishu_cli.doctor",
    ),
    "chat_qa": ToolDefinition(
        family="ChatTool",
        capabilities=["im"],
        name="chat_qa",
        provider=ToolProvider.LOCAL,
        required_permissions=("chat:read_current",),
        audit_action="tool.chat_qa.read",
    ),
    "chat_summary": ToolDefinition(
        family="ChatTool",
        capabilities=["im"],
        name="chat_summary",
        provider=ToolProvider.LOCAL,
        required_permissions=("chat:read_current",),
        audit_action="tool.chat_summary.read",
    ),
    "chat_tasks": ToolDefinition(
        family="ChatTool",
        capabilities=["im_task_summary"],
        name="chat_tasks",
        provider=ToolProvider.LOCAL,
        required_permissions=("chat:read_current", "task:read"),
        audit_action="tool.chat_tasks.read",
    ),
    "feishu_im_chat_search": ToolDefinition(
        family="ChatTool",
        capabilities=["im"],
        name="feishu_im_chat_search",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("chat:read_current",),
        audit_action="tool.feishu_im_chat_search.read",
    ),
    "feishu_im_message_list": ToolDefinition(
        family="ChatTool",
        capabilities=["im"],
        name="feishu_im_message_list",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("chat:read_current",),
        audit_action="tool.feishu_im_message_list.read",
    ),
    "company_qa": ToolDefinition(
        family="ReportTool",
        capabilities=["report"],
        name="company_qa",
        provider=ToolProvider.REPORT,
        required_permissions=("company:read",),
        audit_action="tool.company_qa.report",
    ),
    "domain_qa": ToolDefinition(
        family="KnowledgeTool",
        capabilities=["domain_qa"],
        name="domain_qa",
        provider=ToolProvider.LOCAL,
        required_permissions=("domain:read",),
        audit_action="tool.domain_qa.read",
    ),
    "general_chat": ToolDefinition(
        family="ChatTool",
        capabilities=["im"],
        name="general_chat",
        provider=ToolProvider.LOCAL,
        required_permissions=(),
        audit_action="tool.general_chat.answer",
    ),
    "mail_qa": ToolDefinition(
        family="ChatTool",
        capabilities=["mail_light"],
        name="mail_qa",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read_owner",),
        audit_action="tool.mail_qa.read",
    ),
    "feishu_mail_folder_list": ToolDefinition(
        family="ChatTool",
        capabilities=["mail_light"],
        name="feishu_mail_folder_list",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read_owner",),
        audit_action="tool.feishu_mail_folder_list.read",
    ),
    "feishu_mail_message_get": ToolDefinition(
        family="ChatTool",
        capabilities=["mail_light"],
        name="feishu_mail_message_get",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read_owner",),
        audit_action="tool.feishu_mail_message_get.read",
    ),
    
    "feishu_mail_folders_list": ToolDefinition(
        family="ChatTool",
        capabilities=["mail_light"],
        name="feishu_mail_folders_list",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read_owner",),
        audit_action="tool.mail.folders_list.read",
    ),
    "feishu_mail_folder_messages": ToolDefinition(
        family="ChatTool",
        capabilities=["mail_light"],
        name="feishu_mail_folder_messages",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read_owner",),
        audit_action="tool.mail.folder_messages.read",
    ),
    "feishu_mail_threads_list": ToolDefinition(
        family="ChatTool",
        capabilities=["mail_light"],
        name="feishu_mail_threads_list",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read_owner",),
        audit_action="tool.mail.threads_list.read",
    ),
    "feishu_mail_mailbox_info": ToolDefinition(
        family="ChatTool",
        capabilities=["mail_light"],
        name="feishu_mail_mailbox_info",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read_owner",),
        audit_action="tool.mail.mailbox_info.read",
    ),
    "feishu_mail_message_send": ToolDefinition(
        family="ChatTool",
        capabilities=["mail_light"],
        name="feishu_mail_message_send",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read_owner",),
        supports_write=True,
        audit_action="tool.mail.message.send",
    ),
    "feishu_mail_message_reply": ToolDefinition(
        family="ChatTool",
        capabilities=["mail_light"],
        name="feishu_mail_message_reply",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read_owner",),
        supports_write=True,
        audit_action="tool.mail.message.reply",
    ),
    "feishu_mail_message_forward": ToolDefinition(
        family="ChatTool",
        capabilities=["mail_light"],
        name="feishu_mail_message_forward",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read_owner",),
        supports_write=True,
        audit_action="tool.mail.message.forward",
    ),
    "feishu_mail_message_move": ToolDefinition(
        family="ChatTool",
        capabilities=["mail_light"],
        name="feishu_mail_message_move",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read_owner",),
        supports_write=True,
        audit_action="tool.mail.message.move",
    ),
    "feishu_mail_message_mark": ToolDefinition(
        family="ChatTool",
        capabilities=["mail_light"],
        name="feishu_mail_message_mark",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read_owner",),
        supports_write=True,
        audit_action="tool.mail.message.mark",
    ),
    "feishu_mail_message_delete": ToolDefinition(
        family="ChatTool",
        capabilities=["mail_light"],
        name="feishu_mail_message_delete",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read_owner",),
        supports_write=True,
        audit_action="tool.mail.message.delete",
    ),
    "feishu_mail_drafts_create": ToolDefinition(
        family="ChatTool",
        capabilities=["mail_light"],
        name="feishu_mail_drafts_create",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read_owner",),
        supports_write=True,
        audit_action="tool.mail.drafts.create",
    ),
    "feishu_mail_drafts_update": ToolDefinition(
        family="ChatTool",
        capabilities=["mail_light"],
        name="feishu_mail_drafts_update",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read_owner",),
        supports_write=True,
        audit_action="tool.mail.drafts.update",
    ),
    "feishu_mail_drafts_send": ToolDefinition(
        family="ChatTool",
        capabilities=["mail_light"],
        name="feishu_mail_drafts_send",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read_owner",),
        supports_write=True,
        audit_action="tool.mail.drafts.send",
    ),
    "feishu_mail_drafts_delete": ToolDefinition(
        family="ChatTool",
        capabilities=["mail_light"],
        name="feishu_mail_drafts_delete",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read_owner",),
        supports_write=True,
        audit_action="tool.mail.drafts.delete",
    ),
    "feishu_mail_folder_create": ToolDefinition(
        family="ChatTool",
        capabilities=["mail_light"],
        name="feishu_mail_folder_create",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read_owner",),
        supports_write=True,
        audit_action="tool.mail.folder.create",
    ),

    "feishu_vc_meeting_search": ToolDefinition(
        family="MeetingTool",
        capabilities=["meeting"],
        name="feishu_vc_meeting_search",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("calendar:read",),
        audit_action="tool.feishu_vc_meeting_search.read",
    ),
    "personal_tasks": ToolDefinition(
        family="TaskTool",
        capabilities=["feishu_task"],
        name="personal_tasks",
        provider=ToolProvider.LOCAL,
        required_permissions=("task:read_self",),
        audit_action="tool.personal_tasks.read",
    ),
    "owner_cockpit": ToolDefinition(
        family="ReportTool",
        capabilities=["report"],
        name="owner_cockpit",
        provider=ToolProvider.REPORT,
        required_permissions=("company:read", "report:read_owner"),
        audit_action="tool.owner_cockpit.report",
    ),
    "public_knowledge_qa": ToolDefinition(
        family="KnowledgeTool",
        capabilities=["doc"],
        name="public_knowledge_qa",
        provider=ToolProvider.LOCAL,
        required_permissions=("knowledge:read_public",),
        audit_action="tool.public_knowledge_qa.read",
    ),
    "feishu_wiki_space_list": ToolDefinition(
        family="KnowledgeTool",
        capabilities=["wiki"],
        name="feishu_wiki_space_list",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("knowledge:read_public",),
        audit_action="tool.feishu_wiki_space_list.read",
    ),
    "feishu_wiki_node_list": ToolDefinition(
        family="KnowledgeTool",
        capabilities=["wiki"],
        name="feishu_wiki_node_list",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("knowledge:read_public",),
        audit_action="tool.feishu_wiki_node_list.read",
    ),
    "task_qa": ToolDefinition(
        family="TaskTool",
        capabilities=["feishu_task"],
        name="task_qa",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("task:read",),
        audit_action="tool.task_qa.read",
    ),
    "feishu_im_chat_list": ToolDefinition(
        name="feishu_im_chat_list",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:read",),
        audit_action="tool.im.chat_list.read",
        family="ChatTool",
        capabilities=["im"],
        supports_write=False,
    ),
    "feishu_im_chat_update": ToolDefinition(
        name="feishu_im_chat_update",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:write",),
        audit_action="tool.im.chat_update.write",
        family="ChatTool",
        capabilities=["im"],
        supports_write=True,
    ),
    "feishu_im_message_reply": ToolDefinition(
        name="feishu_im_message_reply",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:write",),
        audit_action="tool.im.message_reply.write",
        family="ChatTool",
        capabilities=["im"],
        supports_write=True,
    ),
    "feishu_im_message_mget": ToolDefinition(
        name="feishu_im_message_mget",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:read",),
        audit_action="tool.im.message_mget.read",
        family="ChatTool",
        capabilities=["im"],
        supports_write=False,
    ),
    "feishu_im_message_search": ToolDefinition(
        name="feishu_im_message_search",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:read",),
        audit_action="tool.im.message_search.read",
        family="ChatTool",
        capabilities=["im"],
        supports_write=False,
    ),
    "feishu_im_message_resource_download": ToolDefinition(
        name="feishu_im_message_resource_download",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:read",),
        audit_action="tool.im.message_resource_download.read",
        family="ChatTool",
        capabilities=["im"],
        supports_write=False,
    ),
    "feishu_im_thread_messages_list": ToolDefinition(
        name="feishu_im_thread_messages_list",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:read",),
        audit_action="tool.im.thread_messages_list.read",
        family="ChatTool",
        capabilities=["im"],
        supports_write=False,
    ),
    "feishu_im_chat_members_list": ToolDefinition(
        name="feishu_im_chat_members_list",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:read",),
        audit_action="tool.im.chat_members_list.read",
        family="ChatTool",
        capabilities=["im"],
        supports_write=False,
    ),
    "feishu_im_pin_create": ToolDefinition(
        name="feishu_im_pin_create",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:write",),
        audit_action="tool.im.pin_create.write",
        family="ChatTool",
        capabilities=["im"],
        supports_write=True,
    ),
    "feishu_im_pin_list": ToolDefinition(
        name="feishu_im_pin_list",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:read",),
        audit_action="tool.im.pin_list.read",
        family="ChatTool",
        capabilities=["im"],
        supports_write=False,
    ),
    "feishu_im_pin_delete": ToolDefinition(
        name="feishu_im_pin_delete",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:write",),
        audit_action="tool.im.pin_delete.write",
        family="ChatTool",
        capabilities=["im"],
        supports_write=True,
    ),
    "feishu_im_reaction_create": ToolDefinition(
        name="feishu_im_reaction_create",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:write",),
        audit_action="tool.im.reaction_create.write",
        family="ChatTool",
        capabilities=["im"],
        supports_write=True,
    ),
    "feishu_im_reaction_list": ToolDefinition(
        name="feishu_im_reaction_list",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:read",),
        audit_action="tool.im.reaction_list.read",
        family="ChatTool",
        capabilities=["im"],
        supports_write=False,
    ),
    "feishu_im_reaction_delete": ToolDefinition(
        name="feishu_im_reaction_delete",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:write",),
        audit_action="tool.im.reaction_delete.write",
        family="ChatTool",
        capabilities=["im"],
        supports_write=True,
    ),
    "feishu_im_image_upload": ToolDefinition(
        name="feishu_im_image_upload",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:write",),
        audit_action="tool.im.image_upload.write",
        family="ChatTool",
        capabilities=["im"],
        supports_write=True,
    ),
    "feishu_im_flag_create": ToolDefinition(
        name="feishu_im_flag_create",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:write",),
        audit_action="tool.im.flag_create.write",
        family="ChatTool",
        capabilities=["im"],
        supports_write=True,
    ),
    "feishu_im_flag_list": ToolDefinition(
        name="feishu_im_flag_list",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:read",),
        audit_action="tool.im.flag_list.read",
        family="ChatTool",
        capabilities=["im"],
        supports_write=False,
    ),
    "feishu_im_flag_cancel": ToolDefinition(
        name="feishu_im_flag_cancel",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:write",),
        audit_action="tool.im.flag_cancel.write",
        family="ChatTool",
        capabilities=["im"],
        supports_write=True,
    ),
    "feishu_im_feed_group_list": ToolDefinition(
        name="feishu_im_feed_group_list",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:read",),
        audit_action="tool.im.feed_group_list.read",
        family="ChatTool",
        capabilities=["im"],
        supports_write=False,
    ),
    "feishu_im_feed_group_list_item": ToolDefinition(
        name="feishu_im_feed_group_list_item",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:read",),
        audit_action="tool.im.feed_group_list_item.read",
        family="ChatTool",
        capabilities=["im"],
        supports_write=False,
    ),
    "feishu_im_feed_group_query_item": ToolDefinition(
        name="feishu_im_feed_group_query_item",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:read",),
        audit_action="tool.im.feed_group_query_item.read",
        family="ChatTool",
        capabilities=["im"],
        supports_write=False,
    ),
    "feishu_im_feed_shortcut_create": ToolDefinition(
        name="feishu_im_feed_shortcut_create",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:write",),
        audit_action="tool.im.feed_shortcut_create.write",
        family="ChatTool",
        capabilities=["im"],
        supports_write=True,
    ),
    "feishu_im_feed_shortcut_list": ToolDefinition(
        name="feishu_im_feed_shortcut_list",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:read",),
        audit_action="tool.im.feed_shortcut_list.read",
        family="ChatTool",
        capabilities=["im"],
        supports_write=False,
    ),
    "feishu_im_feed_shortcut_remove": ToolDefinition(
        name="feishu_im_feed_shortcut_remove",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("im:write",),
        audit_action="tool.im.feed_shortcut_remove.write",
        family="ChatTool",
        capabilities=["im"],
        supports_write=True,
    ),
    "feishu_mail_decline_receipt": ToolDefinition(
        name="feishu_mail_decline_receipt",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:write",),
        audit_action="tool.mail.decline_receipt.write",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=True,
    ),
    "feishu_mail_send_receipt": ToolDefinition(
        name="feishu_mail_send_receipt",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:write",),
        audit_action="tool.mail.send_receipt.write",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=True,
    ),
    "feishu_mail_share_to_chat": ToolDefinition(
        name="feishu_mail_share_to_chat",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:write",),
        audit_action="tool.mail.share_to_chat.write",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=True,
    ),
    "feishu_mail_reply_all": ToolDefinition(
        name="feishu_mail_reply_all",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:write",),
        audit_action="tool.mail.reply_all.write",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=True,
    ),
    "feishu_mail_lint_html": ToolDefinition(
        name="feishu_mail_lint_html",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read",),
        audit_action="tool.mail.lint_html.read",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=False,
    ),
    "feishu_mail_signature_list": ToolDefinition(
        name="feishu_mail_signature_list",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read",),
        audit_action="tool.mail.signature_list.read",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=False,
    ),
    "feishu_mail_template_create": ToolDefinition(
        name="feishu_mail_template_create",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:write",),
        audit_action="tool.mail.template_create.write",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=True,
    ),
    "feishu_mail_template_update": ToolDefinition(
        name="feishu_mail_template_update",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:write",),
        audit_action="tool.mail.template_update.write",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=True,
    ),
    "feishu_mail_template_list": ToolDefinition(
        name="feishu_mail_template_list",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read",),
        audit_action="tool.mail.template_list.read",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=False,
    ),
    "feishu_mail_template_delete": ToolDefinition(
        name="feishu_mail_template_delete",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:write",),
        audit_action="tool.mail.template_delete.write",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=True,
    ),
    "feishu_mail_watch": ToolDefinition(
        name="feishu_mail_watch",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read",),
        audit_action="tool.mail.watch.read",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=False,
    ),
    "feishu_mail_messages_batch_get": ToolDefinition(
        name="feishu_mail_messages_batch_get",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read",),
        audit_action="tool.mail.messages_batch_get.read",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=False,
    ),
    "feishu_mail_label_list": ToolDefinition(
        name="feishu_mail_label_list",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read",),
        audit_action="tool.mail.label_list.read",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=False,
    ),
    "feishu_mail_label_create": ToolDefinition(
        name="feishu_mail_label_create",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:write",),
        audit_action="tool.mail.label_create.write",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=True,
    ),
    "feishu_mail_label_update": ToolDefinition(
        name="feishu_mail_label_update",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:write",),
        audit_action="tool.mail.label_update.write",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=True,
    ),
    "feishu_mail_label_delete": ToolDefinition(
        name="feishu_mail_label_delete",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:write",),
        audit_action="tool.mail.label_delete.write",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=True,
    ),
    "feishu_mail_contact_list": ToolDefinition(
        name="feishu_mail_contact_list",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read",),
        audit_action="tool.mail.contact_list.read",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=False,
    ),
    "feishu_mail_contact_create": ToolDefinition(
        name="feishu_mail_contact_create",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:write",),
        audit_action="tool.mail.contact_create.write",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=True,
    ),
    "feishu_mail_contact_delete": ToolDefinition(
        name="feishu_mail_contact_delete",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:write",),
        audit_action="tool.mail.contact_delete.write",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=True,
    ),
    "feishu_mail_attachment_download": ToolDefinition(
        name="feishu_mail_attachment_download",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read",),
        audit_action="tool.mail.attachment_download.read",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=False,
    ),
    "feishu_mail_rule_list": ToolDefinition(
        name="feishu_mail_rule_list",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read",),
        audit_action="tool.mail.rule_list.read",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=False,
    ),
    "feishu_mail_rule_create": ToolDefinition(
        name="feishu_mail_rule_create",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:write",),
        audit_action="tool.mail.rule_create.write",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=True,
    ),
    "feishu_mail_rule_update": ToolDefinition(
        name="feishu_mail_rule_update",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:write",),
        audit_action="tool.mail.rule_update.write",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=True,
    ),
    "feishu_mail_rule_delete": ToolDefinition(
        name="feishu_mail_rule_delete",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:write",),
        audit_action="tool.mail.rule_delete.write",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=True,
    ),
    "feishu_mail_sent_message_list": ToolDefinition(
        name="feishu_mail_sent_message_list",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read",),
        audit_action="tool.mail.sent_message_list.read",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=False,
    ),
    "feishu_mail_settings_get": ToolDefinition(
        name="feishu_mail_settings_get",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:read",),
        audit_action="tool.mail.settings_get.read",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=False,
    ),
    "feishu_mail_settings_update": ToolDefinition(
        name="feishu_mail_settings_update",
        provider=ToolProvider.FEISHU_MCP,
        required_permissions=("mail:write",),
        audit_action="tool.mail.settings_update.write",
        family="ChatTool",
        capabilities=["mail_light"],
        supports_write=True,
    ),
}


def execute_agent_tool(context: ToolContext, request: ToolRequest) -> ToolResult:
    definition = TOOL_REGISTRY.get(request.tool_name)
    if definition is None:
        raise ValueError(f"Unknown agent tool: {request.tool_name}")
    definition = _effective_definition(context, definition)
    definition = _definition_for_request(definition, request)
    if not definition.enabled:
        raise ValueError(f"Disabled agent tool: {request.tool_name}")
    started = perf_counter()
    metadata = _tool_metadata(definition, request, context=context)
    allowed, reason = _actor_can_invoke_tool(context.actor, definition, chat_id=context.chat_id)
    if not allowed:
        metadata["denied_reason"] = reason
        result = ToolResult(
            tool_name=request.tool_name,
            provider=definition.provider,
            answer="这个工具能力不在你的授权范围内。",
            status=ToolExecutionStatus.DENIED,
            error=reason,
            structured_result=_structured_tool_result(
                definition,
                request,
                status=ToolExecutionStatus.DENIED,
                response_text="这个工具能力不在你的授权范围内。",
                error=reason,
                context=context,
            ),
            data_source=_tool_data_source(definition),
            execution_source=_tool_execution_source(definition),
            metadata=metadata,
        )
        _record_tool_execution(context, definition, request, result, duration_ms=_duration_ms(started))
        return result
    data_allowed, data_reason = _actor_data_permission_for_tool(context, definition)
    if not data_allowed:
        metadata["data_access_denied_reason"] = data_reason
        metadata["tool_invocation_allowed"] = True
        metadata.update(_data_permission_denial_metadata(data_reason, context=context))
        answer = _data_permission_denied_answer(data_reason)
        result = ToolResult(
            tool_name=request.tool_name,
            provider=definition.provider,
            answer=answer,
            status=ToolExecutionStatus.DENIED,
            error=data_reason,
            structured_result=_structured_tool_result(
                definition,
                request,
                status=ToolExecutionStatus.DENIED,
                response_text=answer,
                error=data_reason,
                context=context,
            ),
            data_source=_tool_data_source(definition),
            execution_source=_tool_execution_source(definition),
            metadata=metadata,
        )
        _record_tool_execution(context, definition, request, result, duration_ms=_duration_ms(started))
        return result
    try:
        if definition.supports_write:
            guard_answer = _write_guard_answer(definition, context, request)
            if guard_answer is not None:
                result = ToolResult(
                    tool_name=request.tool_name,
                    provider=definition.provider,
                    answer=guard_answer,
                    structured_result=_structured_tool_result(
                        definition,
                        request,
                        status=ToolExecutionStatus.SUCCESS,
                        response_text=guard_answer,
                        context=context,
                    ),
                    data_source=_tool_data_source(definition),
                    execution_source=_tool_execution_source(definition),
                    metadata=metadata,
                )
                _record_tool_execution(context, definition, request, result, duration_ms=_duration_ms(started))
                return result
        request = _request_with_provider_runtime_params(context, definition, request)
        answer = _execute_provider_tool(definition.provider, context, request)
    except Exception as exc:
        if isinstance(exc, PermissionError) and definition.supports_write and str(exc).startswith("Feishu write tool "):
            metadata["policy_reason"] = "write_confirmation_required"
            metadata["write_policy_denied"] = True
            result = ToolResult(
                tool_name=request.tool_name,
                provider=definition.provider,
                answer="写操作必须先执行 dry-run，并在确认写目标后带回 confirmation_token 执行。",
                status=ToolExecutionStatus.DENIED,
                error=str(exc),
                structured_result=_structured_tool_result(
                    definition,
                    request,
                    status=ToolExecutionStatus.DENIED,
                    response_text="写操作必须先执行 dry-run，并在确认写目标后带回 confirmation_token 执行。",
                    error=str(exc),
                    context=context,
                ),
                data_source=_tool_data_source(definition),
                execution_source=_tool_execution_source(definition),
                metadata=metadata,
            )
            _record_tool_execution(context, definition, request, result, duration_ms=_duration_ms(started))
            return result
        result = ToolResult(
            tool_name=request.tool_name,
            provider=definition.provider,
            answer=f"这个工具执行失败：{exc}",
            status=ToolExecutionStatus.ERROR,
            error=str(exc),
            structured_result=_structured_tool_result(
                definition,
                request,
                status=ToolExecutionStatus.ERROR,
                response_text=f"这个工具执行失败：{exc}",
                error=str(exc),
                context=context,
            ),
            data_source=_tool_data_source(definition),
            execution_source=_tool_execution_source(definition),
            metadata=metadata,
        )
        _record_tool_execution(context, definition, request, result, duration_ms=_duration_ms(started))
        return result
    result = ToolResult(
        tool_name=request.tool_name,
        provider=definition.provider,
        answer=answer,
        structured_result=_structured_tool_result(
            definition,
            request,
            status=ToolExecutionStatus.SUCCESS,
            response_text=answer,
            context=context,
        ),
        data_source=_tool_data_source(definition),
        execution_source=_tool_execution_source(definition),
        metadata=metadata,
    )
    _record_tool_execution(context, definition, request, result, duration_ms=_duration_ms(started))
    return result


def preflight_agent_tool_data_permission(context: ToolContext, request: ToolRequest) -> ToolResult | None:
    definition = TOOL_REGISTRY.get(request.tool_name)
    if definition is None:
        raise ValueError(f"Unknown agent tool: {request.tool_name}")
    definition = _effective_definition(context, definition)
    if not definition.enabled:
        raise ValueError(f"Disabled agent tool: {request.tool_name}")
    metadata = _tool_metadata(definition, request, context=context)
    allowed, reason = _actor_can_invoke_tool(context.actor, definition, chat_id=context.chat_id)
    if not allowed:
        metadata["denied_reason"] = reason
        return ToolResult(
            tool_name=request.tool_name,
            provider=definition.provider,
            answer="这个工具能力不在你的授权范围内。",
            status=ToolExecutionStatus.DENIED,
            error=reason,
            structured_result=_structured_tool_result(
                definition,
                request,
                status=ToolExecutionStatus.DENIED,
                response_text="这个工具能力不在你的授权范围内。",
                error=reason,
                context=context,
            ),
            data_source=_tool_data_source(definition),
            execution_source=_tool_execution_source(definition),
            metadata=metadata,
        )
    data_allowed, data_reason = _actor_data_permission_for_tool(context, definition)
    if data_allowed:
        return None
    metadata["data_access_denied_reason"] = data_reason
    metadata["tool_invocation_allowed"] = True
    metadata.update(_data_permission_denial_metadata(data_reason, context=context))
    answer = _data_permission_denied_answer(data_reason)
    return ToolResult(
        tool_name=request.tool_name,
        provider=definition.provider,
        answer=answer,
        status=ToolExecutionStatus.DENIED,
        error=data_reason,
        structured_result=_structured_tool_result(
            definition,
            request,
            status=ToolExecutionStatus.DENIED,
            response_text=answer,
            error=data_reason,
            context=context,
        ),
        data_source=_tool_data_source(definition),
        execution_source=_tool_execution_source(definition),
        metadata=metadata,
    )


def _execute_provider_tool(provider: ToolProvider, context: ToolContext, request: ToolRequest) -> str:
    if provider == ToolProvider.LOCAL:
        return execute_local_tool(context, request)
    if provider == ToolProvider.REPORT:
        return execute_report_tool(context, request)
    if provider == ToolProvider.FEISHU_API:
        raise PermissionError(
            "Feishu API provider is reserved for Sync Engine data synchronization, "
            "resource discovery, and admin sync preview; realtime business tools must use MCP -> CLI."
        )
    if provider == ToolProvider.FEISHU_MCP:
        return execute_feishu_mcp_tool(context, request)
    if provider == ToolProvider.DEVOPS:
        return execute_devops_tool(context, request)
    raise ValueError(f"Unsupported tool provider: {provider}")


def _tool_metadata(definition: ToolDefinition, request: ToolRequest, *, context: ToolContext) -> dict[str, object]:
    source_metadata = _tool_source_metadata(definition)
    metadata: dict[str, object] = {
        "route": request.tool_name,
        "provider": definition.provider.value,
        "provider_preference": request.provider_preference.value,
        "required_permissions": list(definition.required_permissions),
        "supports_write": definition.supports_write,
        "audit_action": definition.audit_action,
        "tool_decides_data_or_execution_source": True,
        "tool_returns_structured_result": True,
        "final_answer_owner": "agent_runtime",
        "agent_runtime_chain": list(AGENT_RUNTIME_CHAIN),
        **_tool_identity_boundary_metadata(definition),
        **_contextual_identity_scope_metadata(definition, context),
        **_contextual_user_identity_metadata(definition, context),
        **source_metadata,
        **_provider_execution_boundary_metadata(definition),
        **_tool_access_boundary_metadata(definition, context),
        **_tool_cli_profile_metadata(definition, context),
    }
    if definition.supports_write:
        metadata.update(_write_request_metadata(request))
    return metadata


def _structured_tool_result(
    definition: ToolDefinition,
    request: ToolRequest,
    *,
    status: ToolExecutionStatus,
    response_text: str,
    error: str | None = None,
    context: ToolContext | None = None,
) -> dict[str, object]:
    source_metadata = _tool_source_metadata(definition)
    access_metadata = _tool_access_boundary_metadata(definition, context) if context else {}
    response_payload = _parse_structured_response_payload(response_text)
    return {
        "tool_name": definition.name,
        "business_tool": getattr(definition, 'family', None),
        "provider": definition.provider.value,
        "status": status.value,
        "response_text": response_text,
        "response_payload": response_payload,
        "error": error,
        "data_access_denied_reason": error if status == ToolExecutionStatus.DENIED and error else None,
        "question": request.question,
        "normalized_command": request.normalized_command,
        "data_source": source_metadata["data_source"],
        "execution_source": source_metadata["execution_source"],
        "source_chain": source_metadata["source_chain"],
        **_tool_identity_boundary_metadata(definition),
        **(_contextual_identity_scope_metadata(definition, context) if context else {}),
        **_contextual_user_identity_metadata(definition, context),
        **_data_permission_denial_metadata(error, context=context),
        **access_metadata,
        "final_answer_allowed": False,
        "final_answer_owner": "agent_runtime",
    }


def _parse_structured_response_payload(response_text: str) -> dict[str, Any] | None:
    text = str(response_text or "").strip()
    if not text or (not text.startswith("{") and not text.startswith("[")):
        return None
    try:
        payload = json.loads(text)
    except Exception:
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _tool_access_boundary_metadata(definition: ToolDefinition, context: ToolContext | None) -> dict[str, object]:
    if definition.name != "personal_tasks" or context is None:
        return {}
    boundary = personal_task_access_boundary(context.actor)
    return {
        "data_access_scope": boundary["data_access_scope"],
        "personal_owner_open_id": boundary["personal_owner_open_id"],
        "identity_keys": boundary["identity_keys"],
        "has_strong_identity": boundary["has_strong_identity"],
        "strong_identity_keys": boundary["strong_identity_keys"],
        "cross_user_data_allowed": boundary["cross_user_data_allowed"],
        "boundary_reason": boundary["boundary_reason"],
    }


def _tool_cli_profile_metadata(definition: ToolDefinition, context: ToolContext | None) -> dict[str, object]:
    if definition.provider != ToolProvider.FEISHU_MCP or context is None or not context.cli_profile:
        return {}
    return {
        "cli_profile": context.cli_profile,
        "cli_profile_source": "feishu_app_config",
    }


def _contextual_user_identity_metadata(definition: ToolDefinition, context: ToolContext | None) -> dict[str, object]:
    if context is None:
        return {}
    resources: list[str] = []
    for permission in definition.required_permissions:
        for resource_type in _user_identity_resources_for_permission(
            permission,
            context=context,
            definition=definition,
        ):
            if resource_type not in resources:
                resources.append(resource_type)
    if not resources:
        return {}
    return {
        "user_identity_required": True,
        "required_user_identity_resources": resources,
        "user_identity_constraints": list(USER_IDENTITY_CONSTRAINTS),
        "user_resource_boundary": {
            "identity": "resource_owner_identity",
            "supported_resources": list(USER_IDENTITY_RESOURCES),
            "constraints": list(USER_IDENTITY_CONSTRAINTS),
            "required": True,
            "can_exceed_original_authorization": False,
        },
        "identity_permission_contract": identity_permission_contract(user_identity_required=True),
    }


def _tool_identity_boundary_metadata(definition: ToolDefinition) -> dict[str, object]:
    permissions = list(definition.required_permissions)
    user_identity_required = _requires_user_identity(permissions, definition.name)
    app_identity_required = _requires_app_identity(definition)
    return {
        "business_tool": getattr(definition, 'family', None),
        "tool_is_global_shared_business_capability": _is_shared_business_tool(definition),
        "tool_access_policy": TOOL_ACCESS_POLICY if _is_shared_business_tool(definition) else "restricted_system_tool",
        "tool_invocation_policy": "global_shared_tool" if _is_shared_business_tool(definition) else "system_admin_tool",
        "tool_invocation_allowed_for_agents": _is_shared_business_tool(definition),
        "tool_sharing_model": TOOL_SHARING_MODEL
        if _is_shared_business_tool(definition)
        else "restricted_admin_capability",
        "shared_business_tools": ["ApprovalTool", "KnowledgeTool", "BitableTool", "ChatTool", "CalendarTool", "MeetingTool", "ReportTool", "AutomationTool", "PeopleTool"] if _is_shared_business_tool(definition) else [],
        "shared_business_tool_count": SHARED_TOOL_COUNT if _is_shared_business_tool(definition) else 0,
        "data_permission_model": DATA_PERMISSION_MODEL,
        "data_boundary_policy": DATA_BOUNDARY_POLICY,
        "identity_permission_contract": identity_permission_contract(user_identity_required=user_identity_required),
        "app_identity_required": app_identity_required,
        "app_identity_constraints": list(ENTERPRISE_IDENTITY_CONSTRAINTS) if app_identity_required else [],
        "enterprise_resource_boundary": {
            "identity": "app_identity",
            "resource_owner": ENTERPRISE_IDENTITY_RESOURCE_OWNER,
            "constraints": list(ENTERPRISE_IDENTITY_CONSTRAINTS),
            "can_exceed_feishu_app_permissions": False,
        }
        if app_identity_required
        else None,
        "user_identity_required": user_identity_required,
        "user_identity_constraints": list(USER_IDENTITY_CONSTRAINTS) if user_identity_required else [],
        "user_identity_supported_resources": list(USER_IDENTITY_RESOURCES),
        "user_resource_boundary": {
            "identity": "resource_owner_identity",
            "supported_resources": list(USER_IDENTITY_RESOURCES),
            "constraints": list(USER_IDENTITY_CONSTRAINTS),
            "can_exceed_original_authorization": False,
        }
        if user_identity_required
        else None,
        "digital_advisor_permission_policy": DIGITAL_ADVISOR_PERMISSION_POLICY,
        "cannot_escalate_original_permissions": True,
    }


def _contextual_identity_scope_metadata(definition: ToolDefinition, context: ToolContext) -> dict[str, object]:
    app_identity_required = _requires_app_identity(definition)
    user_identity_required = _requires_user_identity(list(definition.required_permissions), definition.name)
    if _uses_personal_feishu_identity(context=context, definition=definition):
        user_identity_required = True
    metadata: dict[str, object] = {
        "company_scope": str(context.company_id),
        "role_scope": {
            "role": context.actor.role,
            "access_scope": context.actor.access_scope,
            "domains": list(context.actor.domains),
            "company_data_allowed": context.actor.can_query_company,
        },
        "data_boundary_enforcement": {
            "tool_is_global_shared": _is_shared_business_tool(definition),
            "enterprise_resources": list(ENTERPRISE_IDENTITY_CONSTRAINTS),
            "user_resources": list(USER_IDENTITY_CONSTRAINTS),
            "digital_advisor_can_only_tighten": True,
            "can_escalate_original_permissions": False,
        },
    }
    if app_identity_required:
        metadata.update(
            {
                "enterprise_identity": "app_identity",
                "enterprise_identity_boundary": "App Identity + Company Scope + Role Scope",
                "enterprise_company_scope": str(context.company_id),
                "enterprise_role_scope": metadata["role_scope"],
                "enterprise_resource_boundary": {
                    "identity": "app_identity",
                    "resource_owner": ENTERPRISE_IDENTITY_RESOURCE_OWNER,
                    "constraints": list(ENTERPRISE_IDENTITY_CONSTRAINTS),
                    "company_scope": str(context.company_id),
                    "role_scope": metadata["role_scope"],
                    "can_exceed_feishu_app_permissions": False,
                },
                "can_exceed_feishu_app_permissions": False,
            }
        )
    if user_identity_required:
        metadata.update(
            {
                "user_identity": "resource_owner_identity",
                "user_identity_owner_open_id": context.actor.open_id,
                "user_identity_boundary": "User Identity + Resource Owner Authorization",
                "can_exceed_user_original_authorization": False,
            }
        )
    return metadata


def _tool_source_metadata(definition: ToolDefinition) -> dict[str, object]:
    data_source = _tool_data_source(definition)
    execution_source = _tool_execution_source(definition)
    return {
        "data_source": data_source,
        "execution_source": execution_source,
        "source_chain": _tool_source_chain(definition),
        "source_kind": _tool_source_kind(data_source=data_source, execution_source=execution_source),
    }


def _tool_data_source(definition: ToolDefinition) -> str | None:
    if definition.name == "general_chat":
        return None
    if definition.provider == ToolProvider.FEISHU_API:
        return "API -> Feishu"
    if definition.provider == ToolProvider.LOCAL:
        if definition.name == "public_knowledge_qa" or getattr(definition, 'family', None) == "KnowledgeTool":
            return "PostgreSQL + Vector DB"
        return "PostgreSQL"
    if definition.provider == ToolProvider.REPORT:
        return "PostgreSQL"
    return None


def _tool_execution_source(definition: ToolDefinition) -> str | None:
    if definition.name == "general_chat":
        return "Pure Reasoning"
    if definition.provider == ToolProvider.FEISHU_MCP:
        return "MCP -> CLI -> Feishu"
    if definition.provider == ToolProvider.DEVOPS:
        return "Local Runtime"
    return None


def _tool_source_chain(definition: ToolDefinition) -> list[str]:
    if definition.name == "general_chat":
        return ["Pure Reasoning"]
    if definition.provider == ToolProvider.FEISHU_MCP:
        return ["MCP", "CLI", "Feishu"]
    if definition.provider == ToolProvider.FEISHU_API:
        return ["API", "Feishu"]
    if definition.provider == ToolProvider.LOCAL:
        if definition.name == "public_knowledge_qa" or getattr(definition, 'family', None) == "KnowledgeTool":
            return ["PostgreSQL", "Vector DB"]
        return ["PostgreSQL"]
    if definition.provider == ToolProvider.REPORT:
        return ["PostgreSQL"]
    if definition.provider == ToolProvider.DEVOPS:
        return ["Local Runtime"]
    return []


def _tool_source_kind(*, data_source: str | None, execution_source: str | None) -> str:
    if execution_source:
        return "execution_source"
    if data_source:
        return "data_source"
    return "no_enterprise_data"


def _business_tool_name(definition: ToolDefinition) -> str | None:
    if None:
        return None.value
    from app.services.tools.config import business_tool_for_tool_name

    business_tool = business_tool_for_tool_name(definition.name)
    return business_tool.value if business_tool else None


def _is_shared_business_tool(definition: ToolDefinition) -> bool:
    return getattr(definition, 'family', None) is not None


def _requires_app_identity(definition: ToolDefinition) -> bool:
    if definition.provider in {ToolProvider.FEISHU_MCP, ToolProvider.FEISHU_API}:
        return True
    return any(permission.startswith(("company:", "domain:", "approval:", "bitable:", "calendar:", "im:", "contact:")) for permission in definition.required_permissions)


def _requires_user_identity(permissions: list[str], tool_name: str) -> bool:
    if _tool_requires_user_identity_bundle(tool_name) or tool_name.startswith("feishu_mail_"):
        return True
    return any(permission.endswith("_self") or permission.endswith("_owner") for permission in permissions)


def _write_request_metadata(request: ToolRequest) -> dict[str, object]:
    dry_run = request.params.get("dry_run") is True
    confirmed = request.params.get("confirmed") is True
    has_confirmation_token = bool(str(request.params.get("confirmation_token") or "").strip())
    if dry_run:
        write_mode = "dry_run"
    elif confirmed:
        write_mode = "confirmed"
    else:
        write_mode = "pending_confirmation"
    return {
        "write_mode": write_mode,
        "dry_run": dry_run,
        "confirmed": confirmed,
        "has_confirmation_token": has_confirmation_token,
        "write_target": write_target_metadata(request.tool_name, request.params),
    }


def _write_guard_answer(definition: ToolDefinition, context: ToolContext, request: ToolRequest) -> str | None:
    if request.params.get("dry_run") is True:
        return _dry_run_write_answer(definition, context, request)
    if request.params.get("confirmed") is not True:
        raise PermissionError(f"Feishu write tool requires dry_run or confirmed=true with confirmation_token: {request.tool_name}")
    expected = feishu_write_confirmation_token(context, request)
    actual = str(request.params.get("confirmation_token") or "").strip()
    if actual != expected:
        raise PermissionError(f"Feishu write tool confirmation_token mismatch: {request.tool_name}")
    return None


def _dry_run_write_answer(definition: ToolDefinition, context: ToolContext, request: ToolRequest) -> str:
    capability = FEISHU_API_CAPABILITIES.get(definition.name)
    command = " ".join(capability.cli_command) if capability else definition.name
    token = feishu_write_confirmation_token(context, request)
    target = write_target_summary(write_target_metadata(request.tool_name, request.params))
    target_line = f"写目标: {target}\n" if target else ""
    return (
        f"Dry-run only. 已验证飞书写操作能力：{definition.name}。\n"
        "Tool 已选择执行源: MCP -> CLI -> Feishu\n"
        "完整链路: Agent Runtime -> Tool Router -> Tool -> 执行源 -> Agent Runtime\n"
        f"CLI: {command} --dry-run\n"
        f"{target_line}"
        f"confirmation_token: {token}\n"
        "真实执行必须带回同一组参数生成的 confirmation_token，并由 Tool 通过 MCP 调度 CLI 执行。"
    )


def _provider_execution_boundary_metadata(definition: ToolDefinition) -> dict[str, object]:
    if definition.provider in {ToolProvider.LOCAL, ToolProvider.REPORT, ToolProvider.DEVOPS}:
        return {
            "execution_chain": [definition.provider.value],
            "agent_runtime_direct_access": False,
            "sync_engine_mcp_access_allowed": False,
        }
    capability = FEISHU_API_CAPABILITIES.get(definition.name)
    if capability is None:
        if definition.name in feishu_mcp_bound_tool_names():
            return {
                "preferred_execution_engine": "lark_cli",
                "realtime_policy": "tool_mcp_cli_read",
                "realtime_bridge": "feishu_mcp",
                "mcp_provider": "feishu_mcp",
                "api_role": "none_mcp_only",
                "responsibility_boundary": dict(FEISHU_REALTIME_RESPONSIBILITY_BOUNDARY),
                "execution_chain": list(FEISHU_REALTIME_EXECUTION_CHAIN),
                "agent_runtime_direct_access": False,
                "sync_engine_direct_api_allowed": False,
                "sync_engine_mcp_access_allowed": False,
            }
        return {
            "execution_chain": [definition.provider.value],
            "agent_runtime_direct_access": False,
            "sync_engine_mcp_access_allowed": False,
        }
    return {
        "preferred_execution_engine": capability.preferred_execution_engine,
        "realtime_policy": "tool_mcp_cli_only",
        "realtime_bridge": "feishu_mcp",
        "mcp_provider": "feishu_mcp",
        "api_role": "sync_engine_only",
        "responsibility_boundary": dict(FEISHU_REALTIME_RESPONSIBILITY_BOUNDARY),
        "execution_chain": list(FEISHU_REALTIME_EXECUTION_CHAIN),
        "agent_runtime_direct_access": False,
        "sync_engine_direct_api_allowed": not capability.supports_write,
        "sync_engine_mcp_access_allowed": False,
    }


def _actor_can_invoke_tool(actor: BotActor, definition: ToolDefinition, *, chat_id: str | None) -> tuple[bool, str | None]:
    if _is_shared_business_tool(definition):
        return True, None
    if definition.provider != ToolProvider.DEVOPS:
        return True, None
    if _actor_has_permission(actor, "system:admin", chat_id=chat_id):
        return True, None
    return False, "missing_permission:system:admin"


def _actor_data_permission_for_tool(context: ToolContext, definition: ToolDefinition) -> tuple[bool, str | None]:
    for permission in definition.required_permissions:
        user_identity_resources = _user_identity_resources_for_permission(
            permission,
            context=context,
            definition=definition,
        )
        if user_identity_resources:
            if _actor_has_user_identity_authorization(context, user_identity_resources):
                continue
            return False, f"user_identity_authorization_required:{'_or_'.join(user_identity_resources)}"
        if not _actor_has_permission(context.actor, permission, chat_id=context.chat_id):
            return False, f"data_permission_denied:{permission}"
    return True, None


def _user_identity_resources_for_permission(
    permission: str,
    *,
    context: ToolContext | None = None,
    definition: ToolDefinition | None = None,
) -> tuple[str, ...]:
    if definition is not None and _tool_requires_user_identity_bundle(definition.name):
        return (USER_IDENTITY_BUNDLE_RESOURCE,)
    if permission == "mail:read_owner":
        return (USER_IDENTITY_BUNDLE_RESOURCE,)
    if permission == "task:read_self":
        return (USER_IDENTITY_BUNDLE_RESOURCE,)
    if permission in {"calendar:read", "calendar:write"} and _uses_personal_feishu_identity(
        context=context,
        definition=definition,
    ):
        return (USER_IDENTITY_BUNDLE_RESOURCE,)
    return ()


def _tool_requires_user_identity_bundle(tool_name: str) -> bool:
    """
    Only mark tools that need user OAuth.
    - Mail: Bot cannot access directly
    - approval tasks query: Feishu API requires user_access_token for tasks/query
    """
    if tool_name.startswith("feishu_mail_") or tool_name in {"mail_qa"}:
        return True
    if tool_name == "feishu_approval_task_query":
        return True
    return False


def _uses_personal_feishu_identity(
    *,
    context: ToolContext | None,
    definition: ToolDefinition | None,
) -> bool:
    if context is None or definition is None:
        return False
    if context.actor.can_query_company:
        return False
    if context.actor.access_scope not in {"personal", "self", "user"}:
        return False
    return definition.name in {"calendar_qa", "feishu_vc_meeting_search", "feishu_calendar_create_event"}


def _actor_has_user_identity_authorization(context: ToolContext, resource_types: set[str]) -> bool:
    if context.db is None or not context.actor.open_id:
        return False
    access = context.db.scalar(
        select(BotUserAccess)
        .where(BotUserAccess.company_id == context.company_id)
        .where(BotUserAccess.open_id == context.actor.open_id)
        .where(BotUserAccess.is_active.is_(True))
    )
    if access is None:
        return False
    settings_data = access.settings if isinstance(access.settings, dict) else {}
    raw_authorizations = settings_data.get("user_identity_authorizations")
    authorizations = raw_authorizations if isinstance(raw_authorizations, dict) else {}
    bundle = authorizations.get(USER_IDENTITY_BUNDLE_RESOURCE)
    if isinstance(bundle, dict):
        owner_open_id = str(bundle.get("owner_open_id") or context.actor.open_id or "").strip()
        status = str(bundle.get("status") or "").strip()
        if owner_open_id == context.actor.open_id and status in {"authorized", "connected"}:
            return True
    legacy_resource_types = set(USER_IDENTITY_RESOURCES)
    for resource_type in resource_types:
        checked_resource_types = legacy_resource_types if resource_type == USER_IDENTITY_BUNDLE_RESOURCE else {resource_type}
        for checked_resource_type in checked_resource_types:
            item = authorizations.get(checked_resource_type)
            if not isinstance(item, dict):
                continue
            owner_open_id = str(item.get("owner_open_id") or context.actor.open_id or "").strip()
            status = str(item.get("status") or "").strip()
            if owner_open_id == context.actor.open_id and status in {"authorized", "connected"}:
                return True
    return False


def _data_permission_denial_metadata(reason: str | None, *, context: ToolContext | None = None) -> dict[str, object]:
    if reason and reason.startswith("user_identity_authorization_required:"):
        return {
            "user_identity_authorization_required": True,
            "required_user_identity_resources": reason.split(":", 1)[1].split("_or_"),
            "authorization_owner": "resource_owner",
            "authorization_policy": "owner_granted_tighten_only",
            "can_escalate_original_permissions": False,
            "first_use_guidance": _user_identity_first_use_guidance(reason),
            "authorization_actions": _user_identity_authorization_actions(
                context,
                resource_types=tuple(reason.split(":", 1)[1].split("_or_")),
            ),
        }
    return {}


def _user_identity_first_use_guidance(reason: str | None) -> str:
    return "首次使用用户级能力时，需要由资源所有者本人完成一次整体授权；授权后长期按本人原始授权范围使用个人能力。"


def _user_identity_authorization_actions(
    context: ToolContext | None = None,
    *,
    resource_types: tuple[str, ...] | None = None,
) -> list[dict[str, object]]:
    base_url = settings.api_base_url.rstrip("/")
    url_status = user_identity_authorization_url_status(base_url)
    query = ""
    if context is not None and context.actor.open_id:
        query = urlencode({"company_id": str(context.company_id), "open_id": context.actor.open_id})
    return [
        {
            "resource_type": USER_IDENTITY_BUNDLE_RESOURCE,
            "label": "授权个人能力包",
            "channel": "feishu_oauth",
            "authorization_flow": "feishu_in_app_oauth",
            "url": f"{base_url}/api/user-identity/oauth/feishu/start?{query}"
            if query
            else f"{base_url}/api/user-identity/oauth/feishu/start",
            "start_endpoint": "/api/user-identity/oauth/feishu/start",
            "callback_endpoint": "/api/feishu/oauth/callback",
            "instruction": "从大飞哥授权卡片打开飞书内授权页，由资源所有者本人确认授权；系统只按本人原始授权范围读取个人飞书资源。",
            "fallback_debug_flow": "feishu_cli_split_flow",
            "fallback_debug_url": f"{base_url}/user-auth/feishu-cli?{query}" if query else f"{base_url}/user-auth/feishu-cli",
            "covered_resources": list(USER_IDENTITY_RESOURCES),
            "employee_reachable": url_status["employee_reachable"],
            "local_only": url_status["local_only"],
            "api_base_url_status": url_status,
        }
    ]


def _data_permission_denied_answer(reason: str | None) -> str:
    if reason and reason.startswith("user_identity_authorization_required:"):
        return (
            "这个 Tool 是全局共享能力，但当前个人资源还没有完成本人授权。"
            "请先由资源所有者本人完成一次用户级整体授权；授权前我不会读取任何个人资源。"
        )
    return (
        "这个 Tool 是全局共享能力，但当前身份没有访问对应企业级或用户级资源的授权。"
        "我不会突破飞书 App Identity、Company Scope、Role Scope 或用户原始授权范围。"
    )


def _actor_has_permission(actor: BotActor, permission: str, *, chat_id: str | None) -> bool:
    if permission in {"company:read", "report:read_owner"}:
        return actor.can_query_company
    if permission == "approval:read":
        return actor.can_query_company or "approval" in actor.domains
    if permission == "approval:write":
        return actor.can_query_company or ("approval" in actor.domains and actor.role in {"admin", "manager", "lead"})
    if permission == "okr:read":
        return actor.can_query_company or bool({"okr", "management", "project"} & set(actor.domains))
    if permission == "calendar:read":
        return actor.can_query_company or "calendar" in actor.domains or "meeting" in actor.domains
    if permission == "calendar:write":
        return actor.can_query_company or (
            ("calendar" in actor.domains or "meeting" in actor.domains) and actor.role in {"admin", "manager", "lead"}
        )
    if permission == "im:write":
        return actor.can_query_company or (
            ("im" in actor.domains or "chat" in actor.domains) and actor.role in {"admin", "manager", "lead"}
        )
    if permission == "contact:read":
        return actor.can_query_company or bool({"contact", "directory", "hr", "admin"} & set(actor.domains))
    if permission == "bitable:read":
        return actor.can_query_company or "bitable" in actor.domains
    if permission == "bitable:write":
        return actor.can_query_company or ("bitable" in actor.domains and actor.role in {"admin", "manager", "lead"})
    if permission == "domain:read":
        return actor.can_query_company or actor.access_scope in {"domain", "department", "project"}
    if permission == "chat:read_current":
        return bool(chat_id) or actor.access_scope in {"chat", "personal", "self", "user"}
    if permission == "task:read":
        return actor.can_query_company or bool(chat_id)
    if permission == "task:read_self":
        return actor.role != "guest"
    if permission == "task:write":
        return actor.can_query_company or actor.role in {"admin", "manager", "lead"}
    if permission == "knowledge:read_public":
        return True
    if permission == "knowledge:read":
        return actor.can_query_company or bool({"knowledge", "doc", "wiki"} & set(actor.domains))
    if permission == "mail:read_owner":
        return False
    if permission == "system:admin":
        return actor.role in {"owner", "admin"} and actor.access_scope in {"company", "all"}
    return False


def _record_tool_execution(
    context: ToolContext,
    definition: ToolDefinition,
    request: ToolRequest,
    result: ToolResult,
    *,
    duration_ms: int,
) -> None:
    if context.db is None:
        return
    write_audit_log(
        context.db,
        action=definition.audit_action,
        company_id=context.company_id,
        actor=context.actor.open_id or context.actor.display_name or context.actor.role,
        target_type="tool",
        target_id=request.tool_name,
        payload={
            "provider": result.provider.value,
            "business_tool": result.metadata.get("business_tool"),
            "status": result.status.value,
            "error": result.error,
            "duration_ms": duration_ms,
            "required_permissions": list(definition.required_permissions),
            "supports_write": definition.supports_write,
            "data_source": result.data_source,
            "execution_source": result.execution_source,
            "source_chain": result.metadata.get("source_chain"),
            "source_kind": result.metadata.get("source_kind"),
            "tool_decides_data_or_execution_source": result.metadata.get("tool_decides_data_or_execution_source"),
            "tool_returns_structured_result": result.metadata.get("tool_returns_structured_result"),
            "final_answer_owner": result.metadata.get("final_answer_owner"),
            "data_permission_model": result.metadata.get("data_permission_model"),
            "data_boundary_policy": result.metadata.get("data_boundary_policy"),
            "company_scope": result.metadata.get("company_scope"),
            "role_scope": result.metadata.get("role_scope"),
            "enterprise_identity": result.metadata.get("enterprise_identity"),
            "enterprise_identity_boundary": result.metadata.get("enterprise_identity_boundary"),
            "enterprise_identity_constraints": result.metadata.get("app_identity_constraints"),
            "enterprise_company_scope": result.metadata.get("enterprise_company_scope"),
            "enterprise_role_scope": result.metadata.get("enterprise_role_scope"),
            "can_exceed_feishu_app_permissions": result.metadata.get("can_exceed_feishu_app_permissions"),
            "user_identity": result.metadata.get("user_identity"),
            "user_identity_boundary": result.metadata.get("user_identity_boundary"),
            "user_identity_owner_open_id": result.metadata.get("user_identity_owner_open_id"),
            "can_exceed_user_original_authorization": result.metadata.get("can_exceed_user_original_authorization"),
            "data_boundary_enforcement": result.metadata.get("data_boundary_enforcement"),
            "digital_advisor_permission_policy": result.metadata.get("digital_advisor_permission_policy"),
            "cannot_escalate_original_permissions": result.metadata.get("cannot_escalate_original_permissions"),
            "preferred_execution_engine": result.metadata.get("preferred_execution_engine"),
            "realtime_policy": result.metadata.get("realtime_policy"),
            "realtime_bridge": result.metadata.get("realtime_bridge"),
            "mcp_provider": result.metadata.get("mcp_provider"),
            "api_role": result.metadata.get("api_role"),
            "responsibility_boundary": result.metadata.get("responsibility_boundary"),
            "execution_chain": result.metadata.get("execution_chain"),
            "agent_runtime_direct_access": result.metadata.get("agent_runtime_direct_access"),
            "sync_engine_direct_api_allowed": result.metadata.get("sync_engine_direct_api_allowed"),
            "sync_engine_mcp_access_allowed": result.metadata.get("sync_engine_mcp_access_allowed"),
            "cli_profile": result.metadata.get("cli_profile"),
            "cli_profile_source": result.metadata.get("cli_profile_source"),
            "write_mode": result.metadata.get("write_mode"),
            "dry_run": result.metadata.get("dry_run"),
            "confirmed": result.metadata.get("confirmed"),
            "has_confirmation_token": result.metadata.get("has_confirmation_token"),
            "write_target": result.metadata.get("write_target"),
            "chat_id": context.chat_id,
        },
    )


def _duration_ms(started: float) -> int:
    return max(0, int((perf_counter() - started) * 1000))


def _effective_definition(context: ToolContext, definition: ToolDefinition) -> ToolDefinition:
    from app.services.tools.config import effective_tool_definition

    return effective_tool_definition(context.db, company_id=context.company_id, definition=definition)


def _definition_for_request(definition: ToolDefinition, request: ToolRequest) -> ToolDefinition:
    if (
        definition.name == "bitable_qa"
        and definition.provider == ToolProvider.FEISHU_MCP
        and not _has_explicit_bitable_runtime_target(request)
    ):
        return replace(definition, provider=ToolProvider.LOCAL)
    return definition


def _has_explicit_bitable_runtime_target(request: ToolRequest) -> bool:
    target_keys = {"app_token", "base_token", "table_id", "record_id", "view_id"}
    return any(request.params.get(key) not in (None, "", [], {}) for key in target_keys)


def _request_with_provider_runtime_params(
    context: ToolContext,
    definition: ToolDefinition,
    request: ToolRequest,
) -> ToolRequest:
    request = _strip_agent_supplied_runtime_params(request)
    if definition.provider == ToolProvider.FEISHU_MCP and context.cli_profile:
        return replace(request, params={**request.params, "cli_profile": context.cli_profile})
    return request


def _strip_agent_supplied_runtime_params(request: ToolRequest) -> ToolRequest:
    runtime_only_keys = {"client", "app_config", "profile", "cli_profile", "lark_profile"}
    if not runtime_only_keys & set(request.params):
        return request
    return replace(request, params={key: value for key, value in request.params.items() if key not in runtime_only_keys})


