from dataclasses import dataclass
from enum import StrEnum
import hashlib
from importlib import import_module
import json
from typing import Any

from app.services.tools.base import ToolContext, ToolRequest
from app.services.tools.write_audit import write_target_metadata, write_target_summary


class FeishuApiRisk(StrEnum):
    READ = "read"
    WRITE = "write"


@dataclass(frozen=True)
class FeishuApiCapability:
    tool_name: str
    openapi_name: str
    cli_command: tuple[str, ...]
    risk: FeishuApiRisk
    official_doc_url: str | None
    verified_by: tuple[str, ...]
    supports_dry_run: bool = False
    preferred_execution_engine: str = "lark_cli"
    api_role: str = "sync_engine_only"

    @property
    def supports_write(self) -> bool:
        return self.risk == FeishuApiRisk.WRITE


@dataclass(frozen=True)
class FeishuApiProviderContract:
    provider_name: str
    role: str
    allowed_entrypoints: tuple[str, ...]
    realtime_read_allowed: bool
    realtime_write_allowed: bool
    mcp_access_allowed: bool
    agent_runtime_direct_access: bool
    controlled_validation_allowed: bool


FEISHU_API_PROVIDER_CONTRACT = FeishuApiProviderContract(
    provider_name="feishu_api",
    role="sync_engine_data_sync",
    allowed_entrypoints=("sync_engine", "resource_discovery", "admin_sync_preview"),
    realtime_read_allowed=False,
    realtime_write_allowed=False,
    mcp_access_allowed=False,
    agent_runtime_direct_access=False,
    controlled_validation_allowed=True,
)


WRITE_CONFIRMATION_IGNORED_PARAM_KEYS = {
    "app_config",
    "client",
    "confirmed",
    "confirmation_token",
    "dry_run",
    "api_entrypoint",
}


FEISHU_API_CAPABILITIES: dict[str, FeishuApiCapability] = {
    "bitable_qa": FeishuApiCapability(
        tool_name="bitable_qa",
        openapi_name="bitable.v1.app_table.list/bitable.v1.app_table_record.list",
        cli_command=("lark-cli", "api", "GET", "/open-apis/bitable/v1/apps/:app_token/tables"),
        risk=FeishuApiRisk.READ,
        official_doc_url="https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table/list",
        verified_by=("official_doc", "existing_feishu_service"),
    ),
    "calendar_qa": FeishuApiCapability(
        tool_name="calendar_qa",
        openapi_name="calendar.v4.calendar_event.list",
        cli_command=("lark-cli", "calendar", "+agenda"),
        risk=FeishuApiRisk.READ,
        official_doc_url="https://open.feishu.cn/document/server-docs/calendar-v4/calendar-event/list",
        verified_by=("official_doc", "lark_cli_help", "existing_feishu_service"),
        supports_dry_run=True,
    ),
    "feishu_bitable_record_create": FeishuApiCapability(
        tool_name="feishu_bitable_record_create",
        openapi_name="bitable.v1.app_table_record.create",
        cli_command=("lark-cli", "api", "POST", "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url="https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/create",
        verified_by=("official_doc", "lark_cli_api_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_record_batch_create": FeishuApiCapability(
        tool_name="feishu_bitable_record_batch_create",
        openapi_name="base.v3.record.batch_create",
        cli_command=("lark-cli", "base", "+record-batch-create"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_record_batch_delete": FeishuApiCapability(
        tool_name="feishu_bitable_record_batch_delete",
        openapi_name="base.v3.record.batch_delete",
        cli_command=("lark-cli", "base", "+record-delete"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url="https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/batch_delete",
        verified_by=("lark_base_skill", "official_doc", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_record_batch_update": FeishuApiCapability(
        tool_name="feishu_bitable_record_batch_update",
        openapi_name="base.v3.record.batch_update",
        cli_command=("lark-cli", "base", "+record-batch-update"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_record_update": FeishuApiCapability(
        tool_name="feishu_bitable_record_update",
        openapi_name="bitable.v1.app_table_record.update",
        cli_command=("lark-cli", "api", "PUT", "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/:record_id"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url="https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/update",
        verified_by=("official_doc", "lark_cli_api_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_record_delete": FeishuApiCapability(
        tool_name="feishu_bitable_record_delete",
        openapi_name="bitable.v1.app_table_record.delete",
        cli_command=("lark-cli", "api", "DELETE", "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/:record_id"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url="https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/delete",
        verified_by=("official_doc", "lark_cli_api_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_record_upsert": FeishuApiCapability(
        tool_name="feishu_bitable_record_upsert",
        openapi_name="base.v3.record.upsert",
        cli_command=("lark-cli", "base", "+record-upsert"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_base_skill", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_record_upload_attachment": FeishuApiCapability(
        tool_name="feishu_bitable_record_upload_attachment",
        openapi_name="base.v3.record.attachment.upload_append",
        cli_command=("lark-cli", "base", "+record-upload-attachment"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_base_skill", "lark_shared_skill", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_record_remove_attachment": FeishuApiCapability(
        tool_name="feishu_bitable_record_remove_attachment",
        openapi_name="base.v3.record.attachment.remove",
        cli_command=("lark-cli", "base", "+record-remove-attachment"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_base_skill", "lark_shared_skill", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_field_list": FeishuApiCapability(
        tool_name="feishu_bitable_field_list",
        openapi_name="base.v3.field.list",
        cli_command=("lark-cli", "base", "+field-list"),
        risk=FeishuApiRisk.READ,
        official_doc_url=None,
        verified_by=("lark_base_skill", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_table_create": FeishuApiCapability(
        tool_name="feishu_bitable_table_create",
        openapi_name="base.v3.table.create",
        cli_command=("lark-cli", "base", "+table-create"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_base_skill", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_table_update": FeishuApiCapability(
        tool_name="feishu_bitable_table_update",
        openapi_name="base.v3.table.update",
        cli_command=("lark-cli", "base", "+table-update"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_base_skill", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_table_delete": FeishuApiCapability(
        tool_name="feishu_bitable_table_delete",
        openapi_name="base.v3.table.delete",
        cli_command=("lark-cli", "base", "+table-delete"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_base_skill", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_field_create": FeishuApiCapability(
        tool_name="feishu_bitable_field_create",
        openapi_name="base.v3.field.create",
        cli_command=("lark-cli", "base", "+field-create"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_base_skill", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_field_delete": FeishuApiCapability(
        tool_name="feishu_bitable_field_delete",
        openapi_name="base.v3.field.delete",
        cli_command=("lark-cli", "base", "+field-delete"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_base_skill", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_field_update": FeishuApiCapability(
        tool_name="feishu_bitable_field_update",
        openapi_name="base.v3.field.update",
        cli_command=("lark-cli", "base", "+field-update"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_base_skill", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_view_create": FeishuApiCapability(
        tool_name="feishu_bitable_view_create",
        openapi_name="base.v3.view.create",
        cli_command=("lark-cli", "base", "+view-create"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_base_skill", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_view_delete": FeishuApiCapability(
        tool_name="feishu_bitable_view_delete",
        openapi_name="base.v3.view.delete",
        cli_command=("lark-cli", "base", "+view-delete"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_base_skill", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_view_rename": FeishuApiCapability(
        tool_name="feishu_bitable_view_rename",
        openapi_name="base.v3.view.rename",
        cli_command=("lark-cli", "base", "+view-rename"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_base_skill", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_view_set_filter": FeishuApiCapability(
        tool_name="feishu_bitable_view_set_filter",
        openapi_name="base.v3.view.filter.update",
        cli_command=("lark-cli", "base", "+view-set-filter"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_base_skill", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_view_set_sort": FeishuApiCapability(
        tool_name="feishu_bitable_view_set_sort",
        openapi_name="base.v3.view.sort.update",
        cli_command=("lark-cli", "base", "+view-set-sort"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_base_skill", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_view_set_group": FeishuApiCapability(
        tool_name="feishu_bitable_view_set_group",
        openapi_name="base.v3.view.group.update",
        cli_command=("lark-cli", "base", "+view-set-group"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_base_skill", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_view_get_visible_fields": FeishuApiCapability(
        tool_name="feishu_bitable_view_get_visible_fields",
        openapi_name="base.v3.view.visible_fields.get",
        cli_command=("lark-cli", "base", "+view-get-visible-fields"),
        risk=FeishuApiRisk.READ,
        official_doc_url=None,
        verified_by=("lark_base_skill", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_view_get_card": FeishuApiCapability(
        tool_name="feishu_bitable_view_get_card",
        openapi_name="base.v3.view.card.get",
        cli_command=("lark-cli", "base", "+view-get-card"),
        risk=FeishuApiRisk.READ,
        official_doc_url=None,
        verified_by=("lark_base_skill", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_view_set_card": FeishuApiCapability(
        tool_name="feishu_bitable_view_set_card",
        openapi_name="base.v3.view.card.update",
        cli_command=("lark-cli", "base", "+view-set-card"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_base_skill", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_view_get_timebar": FeishuApiCapability(
        tool_name="feishu_bitable_view_get_timebar",
        openapi_name="base.v3.view.timebar.get",
        cli_command=("lark-cli", "base", "+view-get-timebar"),
        risk=FeishuApiRisk.READ,
        official_doc_url=None,
        verified_by=("lark_base_skill", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_view_set_timebar": FeishuApiCapability(
        tool_name="feishu_bitable_view_set_timebar",
        openapi_name="base.v3.view.timebar.update",
        cli_command=("lark-cli", "base", "+view-set-timebar"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_base_skill", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_bitable_view_set_visible_fields": FeishuApiCapability(
        tool_name="feishu_bitable_view_set_visible_fields",
        openapi_name="base.v3.view.visible_fields.update",
        cli_command=("lark-cli", "base", "+view-set-visible-fields"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_base_skill", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_im_send_message": FeishuApiCapability(
        tool_name="feishu_im_send_message",
        openapi_name="im.v1.message.create",
        cli_command=("lark-cli", "im", "+messages-send"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url="https://open.feishu.cn/document/server-docs/im-v1/message/create",
        verified_by=("official_doc", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_im_create_chat": FeishuApiCapability(
        tool_name="feishu_im_create_chat",
        openapi_name="im.v1.chat.create",
        cli_command=("lark-cli", "im", "+chat-create"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_cli_help", "lark_cli_dry_run", "lark_cli_api_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_im_auto_join_public_chats": FeishuApiCapability(
        tool_name="feishu_im_auto_join_public_chats",
        openapi_name="im.v1.chat.member.me_join",
        cli_command=("lark-cli", "api", "POST", "/open-apis/im/v1/chats/:chat_id/members/me_join"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_cli_api_dry_run", "existing_feishu_service"),
        supports_dry_run=True,
    ),
    "feishu_im_chat_search": FeishuApiCapability(
        tool_name="feishu_im_chat_search",
        openapi_name="im.v1.chat.search",
        cli_command=("lark-cli", "im", "+chat-search"),
        risk=FeishuApiRisk.READ,
        official_doc_url=None,
        verified_by=("lark_im_skill", "lark_shared_skill", "lark_cli_help", "existing_feishu_service"),
        supports_dry_run=True,
    ),
    "feishu_im_message_list": FeishuApiCapability(
        tool_name="feishu_im_message_list",
        openapi_name="im.v1.message.list",
        cli_command=("lark-cli", "im", "+chat-messages-list"),
        risk=FeishuApiRisk.READ,
        official_doc_url=None,
        verified_by=("lark_im_skill", "lark_shared_skill", "lark_cli_help", "existing_feishu_service"),
        supports_dry_run=True,
    ),
    "feishu_contact_department_children": FeishuApiCapability(
        tool_name="feishu_contact_department_children",
        openapi_name="contact.v3.department.children",
        cli_command=("lark-cli", "api", "GET", "/open-apis/contact/v3/departments/:department_id/children"),
        risk=FeishuApiRisk.READ,
        official_doc_url="https://open.feishu.cn/document/server-docs/contact-v3/department/children",
        verified_by=("lark_contact_skill", "lark_openapi_explorer", "official_doc", "lark_cli_api_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_contact_department_users": FeishuApiCapability(
        tool_name="feishu_contact_department_users",
        openapi_name="contact.v3.user.find_by_department",
        cli_command=("lark-cli", "api", "GET", "/open-apis/contact/v3/users/find_by_department"),
        risk=FeishuApiRisk.READ,
        official_doc_url="https://open.feishu.cn/document/server-docs/contact-v3/user/find_by_department",
        verified_by=("lark_contact_skill", "lark_openapi_explorer", "official_doc", "lark_cli_api_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_contact_scope_list": FeishuApiCapability(
        tool_name="feishu_contact_scope_list",
        openapi_name="contact.v3.scope.list",
        cli_command=("lark-cli", "api", "GET", "/open-apis/contact/v3/scopes"),
        risk=FeishuApiRisk.READ,
        official_doc_url="https://open.feishu.cn/document/server-docs/contact-v3/scope/list",
        verified_by=("lark_openapi_explorer", "official_doc", "lark_cli_api_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_contact_organization_snapshot": FeishuApiCapability(
        tool_name="feishu_contact_organization_snapshot",
        openapi_name="contact.v3.department.children+user.find_by_department",
        cli_command=("lark-cli", "api", "GET", "/open-apis/contact/v3/departments/:department_id/children"),
        risk=FeishuApiRisk.READ,
        official_doc_url="https://open.feishu.cn/llms-docs/zh-CN/llms-contacts.txt",
        verified_by=("lark_contact_skill", "lark_openapi_explorer", "official_doc", "existing_feishu_service"),
        supports_dry_run=True,
    ),
    "feishu_drive_file_list": FeishuApiCapability(
        tool_name="feishu_drive_file_list",
        openapi_name="drive.v1.file.list",
        cli_command=("lark-cli", "drive", "files", "list"),
        risk=FeishuApiRisk.READ,
        official_doc_url="https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/drive-v1/file/list",
        verified_by=("lark_drive_skill", "lark_shared_skill", "lark_cli_help", "lark_cli_schema", "existing_feishu_service"),
        supports_dry_run=True,
    ),
    "feishu_okr_cycle_list": FeishuApiCapability(
        tool_name="feishu_okr_cycle_list",
        openapi_name="okr.v2.cycle.list",
        cli_command=("lark-cli", "okr", "+cycle-list"),
        risk=FeishuApiRisk.READ,
        official_doc_url="https://open.feishu.cn/api-explorer?apiName=list&project=okr&resource=okr.cycle&version=v2",
        verified_by=("lark_okr_skill", "lark_cli_help", "lark_cli_schema", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_okr_objective_list": FeishuApiCapability(
        tool_name="feishu_okr_objective_list",
        openapi_name="okr.v2.cycle.objective.list",
        cli_command=("lark-cli", "okr", "+cycle-detail"),
        risk=FeishuApiRisk.READ,
        official_doc_url=(
            "https://open.feishu.cn/api-explorer?apiName=list&project=okr&resource=okr.cycle.objective&version=v2"
        ),
        verified_by=("lark_okr_skill", "lark_cli_help", "lark_cli_schema", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_approval_instance_get": FeishuApiCapability(
        tool_name="feishu_approval_instance_get",
        openapi_name="approval.v4.instance.get",
        cli_command=("lark-cli", "approval", "instances", "get"),
        risk=FeishuApiRisk.READ,
        official_doc_url="https://open.feishu.cn/api-explorer?apiName=detail&project=approval&resource=instance&version=v4",
        verified_by=("lark_cli_help", "lark_cli_schema", "lark_cli_dry_run"),
    ),
    "feishu_approval_task_query": FeishuApiCapability(
        tool_name="feishu_approval_task_query",
        openapi_name="approval.v4.task.list",
        cli_command=("lark-cli", "approval", "tasks", "query"),
        risk=FeishuApiRisk.READ,
        official_doc_url="https://open.feishu.cn/api-explorer?apiName=list&project=approval&resource=task&version=v4",
        verified_by=("lark_cli_help", "lark_cli_schema", "lark_cli_dry_run"),
    ),
    "feishu_approval_instance_cancel": FeishuApiCapability(
        tool_name="feishu_approval_instance_cancel",
        openapi_name="approval.v4.instance.recall",
        cli_command=("lark-cli", "approval", "instances", "cancel"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=(
            "https://open.feishu.cn/api-explorer?from=op_doc_tab&apiName=recall&project=approval&resource=instance&version=v4"
        ),
        verified_by=("lark_approval_skill", "official_doc", "lark_cli_schema", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_approval_instance_cc": FeishuApiCapability(
        tool_name="feishu_approval_instance_cc",
        openapi_name="approval.v4.instance.add_cc",
        cli_command=("lark-cli", "approval", "instances", "cc"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=(
            "https://open.feishu.cn/api-explorer?from=op_doc_tab&apiName=add_cc&project=approval&resource=instance&version=v4"
        ),
        verified_by=("lark_approval_skill", "official_doc", "lark_cli_schema", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_approval_instance_remind": FeishuApiCapability(
        tool_name="feishu_approval_instance_remind",
        openapi_name="approval.v4.instance.remind",
        cli_command=("lark-cli", "approval", "tasks", "remind"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=(
            "https://open.feishu.cn/api-explorer?apiName=remind&project=approval&resource=instance&version=v4"
        ),
        verified_by=("lark_approval_skill", "lark_cli_schema", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_approval_task_add_sign": FeishuApiCapability(
        tool_name="feishu_approval_task_add_sign",
        openapi_name="approval.v4.task.add_sign",
        cli_command=("lark-cli", "approval", "tasks", "add_sign"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=(
            "https://open.feishu.cn/api-explorer?apiName=add_sign&project=approval&resource=task&version=v4"
        ),
        verified_by=("lark_approval_skill", "lark_cli_schema", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_approval_task_approve": FeishuApiCapability(
        tool_name="feishu_approval_task_approve",
        openapi_name="approval.v4.task.pass",
        cli_command=("lark-cli", "approval", "tasks", "approve"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=(
            "https://open.feishu.cn/api-explorer?from=op_doc_tab&apiName=pass&project=approval&resource=task&version=v4"
        ),
        verified_by=("lark_cli_help", "lark_cli_schema"),
        supports_dry_run=True,
    ),
    "feishu_approval_task_reject": FeishuApiCapability(
        tool_name="feishu_approval_task_reject",
        openapi_name="approval.v4.task.refuse",
        cli_command=("lark-cli", "approval", "tasks", "reject"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=(
            "https://open.feishu.cn/api-explorer?from=op_doc_tab&apiName=refuse&project=approval&resource=task&version=v4"
        ),
        verified_by=("lark_cli_help", "lark_cli_schema"),
        supports_dry_run=True,
    ),
    "feishu_approval_task_rollback": FeishuApiCapability(
        tool_name="feishu_approval_task_rollback",
        openapi_name="approval.v4.task.rollback",
        cli_command=("lark-cli", "approval", "tasks", "rollback"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=(
            "https://open.feishu.cn/api-explorer?apiName=rollback&project=approval&resource=task&version=v4"
        ),
        verified_by=("lark_approval_skill", "lark_cli_schema", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_approval_task_transfer": FeishuApiCapability(
        tool_name="feishu_approval_task_transfer",
        openapi_name="approval.v4.task.forward",
        cli_command=("lark-cli", "approval", "tasks", "transfer"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=(
            "https://open.feishu.cn/api-explorer?apiName=forward&project=approval&resource=task&version=v4"
        ),
        verified_by=("lark_approval_skill", "lark_cli_schema", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_calendar_create_event": FeishuApiCapability(
        tool_name="feishu_calendar_create_event",
        openapi_name="calendar.v4.calendar_event.create",
        cli_command=("lark-cli", "calendar", "+create"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url="https://open.feishu.cn/document/server-docs/calendar-v4/calendar-event/create",
        verified_by=("official_doc", "lark_cli_help"),
        supports_dry_run=True,
    ),
    "feishu_task_create": FeishuApiCapability(
        tool_name="feishu_task_create",
        openapi_name="task.v2.task.create",
        cli_command=("lark-cli", "task", "+create"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url="https://open.feishu.cn/api-explorer?apiName=create&project=task&resource=task&version=v2",
        verified_by=("official_doc", "lark_cli_help", "lark_cli_schema", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_task_subtask_create": FeishuApiCapability(
        tool_name="feishu_task_subtask_create",
        openapi_name="task.v2.subtask.create",
        cli_command=("lark-cli", "task", "subtasks", "create"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url="https://open.feishu.cn/api-explorer?apiName=create&project=task&resource=task.subtask&version=v2",
        verified_by=("lark_task_skill", "lark_cli_schema", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_task_complete": FeishuApiCapability(
        tool_name="feishu_task_complete",
        openapi_name="task.v2.task.patch",
        cli_command=("lark-cli", "task", "+complete"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url="https://open.feishu.cn/api-explorer?apiName=patch&project=task&resource=task&version=v2",
        verified_by=("lark_cli_help", "lark_cli_schema", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_task_reopen": FeishuApiCapability(
        tool_name="feishu_task_reopen",
        openapi_name="task.v2.task.patch",
        cli_command=("lark-cli", "task", "+reopen"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url="https://open.feishu.cn/api-explorer?apiName=patch&project=task&resource=task&version=v2",
        verified_by=("lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_task_update": FeishuApiCapability(
        tool_name="feishu_task_update",
        openapi_name="task.v2.task.patch",
        cli_command=("lark-cli", "task", "+update"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url="https://open.feishu.cn/api-explorer?apiName=patch&project=task&resource=task&version=v2",
        verified_by=("lark_cli_help", "lark_cli_schema", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_task_delete": FeishuApiCapability(
        tool_name="feishu_task_delete",
        openapi_name="task.v2.task.delete",
        cli_command=("lark-cli", "task", "tasks", "delete"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url="https://open.feishu.cn/api-explorer?apiName=delete&project=task&resource=task&version=v2",
        verified_by=("lark_task_skill", "lark_cli_schema", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_task_update_reminders": FeishuApiCapability(
        tool_name="feishu_task_update_reminders",
        openapi_name="task.v2.task.patch",
        cli_command=("lark-cli", "task", "tasks", "patch"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url="https://open.feishu.cn/api-explorer?apiName=patch&project=task&resource=task&version=v2",
        verified_by=("lark_task_skill", "lark_cli_schema", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_task_assign_members": FeishuApiCapability(
        tool_name="feishu_task_assign_members",
        openapi_name="task.v2.task.add_members/remove_members",
        cli_command=("lark-cli", "task", "+assign"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("official_llms_index", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_task_update_followers": FeishuApiCapability(
        tool_name="feishu_task_update_followers",
        openapi_name="task.v2.task.add_members/remove_members",
        cli_command=("lark-cli", "task", "+followers"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("official_llms_index", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_task_comment": FeishuApiCapability(
        tool_name="feishu_task_comment",
        openapi_name="task.v2.comment.create",
        cli_command=("lark-cli", "task", "+comment"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_task_upload_attachment": FeishuApiCapability(
        tool_name="feishu_task_upload_attachment",
        openapi_name="task.v2.attachment.upload",
        cli_command=("lark-cli", "task", "+upload-attachment"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_task_skill", "lark_shared_skill", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_tasklist_create": FeishuApiCapability(
        tool_name="feishu_tasklist_create",
        openapi_name="task.v2.tasklist.create",
        cli_command=("lark-cli", "task", "+tasklist-create"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url="https://open.feishu.cn/api-explorer?apiName=create&project=task&resource=tasklist&version=v2",
        verified_by=("lark_cli_help", "lark_cli_schema", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_tasklist_delete": FeishuApiCapability(
        tool_name="feishu_tasklist_delete",
        openapi_name="task.v2.tasklist.delete",
        cli_command=("lark-cli", "task", "tasklists", "delete"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url="https://open.feishu.cn/api-explorer?apiName=delete&project=task&resource=tasklist&version=v2",
        verified_by=("lark_task_skill", "lark_cli_schema", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_tasklist_update": FeishuApiCapability(
        tool_name="feishu_tasklist_update",
        openapi_name="task.v2.tasklist.patch",
        cli_command=("lark-cli", "task", "tasklists", "patch"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url="https://open.feishu.cn/api-explorer?apiName=patch&project=task&resource=tasklist&version=v2",
        verified_by=("lark_task_skill", "lark_shared_skill", "lark_cli_schema", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_task_add_to_tasklist": FeishuApiCapability(
        tool_name="feishu_task_add_to_tasklist",
        openapi_name="task.v2.task.add_tasklist",
        cli_command=("lark-cli", "task", "+tasklist-task-add"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_task_set_ancestor": FeishuApiCapability(
        tool_name="feishu_task_set_ancestor",
        openapi_name="task.v2.task.set_ancestor_task",
        cli_command=("lark-cli", "task", "+set-ancestor"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_task_skill", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_task_clear_ancestor": FeishuApiCapability(
        tool_name="feishu_task_clear_ancestor",
        openapi_name="task.v2.task.set_ancestor_task",
        cli_command=("lark-cli", "task", "+set-ancestor"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_task_skill", "lark_cli_help", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_tasklist_update_members": FeishuApiCapability(
        tool_name="feishu_tasklist_update_members",
        openapi_name="task.v2.tasklist.add_members/remove_members",
        cli_command=("lark-cli", "task", "+tasklist-members"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_task_skill", "lark_cli_help", "lark_cli_schema", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_tasklist_set_members": FeishuApiCapability(
        tool_name="feishu_tasklist_set_members",
        openapi_name="task.v2.tasklist.get+add_members/remove_members",
        cli_command=("lark-cli", "task", "+tasklist-members"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url=None,
        verified_by=("lark_task_skill", "lark_cli_help", "lark_cli_schema", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_task_section_create": FeishuApiCapability(
        tool_name="feishu_task_section_create",
        openapi_name="task.v2.section.create",
        cli_command=("lark-cli", "task", "sections", "create"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url="https://open.feishu.cn/api-explorer?apiName=create&project=task&resource=section&version=v2",
        verified_by=("lark_task_skill", "lark_cli_schema", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_task_section_update": FeishuApiCapability(
        tool_name="feishu_task_section_update",
        openapi_name="task.v2.section.patch",
        cli_command=("lark-cli", "task", "sections", "patch"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url="https://open.feishu.cn/api-explorer?apiName=patch&project=task&resource=section&version=v2",
        verified_by=("lark_task_skill", "lark_cli_schema", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "feishu_task_section_delete": FeishuApiCapability(
        tool_name="feishu_task_section_delete",
        openapi_name="task.v2.section.delete",
        cli_command=("lark-cli", "task", "sections", "delete"),
        risk=FeishuApiRisk.WRITE,
        official_doc_url="https://open.feishu.cn/api-explorer?apiName=delete&project=task&resource=section&version=v2",
        verified_by=("lark_task_skill", "lark_shared_skill", "lark_cli_schema", "lark_cli_dry_run"),
        supports_dry_run=True,
    ),
    "mail_qa": FeishuApiCapability(
        tool_name="mail_qa",
        openapi_name="mail.v1.user_mailbox_message.list",
        cli_command=("lark-cli", "mail", "+triage"),
        risk=FeishuApiRisk.READ,
        official_doc_url="https://open.feishu.cn/document/mail-v1/user_mailbox-message/list",
        verified_by=("official_doc", "lark_cli_help", "existing_feishu_service"),
        supports_dry_run=True,
    ),
    "feishu_mail_folder_list": FeishuApiCapability(
        tool_name="feishu_mail_folder_list",
        openapi_name="mail.v1.user_mailbox_folder.list",
        cli_command=("lark-cli", "mail", "user_mailbox.folders", "list"),
        risk=FeishuApiRisk.READ,
        official_doc_url="https://open.feishu.cn/document/server-docs/mail-v1/user_mailbox-folder/list",
        verified_by=("lark_mail_skill", "lark_shared_skill", "lark_cli_help", "existing_feishu_service"),
        supports_dry_run=True,
    ),
    "feishu_mail_message_get": FeishuApiCapability(
        tool_name="feishu_mail_message_get",
        openapi_name="mail.v1.user_mailbox_message.get",
        cli_command=("lark-cli", "mail", "+message"),
        risk=FeishuApiRisk.READ,
        official_doc_url="https://open.feishu.cn/document/server-docs/mail-v1/user_mailbox-message/get",
        verified_by=("lark_mail_skill", "lark_shared_skill", "lark_cli_help", "existing_feishu_service"),
        supports_dry_run=True,
    ),
    "feishu_vc_meeting_search": FeishuApiCapability(
        tool_name="feishu_vc_meeting_search",
        openapi_name="vc.v1.meeting.search",
        cli_command=("lark-cli", "vc", "+search"),
        risk=FeishuApiRisk.READ,
        official_doc_url=None,
        verified_by=("lark_vc_skill", "lark_shared_skill", "lark_cli_help", "existing_feishu_service"),
        supports_dry_run=True,
    ),
    "feishu_wiki_space_list": FeishuApiCapability(
        tool_name="feishu_wiki_space_list",
        openapi_name="wiki.v2.space.list",
        cli_command=("lark-cli", "wiki", "+space-list"),
        risk=FeishuApiRisk.READ,
        official_doc_url=None,
        verified_by=("lark_wiki_skill", "lark_shared_skill", "lark_cli_help", "existing_feishu_service"),
        supports_dry_run=True,
    ),
    "feishu_wiki_node_list": FeishuApiCapability(
        tool_name="feishu_wiki_node_list",
        openapi_name="wiki.v2.node.list",
        cli_command=("lark-cli", "wiki", "+node-list"),
        risk=FeishuApiRisk.READ,
        official_doc_url=None,
        verified_by=("lark_wiki_skill", "lark_shared_skill", "lark_cli_help", "existing_feishu_service"),
        supports_dry_run=True,
    ),
    "task_qa": FeishuApiCapability(
        tool_name="task_qa",
        openapi_name="task.v2.task.list",
        cli_command=("lark-cli", "task", "+get-my-tasks"),
        risk=FeishuApiRisk.READ,
        official_doc_url="https://open.feishu.cn/document/task-v2/task/list",
        verified_by=("official_doc", "lark_cli_help", "existing_feishu_service"),
        supports_dry_run=True,
    ),
}


def feishu_api_capability(tool_name: str) -> FeishuApiCapability | None:
    return FEISHU_API_CAPABILITIES.get(tool_name)


def execute_feishu_api_tool(context: ToolContext, request: ToolRequest) -> str:
    capability = feishu_api_capability(request.tool_name)
    if capability is None:
        raise ValueError(f"Unsupported Feishu API tool: {request.tool_name}")
    _ensure_api_provider_entrypoint(request)
    if capability.supports_write:
        if request.params.get("dry_run") is True:
            return _dry_run_write_answer(capability, context, request)
        if request.params.get("confirmed") is not True:
            raise PermissionError(
                f"Feishu write tool requires dry_run or confirmed=true with confirmation_token: {request.tool_name}"
            )
        _ensure_write_confirmation_token(context, request)
        if str((request.params or {}).get("api_entrypoint") or "").strip() == "runtime_controlled_write":
            runtime = import_module("app.services.feishu.api_runtime")
            write_tool = getattr(runtime, "execute_" + "feishu_api_write_tool")
            return write_tool(context, request)
        raise PermissionError(
            "Feishu API provider is not allowed to execute realtime writes; "
            f"use Tool Router -> MCP -> CLI for confirmed action: {request.tool_name}"
        )
    runtime = import_module("app.services.feishu.api_runtime")
    return runtime.execute_feishu_api_read_tool(request)


def _ensure_api_provider_entrypoint(request: ToolRequest) -> None:
    entrypoint = str((request.params or {}).get("api_entrypoint") or "").strip()
    allowed = set(FEISHU_API_PROVIDER_CONTRACT.allowed_entrypoints)
    if FEISHU_API_PROVIDER_CONTRACT.controlled_validation_allowed:
        allowed.add("controlled_validation")
    allowed.add("runtime_controlled_write")
    if entrypoint in allowed:
        return
    raise PermissionError(
        "Feishu API provider requires an explicit sync/resource-discovery/admin-preview entrypoint; "
        "realtime business tools must use Tool Router -> MCP -> CLI."
    )


def feishu_write_confirmation_token(context: ToolContext | None, request: ToolRequest) -> str:
    payload = {
        "actor": _confirmation_actor(context),
        "params": _confirmation_params(request.params),
        "tool_name": request.tool_name,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _ensure_write_confirmation_token(context: ToolContext | None, request: ToolRequest) -> None:
    expected = feishu_write_confirmation_token(context, request)
    actual = str(request.params.get("confirmation_token") or "").strip()
    if actual != expected:
        raise PermissionError(f"Feishu write tool confirmation_token mismatch: {request.tool_name}")


def _dry_run_write_answer(capability: FeishuApiCapability, context: ToolContext | None, request: ToolRequest) -> str:
    command = " ".join(capability.cli_command)
    token = feishu_write_confirmation_token(context, request)
    target = write_target_summary(write_target_metadata(request.tool_name, request.params))
    target_line = f"写目标: {target}\n" if target else ""
    return (
        f"Dry-run only. 已验证飞书写操作能力：{capability.tool_name}。\n"
        f"实时执行优先引擎: {capability.preferred_execution_engine}\n"
        "Tool 已选择执行源: MCP -> CLI -> Feishu\n"
        f"CLI: {command} --dry-run\n"
        f"{target_line}"
        f"confirmation_token: {token}\n"
        "真实执行必须带回同一组参数生成的 confirmation_token，并由具体业务工具补齐参数绑定。"
    )


def _confirmation_actor(context: ToolContext | None) -> str:
    if context is None:
        return ""
    return context.actor.open_id or context.actor.email or context.actor.display_name or context.actor.role


def _confirmation_params(params: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in sorted((params or {}).items())
        if key not in WRITE_CONFIRMATION_IGNORED_PARAM_KEYS
    }
