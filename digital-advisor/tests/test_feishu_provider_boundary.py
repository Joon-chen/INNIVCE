from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from uuid import uuid4

from app.services.agent.policies import BotActor
from app.services.feishu.api_runtime import (
    FEISHU_API_READ_BINDINGS,
    FEISHU_API_WRITE_BINDINGS,
    execute_feishu_api_read_tool,
    execute_feishu_api_write_tool,
)
from app.services.tools.base import ToolContext, ToolProvider, ToolRequest
from app.services.tools.providers.feishu_api import (
    FEISHU_API_CAPABILITIES,
    FEISHU_API_PROVIDER_CONTRACT,
    FeishuApiRisk,
    execute_feishu_api_tool,
)
from app.services.tools.providers.feishu_api import feishu_write_confirmation_token
from app.services.tools.providers.feishu_mcp import (
    FEISHU_MCP_CONTRACT,
    execute_feishu_mcp_realtime_tool,
    execute_feishu_mcp_tool,
    feishu_mcp_bound_tool_names,
    feishu_mcp_realtime_tool_names,
)
from app.services.tools.providers.lark_cli import LARK_CLI_EXECUTION_CONTRACT
from app.services.tools.router import TOOL_REGISTRY


def test_feishu_api_capabilities_are_verified_before_runtime_binding() -> None:
    assert set(FEISHU_API_CAPABILITIES) == {
        "bitable_qa",
        "calendar_qa",
        "feishu_bitable_record_batch_create",
        "feishu_bitable_record_batch_delete",
        "feishu_bitable_record_batch_update",
        "feishu_bitable_record_create",
        "feishu_bitable_record_delete",
        "feishu_bitable_record_remove_attachment",
        "feishu_bitable_record_update",
        "feishu_bitable_record_upload_attachment",
        "feishu_bitable_record_upsert",
        "feishu_bitable_field_create",
        "feishu_bitable_field_delete",
        "feishu_bitable_field_list",
        "feishu_bitable_field_update",
        "feishu_bitable_table_create",
        "feishu_bitable_table_delete",
        "feishu_bitable_table_update",
        "feishu_bitable_view_create",
        "feishu_bitable_view_delete",
        "feishu_bitable_view_rename",
        "feishu_bitable_view_get_card",
        "feishu_bitable_view_get_timebar",
        "feishu_bitable_view_get_visible_fields",
        "feishu_bitable_view_set_card",
        "feishu_bitable_view_set_filter",
        "feishu_bitable_view_set_group",
        "feishu_bitable_view_set_sort",
        "feishu_bitable_view_set_timebar",
        "feishu_bitable_view_set_visible_fields",
        "feishu_approval_instance_cancel",
        "feishu_approval_instance_cc",
        "feishu_approval_instance_remind",
        "feishu_approval_instance_get",
        "feishu_approval_task_add_sign",
        "feishu_approval_task_approve",
        "feishu_approval_task_query",
        "feishu_approval_task_reject",
        "feishu_approval_task_rollback",
        "feishu_approval_task_transfer",
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
        "feishu_task_assign_members",
        "feishu_task_comment",
        "feishu_task_clear_ancestor",
        "feishu_task_complete",
        "feishu_task_create",
        "feishu_task_delete",
        "feishu_task_subtask_create",
        "feishu_task_add_to_tasklist",
        "feishu_task_section_create",
        "feishu_task_section_delete",
        "feishu_task_section_update",
        "feishu_task_reopen",
        "feishu_task_set_ancestor",
        "feishu_task_update",
        "feishu_task_update_followers",
        "feishu_task_update_reminders",
        "feishu_task_upload_attachment",
        "feishu_tasklist_create",
        "feishu_tasklist_delete",
        "feishu_tasklist_update",
        "feishu_tasklist_set_members",
        "feishu_tasklist_update_members",
        "mail_qa",
        "task_qa",
    }
    for capability in FEISHU_API_CAPABILITIES.values():
        assert capability.cli_command[0] == "lark-cli"
        assert capability.verified_by
        assert capability.preferred_execution_engine == "lark_cli"
        assert capability.api_role == "sync_engine_only"
        if capability.risk == FeishuApiRisk.WRITE:
            assert capability.supports_write is True
            assert capability.supports_dry_run is True


def test_feishu_api_capabilities_have_complete_router_mcp_and_runtime_bindings() -> None:
    write_capabilities = {
        tool_name for tool_name, capability in FEISHU_API_CAPABILITIES.items() if capability.risk == FeishuApiRisk.WRITE
    }
    read_capabilities = set(FEISHU_API_CAPABILITIES) - write_capabilities

    assert write_capabilities - set(TOOL_REGISTRY) == set()
    assert write_capabilities - set(FEISHU_API_WRITE_BINDINGS) == set()
    assert write_capabilities - set(feishu_mcp_realtime_tool_names()) == set()
    assert read_capabilities - set(TOOL_REGISTRY) == set()
    assert read_capabilities - set(FEISHU_API_READ_BINDINGS) == set()
    assert read_capabilities - (set(feishu_mcp_bound_tool_names()) | set(feishu_mcp_realtime_tool_names())) == set()
    assert {
        tool_name
        for tool_name, definition in TOOL_REGISTRY.items()
        if tool_name in FEISHU_API_CAPABILITIES and definition.provider != ToolProvider.FEISHU_MCP
    } == set()


def test_feishu_api_capabilities_keep_official_docs_when_confirmed() -> None:
    assert FEISHU_API_CAPABILITIES["feishu_im_send_message"].official_doc_url.endswith("/im-v1/message/create")
    assert FEISHU_API_CAPABILITIES["feishu_im_auto_join_public_chats"].official_doc_url is None
    assert FEISHU_API_CAPABILITIES["feishu_im_create_chat"].official_doc_url is None
    assert FEISHU_API_CAPABILITIES["feishu_calendar_create_event"].official_doc_url.endswith(
        "/calendar-v4/calendar-event/create"
    )
    assert FEISHU_API_CAPABILITIES["calendar_qa"].official_doc_url.endswith("/calendar-v4/calendar-event/list")
    assert FEISHU_API_CAPABILITIES["feishu_contact_department_children"].cli_command == (
        "lark-cli",
        "api",
        "GET",
        "/open-apis/contact/v3/departments/:department_id/children",
    )
    assert FEISHU_API_CAPABILITIES["feishu_contact_department_users"].official_doc_url.endswith(
        "/contact-v3/user/find_by_department"
    )
    assert FEISHU_API_CAPABILITIES["feishu_contact_scope_list"].cli_command == (
        "lark-cli",
        "api",
        "GET",
        "/open-apis/contact/v3/scopes",
    )
    assert "apiName=create" in FEISHU_API_CAPABILITIES["feishu_task_create"].official_doc_url
    assert "apiName=create" in FEISHU_API_CAPABILITIES["feishu_task_subtask_create"].official_doc_url
    assert "apiName=patch" in FEISHU_API_CAPABILITIES["feishu_tasklist_update"].official_doc_url
    assert "apiName=patch" in FEISHU_API_CAPABILITIES["feishu_task_complete"].official_doc_url
    assert "apiName=patch" in FEISHU_API_CAPABILITIES["feishu_task_reopen"].official_doc_url
    assert "apiName=patch" in FEISHU_API_CAPABILITIES["feishu_task_update"].official_doc_url
    assert "apiName=delete" in FEISHU_API_CAPABILITIES["feishu_task_delete"].official_doc_url
    assert FEISHU_API_CAPABILITIES["feishu_task_delete"].cli_command == (
        "lark-cli",
        "task",
        "tasks",
        "delete",
    )
    assert "apiName=patch" in FEISHU_API_CAPABILITIES["feishu_task_update_reminders"].official_doc_url
    assert FEISHU_API_CAPABILITIES["feishu_task_assign_members"].cli_command == (
        "lark-cli",
        "task",
        "+assign",
    )
    assert FEISHU_API_CAPABILITIES["feishu_task_update_followers"].cli_command == (
        "lark-cli",
        "task",
        "+followers",
    )
    assert FEISHU_API_CAPABILITIES["feishu_task_comment"].official_doc_url is None
    assert FEISHU_API_CAPABILITIES["feishu_task_upload_attachment"].cli_command == (
        "lark-cli",
        "task",
        "+upload-attachment",
    )
    assert "apiName=create" in FEISHU_API_CAPABILITIES["feishu_tasklist_create"].official_doc_url
    assert "apiName=delete" in FEISHU_API_CAPABILITIES["feishu_tasklist_delete"].official_doc_url
    assert FEISHU_API_CAPABILITIES["feishu_tasklist_delete"].cli_command == (
        "lark-cli",
        "task",
        "tasklists",
        "delete",
    )
    assert FEISHU_API_CAPABILITIES["feishu_task_add_to_tasklist"].cli_command == (
        "lark-cli",
        "task",
        "+tasklist-task-add",
    )
    assert FEISHU_API_CAPABILITIES["feishu_task_set_ancestor"].cli_command == (
        "lark-cli",
        "task",
        "+set-ancestor",
    )
    assert FEISHU_API_CAPABILITIES["feishu_task_clear_ancestor"].cli_command == (
        "lark-cli",
        "task",
        "+set-ancestor",
    )
    assert FEISHU_API_CAPABILITIES["feishu_tasklist_update_members"].cli_command == (
        "lark-cli",
        "task",
        "+tasklist-members",
    )
    assert FEISHU_API_CAPABILITIES["feishu_tasklist_set_members"].cli_command == (
        "lark-cli",
        "task",
        "+tasklist-members",
    )
    assert FEISHU_API_CAPABILITIES["feishu_task_section_create"].cli_command == (
        "lark-cli",
        "task",
        "sections",
        "create",
    )
    assert "apiName=delete" in FEISHU_API_CAPABILITIES["feishu_task_section_delete"].official_doc_url
    assert FEISHU_API_CAPABILITIES["task_qa"].official_doc_url.endswith("/task-v2/task/list")
    assert FEISHU_API_CAPABILITIES["mail_qa"].official_doc_url.endswith("/mail-v1/user_mailbox-message/list")
    assert FEISHU_API_CAPABILITIES["bitable_qa"].official_doc_url.endswith("/bitable-v1/app-table/list")
    assert FEISHU_API_CAPABILITIES["feishu_bitable_record_create"].official_doc_url.endswith(
        "/bitable-v1/app-table-record/create"
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_record_batch_create"].cli_command == (
        "lark-cli",
        "base",
        "+record-batch-create",
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_record_batch_delete"].cli_command == (
        "lark-cli",
        "base",
        "+record-delete",
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_record_batch_update"].cli_command == (
        "lark-cli",
        "base",
        "+record-batch-update",
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_record_upload_attachment"].cli_command == (
        "lark-cli",
        "base",
        "+record-upload-attachment",
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_record_remove_attachment"].cli_command == (
        "lark-cli",
        "base",
        "+record-remove-attachment",
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_record_update"].official_doc_url.endswith(
        "/bitable-v1/app-table-record/update"
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_record_delete"].official_doc_url.endswith(
        "/bitable-v1/app-table-record/delete"
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_record_upsert"].cli_command == (
        "lark-cli",
        "base",
        "+record-upsert",
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_table_create"].cli_command == (
        "lark-cli",
        "base",
        "+table-create",
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_table_update"].cli_command == (
        "lark-cli",
        "base",
        "+table-update",
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_table_delete"].cli_command == (
        "lark-cli",
        "base",
        "+table-delete",
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_field_create"].cli_command == (
        "lark-cli",
        "base",
        "+field-create",
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_field_delete"].cli_command == (
        "lark-cli",
        "base",
        "+field-delete",
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_field_update"].cli_command == (
        "lark-cli",
        "base",
        "+field-update",
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_field_list"].cli_command == (
        "lark-cli",
        "base",
        "+field-list",
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_view_create"].cli_command == (
        "lark-cli",
        "base",
        "+view-create",
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_view_delete"].cli_command == (
        "lark-cli",
        "base",
        "+view-delete",
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_view_rename"].cli_command == (
        "lark-cli",
        "base",
        "+view-rename",
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_view_set_filter"].cli_command == (
        "lark-cli",
        "base",
        "+view-set-filter",
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_view_set_sort"].cli_command == (
        "lark-cli",
        "base",
        "+view-set-sort",
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_view_set_group"].cli_command == (
        "lark-cli",
        "base",
        "+view-set-group",
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_view_get_visible_fields"].cli_command == (
        "lark-cli",
        "base",
        "+view-get-visible-fields",
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_view_set_visible_fields"].cli_command == (
        "lark-cli",
        "base",
        "+view-set-visible-fields",
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_view_get_card"].cli_command == (
        "lark-cli",
        "base",
        "+view-get-card",
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_view_set_card"].cli_command == (
        "lark-cli",
        "base",
        "+view-set-card",
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_view_get_timebar"].cli_command == (
        "lark-cli",
        "base",
        "+view-get-timebar",
    )
    assert FEISHU_API_CAPABILITIES["feishu_bitable_view_set_timebar"].cli_command == (
        "lark-cli",
        "base",
        "+view-set-timebar",
    )
    assert "apiName=list" in FEISHU_API_CAPABILITIES["feishu_approval_task_query"].official_doc_url
    assert "apiName=detail" in FEISHU_API_CAPABILITIES["feishu_approval_instance_get"].official_doc_url
    assert "apiName=recall" in FEISHU_API_CAPABILITIES["feishu_approval_instance_cancel"].official_doc_url
    assert "apiName=add_cc" in FEISHU_API_CAPABILITIES["feishu_approval_instance_cc"].official_doc_url
    assert "apiName=remind" in FEISHU_API_CAPABILITIES["feishu_approval_instance_remind"].official_doc_url
    assert "apiName=add_sign" in FEISHU_API_CAPABILITIES["feishu_approval_task_add_sign"].official_doc_url
    assert "apiName=pass" in FEISHU_API_CAPABILITIES["feishu_approval_task_approve"].official_doc_url
    assert "apiName=refuse" in FEISHU_API_CAPABILITIES["feishu_approval_task_reject"].official_doc_url
    assert "apiName=rollback" in FEISHU_API_CAPABILITIES["feishu_approval_task_rollback"].official_doc_url
    assert "apiName=forward" in FEISHU_API_CAPABILITIES["feishu_approval_task_transfer"].official_doc_url
    assert "project=okr" in FEISHU_API_CAPABILITIES["feishu_okr_cycle_list"].official_doc_url
    assert "resource=okr.cycle.objective" in FEISHU_API_CAPABILITIES["feishu_okr_objective_list"].official_doc_url


def test_bitable_capability_does_not_claim_lark_cli_shortcut() -> None:
    capability = FEISHU_API_CAPABILITIES["bitable_qa"]

    assert "lark_cli_help" not in capability.verified_by
    assert capability.cli_command[:3] == ("lark-cli", "api", "GET")


def test_feishu_api_capabilities_match_tool_registry_contract() -> None:
    for tool_name, capability in FEISHU_API_CAPABILITIES.items():
        definition = TOOL_REGISTRY[tool_name]
        if capability.risk == FeishuApiRisk.WRITE:
            assert definition.provider == ToolProvider.FEISHU_MCP
            assert definition.supports_write is True
            assert any(permission.endswith(":write") for permission in definition.required_permissions)
        elif tool_name.startswith("feishu_"):
            assert definition.provider == ToolProvider.FEISHU_MCP
            assert definition.supports_write is False


def test_feishu_api_runtime_has_dispatch_binding_for_every_capability() -> None:
    assert set(FEISHU_API_READ_BINDINGS) == {
        tool_name
        for tool_name, capability in FEISHU_API_CAPABILITIES.items()
        if capability.risk == FeishuApiRisk.READ
    }
    assert set(FEISHU_API_WRITE_BINDINGS) == {
        tool_name
        for tool_name, capability in FEISHU_API_CAPABILITIES.items()
        if capability.risk == FeishuApiRisk.WRITE
    }

    for tool_name, capability in FEISHU_API_CAPABILITIES.items():
        request = ToolRequest(tool_name=tool_name, question="测试", normalized_command="测试", params={})
        try:
            if capability.risk == FeishuApiRisk.WRITE:
                execute_feishu_api_write_tool(None, request)
            else:
                execute_feishu_api_read_tool(request)
        except NotImplementedError as exc:
            raise AssertionError(f"missing Feishu API runtime binding: {tool_name}") from exc
        except Exception:
            pass


def test_all_feishu_write_capabilities_have_mcp_cli_executors() -> None:
    write_tools = {
        tool_name
        for tool_name, capability in FEISHU_API_CAPABILITIES.items()
        if capability.risk == FeishuApiRisk.WRITE
    }

    assert write_tools
    assert write_tools <= feishu_mcp_realtime_tool_names()


def test_all_feishu_read_capabilities_have_mcp_cli_bindings() -> None:
    read_tools = {
        tool_name
        for tool_name, capability in FEISHU_API_CAPABILITIES.items()
        if capability.risk == FeishuApiRisk.READ
    }

    assert read_tools
    assert read_tools <= feishu_mcp_bound_tool_names()


def test_calendar_qa_realtime_read_is_bound_to_mcp_cli() -> None:
    assert "bitable_qa" in feishu_mcp_bound_tool_names()
    assert "calendar_qa" in feishu_mcp_bound_tool_names()
    assert "feishu_approval_instance_get" in feishu_mcp_bound_tool_names()
    assert "feishu_approval_task_query" in feishu_mcp_bound_tool_names()
    assert "feishu_bitable_field_list" in feishu_mcp_bound_tool_names()
    assert "feishu_bitable_view_get_card" in feishu_mcp_bound_tool_names()
    assert "feishu_bitable_view_get_timebar" in feishu_mcp_bound_tool_names()
    assert "feishu_bitable_view_get_visible_fields" in feishu_mcp_bound_tool_names()
    assert "feishu_contact_department_children" in feishu_mcp_bound_tool_names()
    assert "feishu_contact_department_users" in feishu_mcp_bound_tool_names()
    assert "feishu_contact_organization_snapshot" in feishu_mcp_bound_tool_names()
    assert "feishu_contact_scope_list" in feishu_mcp_bound_tool_names()
    assert "feishu_okr_cycle_list" in feishu_mcp_bound_tool_names()
    assert "feishu_okr_objective_list" in feishu_mcp_bound_tool_names()
    assert "mail_qa" in feishu_mcp_bound_tool_names()
    assert "task_qa" in feishu_mcp_bound_tool_names()


def test_all_feishu_confirmed_realtime_writes_delegate_to_mcp_without_client(monkeypatch) -> None:
    write_tools = sorted(
        tool_name
        for tool_name, capability in FEISHU_API_CAPABILITIES.items()
        if capability.risk == FeishuApiRisk.WRITE
    )
    calls: list[str] = []

    def fake_mcp_realtime(context, request):
        calls.append(request.tool_name)
        assert request.params.get("client") is None
        assert request.params.get("confirmed") is True
        return f"mcp:{request.tool_name}"

    monkeypatch.setattr("app.services.tools.providers.feishu_mcp.execute_feishu_mcp_realtime_tool", fake_mcp_realtime)
    context = ToolContext(
        db=None,
        company_id=uuid4(),
        actor=BotActor(role="owner", access_scope="company", open_id="ou_owner"),
    )

    assert write_tools
    for tool_name in write_tools:
        request = _tool_request(
            tool_name,
            _context=context,
            confirmed=True,
            app_token="app_1",
            table_id="tbl_1",
            task_id="task_1",
            task_guid="task_1",
            instance_code="instance_1",
            record_id="rec_1",
            summary="跟进客户",
        )
        request.params["confirmation_token"] = feishu_write_confirmation_token(context, request)

        assert execute_feishu_mcp_tool(context, request) == f"mcp:{tool_name}"

    assert calls == write_tools


def test_feishu_api_provider_rejects_unregistered_tools() -> None:
    try:
        execute_feishu_api_tool(None, type("Request", (), {"tool_name": "unknown"})())
    except ValueError as exc:
        assert "Unsupported Feishu API tool" in str(exc)
    else:
        raise AssertionError("expected unsupported tool to raise")


def test_feishu_api_provider_write_tools_require_dry_run_or_confirmation() -> None:
    request = type(
        "Request",
        (),
        {"tool_name": "feishu_approval_task_approve", "params": {"api_entrypoint": "controlled_validation"}},
    )()
    try:
        execute_feishu_api_tool(None, request)
    except PermissionError as exc:
        assert "requires dry_run or confirmed=true with confirmation_token" in str(exc)
    else:
        raise AssertionError("expected unconfirmed write tool to raise")


def test_all_feishu_api_write_tools_require_dry_run_or_confirmation() -> None:
    write_tools = [
        name
        for name, capability in FEISHU_API_CAPABILITIES.items()
        if capability.risk == FeishuApiRisk.WRITE
    ]

    assert write_tools
    for tool_name in write_tools:
        request = type("Request", (), {"tool_name": tool_name, "params": {"api_entrypoint": "controlled_validation"}})()
        try:
            execute_feishu_api_tool(None, request)
        except PermissionError as exc:
            assert f"requires dry_run or confirmed=true with confirmation_token: {tool_name}" in str(exc)
        else:
            raise AssertionError(f"expected unconfirmed write tool to raise: {tool_name}")


def test_all_feishu_api_write_tools_support_dry_run_without_runtime_execution() -> None:
    write_tools = [
        name
        for name, capability in FEISHU_API_CAPABILITIES.items()
        if capability.risk == FeishuApiRisk.WRITE
    ]

    assert write_tools
    for tool_name in write_tools:
        request = type(
            "Request",
            (),
            {"tool_name": tool_name, "params": {"dry_run": True, "api_entrypoint": "controlled_validation"}},
        )()

        answer = execute_feishu_api_tool(None, request)

        assert "Dry-run only" in answer
        assert tool_name in answer


def test_feishu_api_provider_write_tools_support_dry_run() -> None:
    request = type(
        "Request",
        (),
        {
            "tool_name": "feishu_approval_task_approve",
            "params": {"dry_run": True, "api_entrypoint": "controlled_validation"},
        },
    )()

    answer = execute_feishu_api_tool(None, request)

    assert "Dry-run only" in answer
    assert "lark-cli approval tasks approve --dry-run" in answer
    assert "实时执行优先引擎: lark_cli" in answer
    assert "Tool 已选择执行源: MCP -> CLI -> Feishu" in answer
    assert "confirmation_token:" in answer


def test_feishu_api_provider_dry_run_includes_write_target_summary_without_token() -> None:
    request = _tool_request(
        "feishu_bitable_record_update",
        dry_run=True,
        app_token="app_1",
        table_id="tbl_1",
        record_id="rec_1",
        fields={"客户名称": "A 公司", "状态": "跟进中"},
        confirmation_token="must-not-leak",
    )

    answer = execute_feishu_api_tool(None, request)

    assert "写目标: bitable_record.update / app_token=app_1 / record_id=rec_1 / table_id=tbl_1" in answer
    assert "fields=客户名称,状态" in answer
    assert "must-not-leak" not in answer


def test_feishu_api_provider_chat_create_dry_run_includes_chat_name_without_token() -> None:
    request = _tool_request(
        "feishu_im_create_chat",
        dry_run=True,
        name="经营例会群",
        user_id_list=["ou_owner"],
        confirmation_token="must-not-leak",
    )

    answer = execute_feishu_api_tool(None, request)

    assert "写目标: im.create_chat / name=经营例会群" in answer
    assert "must-not-leak" not in answer


def test_feishu_api_provider_task_create_dry_run_includes_task_title_without_token() -> None:
    request = _tool_request(
        "feishu_task_create",
        dry_run=True,
        summary="跟进客户",
        confirmation_token="must-not-leak",
    )

    answer = execute_feishu_api_tool(None, request)

    assert "写目标: task.create / title=跟进客户" in answer
    assert "must-not-leak" not in answer


def test_feishu_api_provider_rejects_mismatched_confirmation_token() -> None:
    request = _tool_request(
        "feishu_task_create",
        confirmed=True,
        confirmation_token="wrong-token",
        summary="跟进客户",
    )

    try:
        execute_feishu_api_tool(None, request)
    except PermissionError as exc:
        assert "confirmation_token mismatch: feishu_task_create" in str(exc)
    else:
        raise AssertionError("expected mismatched confirmation token to raise")


def test_feishu_api_provider_rejects_confirmed_realtime_write_even_with_valid_token() -> None:
    request = _tool_request(
        "feishu_task_create",
        confirmed=True,
        summary="跟进客户",
    )
    request = _tool_request(
        "feishu_task_create",
        confirmed=True,
        confirmation_token=feishu_write_confirmation_token(None, request),
        summary="跟进客户",
    )

    try:
        execute_feishu_api_tool(None, request)
    except PermissionError as exc:
        assert "Feishu API provider is not allowed to execute realtime writes" in str(exc)
        assert "Tool Router -> MCP -> CLI" in str(exc)
    else:
        raise AssertionError("expected Feishu API provider confirmed write to be rejected")


def test_feishu_api_runtime_confirmed_approval_approve_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_approval_task_approve",
        confirmed=True,
        client=client,
        open_id="ou_owner",
        user_access_token="u-token",
        approval_code="approval_1",
        instance_code="instance_1",
        task_id="task_1",
        comment="同意",
        form=[{"id": "field_1", "type": "input", "value": "ok"}],
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书审批任务已提交同意。"
    assert client.user_post_calls == [
        (
            "/open-apis/approval/v4/tasks/pass?user_id_type=open_id",
            "u-token",
            {
                "instance_code": "instance_1",
                "task_id": "task_1",
                "comment": "同意",
                "form": '[{"id": "field_1", "type": "input", "value": "ok"}]',
            },
        )
    ]


def test_feishu_api_runtime_confirmed_approval_reject_uses_context_actor() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    context = ToolContext(
        db=None,
        company_id=uuid4(),
        actor=BotActor(role="owner", access_scope="company", open_id="ou_owner"),
    )
    request = _tool_request(
        "feishu_approval_task_reject",
        _context=context,
        confirmed=True,
        client=client,
        user_access_token="u-token",
        item={"approval_code": "approval_1", "instance_code": "instance_1", "task_id": "task_1"},
        comment="资料不完整",
    )

    answer = execute_feishu_api_write_tool(context, request)

    assert answer == "飞书审批任务已提交拒绝。"
    assert client.user_post_calls == [
        (
            "/open-apis/approval/v4/tasks/refuse?user_id_type=open_id",
            "u-token",
            {
                "instance_code": "instance_1",
                "task_id": "task_1",
                "comment": "资料不完整",
            },
        )
    ]


def test_feishu_api_runtime_confirmed_approval_transfer_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_approval_task_transfer",
        confirmed=True,
        client=client,
        open_id="ou_owner",
        user_access_token="u-token",
        item={"approval_code": "approval_1", "instance_code": "instance_1", "task_id": "task_1"},
        transfer_user_id="ou_target",
        comment="请协助处理",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书审批任务已转交。"
    assert client.user_post_calls == [
        (
            "/open-apis/approval/v4/tasks/forward?user_id_type=open_id",
            "u-token",
            {
                "instance_code": "instance_1",
                "task_id": "task_1",
                "transfer_user_id": "ou_target",
                "comment": "请协助处理",
            },
        )
    ]


def test_feishu_api_runtime_confirmed_approval_remind_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_approval_instance_remind",
        confirmed=True,
        client=client,
        open_id="ou_owner",
        user_access_token="u-token",
        instance_code="instance_1",
        task_ids=["task_1"],
        comment="请尽快处理",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书审批催办已发送。"
    assert client.user_post_calls == [
        (
            "/open-apis/approval/v4/instances/remind",
            "u-token",
            {
                "instance_code": "instance_1",
                "task_ids": ["task_1"],
                "comment": "请尽快处理",
            },
        )
    ]


def test_feishu_api_runtime_confirmed_approval_cancel_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_approval_instance_cancel",
        confirmed=True,
        client=client,
        open_id="ou_owner",
        user_access_token="u-token",
        instance_code="instance_1",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书审批实例已撤回。"
    assert client.user_post_calls == [
        (
            "/open-apis/approval/v4/instances/recall",
            "u-token",
            {"instance_code": "instance_1"},
        )
    ]


def test_feishu_api_runtime_confirmed_approval_cc_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_approval_instance_cc",
        confirmed=True,
        client=client,
        open_id="ou_owner",
        user_access_token="u-token",
        instance_code="instance_1",
        cc_user_ids=["ou_target"],
        comment="请关注",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书审批实例已抄送。"
    assert client.user_post_calls == [
        (
            "/open-apis/approval/v4/instances/add_cc?user_id_type=open_id",
            "u-token",
            {
                "instance_code": "instance_1",
                "cc_user_ids": ["ou_target"],
                "comment": "请关注",
            },
        )
    ]


def test_feishu_api_runtime_confirmed_approval_add_sign_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_approval_task_add_sign",
        confirmed=True,
        client=client,
        open_id="ou_owner",
        user_access_token="u-token",
        instance_code="instance_1",
        task_id="task_1",
        add_sign_user_ids=["ou_target"],
        add_sign_type=3,
        comment="请协同审批",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书审批任务已加签。"
    assert client.user_post_calls == [
        (
            "/open-apis/approval/v4/tasks/add_sign?user_id_type=open_id",
            "u-token",
            {
                "instance_code": "instance_1",
                "task_id": "task_1",
                "add_sign_user_ids": ["ou_target"],
                "add_sign_type": 3,
                "comment": "请协同审批",
            },
        )
    ]


def test_feishu_api_runtime_confirmed_approval_rollback_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_approval_task_rollback",
        confirmed=True,
        client=client,
        open_id="ou_owner",
        user_access_token="u-token",
        instance_code="instance_1",
        task_id="task_1",
        node_ids=["node_1"],
        comment="退回修改",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书审批任务已退回。"
    assert client.user_post_calls == [
        (
            "/open-apis/approval/v4/tasks/rollback",
            "u-token",
            {
                "instance_code": "instance_1",
                "task_id": "task_1",
                "node_ids": ["node_1"],
                "comment": "退回修改",
            },
        )
    ]


def test_feishu_api_runtime_confirmed_approval_uses_bound_feishu_user_token() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    company_id = uuid4()
    app_config = SimpleNamespace(id=uuid4(), company_id=company_id)
    account = SimpleNamespace(
        settings={"open_id": "ou_owner", "feishu_app_config_id": str(app_config.id)},
        credentials={
            "access_token": "u-token-from-account",
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )

    class FakeScalarResult:
        def all(self):
            return [account]

    class FakeDb:
        def scalars(self, query):
            return FakeScalarResult()

    context = ToolContext(
        db=FakeDb(),
        company_id=company_id,
        actor=BotActor(role="owner", access_scope="company", open_id="ou_owner"),
    )
    request = _tool_request(
        "feishu_approval_task_approve",
        _context=context,
        confirmed=True,
        app_config=app_config,
        client=client,
        instance_code="instance_1",
        task_id="task_1",
    )

    answer = execute_feishu_api_write_tool(context, request)

    assert answer == "飞书审批任务已提交同意。"
    assert client.user_post_calls[0][1] == "u-token-from-account"


def test_feishu_api_runtime_confirmed_approval_requires_user_access_token() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_approval_task_approve",
        confirmed=True,
        client=client,
        open_id="ou_owner",
        instance_code="instance_1",
        task_id="task_1",
    )

    try:
        execute_feishu_api_write_tool(None, request)
    except RuntimeError as exc:
        assert "用户级能力包授权" in str(exc)
    else:
        raise AssertionError("expected missing user token to block approval write")
    assert client.user_post_calls == []


def test_feishu_api_provider_confirmed_approval_approve_prefers_cli_yes(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_approval_task_approve",
        confirmed=True,
        instance_code="instance_1",
        task_id="task_1",
        comment="同意",
        form=[{"id": "field_1", "type": "input", "value": "ok"}],
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书审批任务已通过 CLI 提交同意。"
    assert captured["args"][:8] == [
        "lark-cli",
        "approval",
        "tasks",
        "approve",
        "--as",
        "user",
        "--format",
        "json",
    ]
    assert captured["args"][8] == "--data"
    assert json.loads(captured["args"][9]) == {
        "instance_code": "instance_1",
        "task_id": "task_1",
        "comment": "同意",
        "form": '[{"id":"field_1","type":"input","value":"ok"}]',
    }
    assert captured["args"][10:] == ["--yes"]


def test_feishu_api_provider_confirmed_approval_transfer_prefers_cli_with_params(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_approval_task_transfer",
        confirmed=True,
        item={"instance_code": "instance_1", "task_id": "task_1"},
        transfer_user_id="ou_target",
        comment="请协助处理",
        user_id_type="open_id",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书审批任务已通过 CLI 转交。"
    assert captured["args"][:9] == [
        "lark-cli",
        "approval",
        "tasks",
        "transfer",
        "--as",
        "user",
        "--format",
        "json",
        "--data",
    ]
    assert json.loads(captured["args"][9]) == {
        "instance_code": "instance_1",
        "task_id": "task_1",
        "transfer_user_id": "ou_target",
        "comment": "请协助处理",
    }
    assert captured["args"][10:] == ["--yes", "--params", '{"user_id_type":"open_id"}']


def test_feishu_api_provider_confirmed_approval_remind_prefers_cli_task_ids(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_approval_instance_remind",
        confirmed=True,
        instance_code="instance_1",
        task_ids=["task_1", "task_2"],
        comment="请尽快处理",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书审批催办已通过 CLI 发送。"
    assert captured["args"][:4] == ["lark-cli", "approval", "tasks", "remind"]
    assert json.loads(captured["args"][9]) == {
        "instance_code": "instance_1",
        "task_ids": ["task_1", "task_2"],
        "comment": "请尽快处理",
    }
    assert "--yes" in captured["args"]


def test_feishu_api_provider_confirmed_approval_add_sign_prefers_cli_and_validates_type(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_approval_task_add_sign",
        confirmed=True,
        instance_code="instance_1",
        task_id="task_1",
        add_sign_user_ids=["ou_target"],
        add_sign_type=3,
        approval_method=2,
        comment="请协同审批",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书审批任务已通过 CLI 加签。"
    assert captured["args"][:4] == ["lark-cli", "approval", "tasks", "add_sign"]
    assert json.loads(captured["args"][9]) == {
        "instance_code": "instance_1",
        "task_id": "task_1",
        "add_sign_user_ids": ["ou_target"],
        "add_sign_type": 3,
        "approval_method": 2,
        "comment": "请协同审批",
    }
    assert "--yes" in captured["args"]


def test_feishu_api_provider_confirmed_approval_rejects_invalid_add_sign_type_before_cli(monkeypatch) -> None:
    calls = []

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_approval_task_add_sign",
        confirmed=True,
        instance_code="instance_1",
        task_id="task_1",
        add_sign_user_ids=["ou_target"],
        add_sign_type=5,
    )

    try:
        execute_feishu_mcp_tool(None, request)
    except ValueError as exc:
        assert "add_sign_type between 1 and 3" in str(exc)
    else:
        raise AssertionError("expected invalid add_sign_type to raise")

    assert calls == []


def test_feishu_api_provider_reads_approval_tasks() -> None:
    client = _FakeFeishuClient(
        {"data": {"tasks": [{"task_id": "task_1", "title": "付款审批", "definition_code": "approval_1"}]}}
    )
    request = _tool_request(
        "feishu_approval_task_query",
        client=client,
        open_id="ou_owner",
        topic="1",
        page_size=8,
        definition_code="approval_1",
    )

    answer = execute_feishu_api_tool(None, request)

    assert answer == "飞书审批任务已读取 1 条：付款审批"
    assert client.calls == [
        (
            "/open-apis/approval/v4/tasks",
            {
                "topic": "1",
                "page_size": 8,
                "user_id_type": "open_id",
                "user_id": "ou_owner",
                "definition_code": "approval_1",
            },
        )
    ]


def test_feishu_api_provider_delegates_approval_task_realtime_read_to_mcp_cli(monkeypatch) -> None:
    calls = []

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(
            {
                "args": args,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "check": check,
            }
        )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"data": {"tasks": [{"task_id": "task_1", "title": "付款审批"}]}}),
            stderr="",
        )

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_approval_task_query",
        open_id="ou_owner",
        topic="1",
        page_size=8,
        definition_code="approval_1",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书待审批任务已通过 CLI 读取 1 条：付款审批"
    assert calls == [
        {
            "args": [
                "lark-cli",
                "approval",
                "tasks",
                "query",
                "--as",
                "user",
                "--format",
                "json",
                "--params",
                '{"topic":"1","page_size":8,"user_id_type":"open_id","user_id":"ou_owner","definition_code":"approval_1"}',
            ],
            "capture_output": True,
            "text": True,
            "timeout": 60,
            "check": False,
        }
    ]


def test_feishu_mcp_approval_task_query_can_return_raw_json(monkeypatch) -> None:
    payload = {"data": {"tasks": [{"task_id": "task_1", "title": "付款审批"}]}}

    def fake_run(args, capture_output, text, timeout, check):
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_approval_task_query",
        open_id="ou_owner",
        topic="1",
        page_size=8,
        response_format="raw_json",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert json.loads(answer) == payload


def test_feishu_mcp_approval_instance_get_can_return_raw_json(monkeypatch) -> None:
    payload = {"data": {"instance_code": "inst_1", "approval_name": "付款审批"}}

    def fake_run(args, capture_output, text, timeout, check):
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_approval_instance_get",
        instance_code="inst_1",
        response_format="raw_json",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert json.loads(answer) == payload


def test_feishu_mcp_realtime_read_tools_can_return_raw_json(monkeypatch) -> None:
    payload = {"data": {"items": [{"id": "item_1", "name": "实时数据"}]}}

    def fake_run(args, capture_output, text, timeout, check):
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    cases = [
        ("calendar_qa", {"calendar_id": "primary"}),
        ("task_qa", {}),
        ("mail_qa", {"user_mailbox_id": "owner@example.com"}),
        ("bitable_qa", {"app_token": "app_1"}),
        ("feishu_contact_department_children", {"department_id": "0"}),
        ("feishu_contact_department_users", {"department_id": "0"}),
        ("feishu_drive_search", {"query": "制度"}),
        ("feishu_doc_fetch", {"document_id": "doc_1"}),
    ]

    for tool_name, params in cases:
        answer = execute_feishu_mcp_tool(None, _tool_request(tool_name, response_format="raw_json", **params))
        assert json.loads(answer) == payload


def test_feishu_mcp_approval_attachment_download_uses_drive_cli(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True}), stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_approval_attachment_download",
        file_token="file_123",
        name="contract.pdf",
    )

    answer = execute_feishu_mcp_tool(None, request)

    payload = json.loads(answer)
    assert payload["downloaded"] is True
    assert payload["name"] == "contract.pdf"
    assert payload["output_path"].endswith(".pdf")
    assert captured["args"][:9] == [
        "lark-cli",
        "drive",
        "+download",
        "--as",
        "user",
        "--format",
        "json",
        "--file-token",
        "file_123",
    ]
    assert "--output" in captured["args"]
    assert "--overwrite" in captured["args"]


def test_feishu_api_provider_reads_approval_instance_detail() -> None:
    client = _FakeFeishuClient({"data": {"definition_name": "付款审批", "instance_code": "instance_1"}})
    request = _tool_request("feishu_approval_instance_get", client=client, instance_code="instance_1", locale="zh-CN")

    answer = execute_feishu_api_tool(None, request)

    assert answer == "飞书审批实例已读取：付款审批"
    assert client.calls == [
        (
            "/open-apis/approval/v4/instances/detail",
            {"instance_code": "instance_1", "user_id_type": "open_id", "locale": "zh-CN"},
        )
    ]


def test_feishu_api_provider_delegates_approval_instance_realtime_read_to_mcp_cli(monkeypatch) -> None:
    calls = []

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(
            {
                "args": args,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "check": check,
            }
        )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"data": {"definition_name": "付款审批", "instance_code": "instance_1"}}),
            stderr="",
        )

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request("feishu_approval_instance_get", instance_code="instance_1", locale="zh-CN")

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书审批实例已通过 CLI 读取：付款审批"
    assert calls == [
        {
            "args": [
                "lark-cli",
                "approval",
                "instances",
                "get",
                "--as",
                "user",
                "--format",
                "json",
                "--params",
                '{"instance_code":"instance_1","user_id_type":"open_id","locale":"zh-CN"}',
            ],
            "capture_output": True,
            "text": True,
            "timeout": 60,
            "check": False,
        }
    ]


def test_feishu_mcp_provider_reads_initiated_approval_instances_via_cli(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(
            returncode=0,
            stdout='{"data":{"instances":[{"instance_code":"instance_1","definition_name":"付款审批"}]}}',
            stderr="",
        )

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_approval_instance_initiated",
        definition_code="approval_1",
        locale="zh-CN",
        page_size=12,
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书已发起审批实例已通过 CLI 读取 1 条：付款审批"
    assert captured == {
        "args": [
            "lark-cli",
            "approval",
            "instances",
            "initiated",
            "--as",
            "user",
            "--format",
            "json",
            "--params",
            '{"page_size":12,"user_id_type":"open_id","definition_code":"approval_1","locale":"zh-CN"}',
        ],
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": False,
    }


def test_feishu_mcp_provider_fetches_doc_via_cli(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(
            returncode=0,
            stdout='{"data":{"document":{"document_id":"doccn_1","content":"# 制度\\n正文"}}}',
            stderr="",
        )

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_doc_fetch",
        doc="doccn_1",
        doc_format="markdown",
        detail="with-ids",
        scope="keyword",
        keyword="制度",
        context_before=1,
        context_after=2,
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书文档已通过 CLI 读取：doccn_1\n# 制度 正文"
    assert captured == {
        "args": [
            "lark-cli",
            "docs",
            "+fetch",
            "--api-version",
            "v2",
            "--as",
            "user",
            "--format",
            "json",
            "--doc",
            "doccn_1",
            "--doc-format",
            "markdown",
            "--detail",
            "with-ids",
            "--scope",
            "keyword",
            "--keyword",
            "制度",
            "--context-before",
            "1",
            "--context-after",
            "2",
        ],
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": False,
    }


def test_feishu_mcp_provider_doc_fetch_rejects_unverified_format_before_cli(monkeypatch) -> None:
    calls = []

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request("feishu_doc_fetch", doc="doccn_1", doc_format="html")

    try:
        execute_feishu_mcp_tool(None, request)
    except ValueError as exc:
        assert "doc_format to be one of" in str(exc)
    else:
        raise AssertionError("expected invalid doc_format to raise")

    assert calls == []


def test_feishu_mcp_provider_searches_drive_via_cli(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(
            returncode=0,
            stdout='{"data":{"docs":[{"title":"员工制度","token":"doccn_1"},{"title":"制度 Wiki","token":"wikcn_1"}]}}',
            stderr="",
        )

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_drive_search",
        query="制度",
        doc_types=["docx", "wiki"],
        page_size=10,
        sort="edit_time",
        edited_since="7d",
        mine=True,
        only_title=True,
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书云空间已通过 CLI 搜索 2 条：员工制度, 制度 Wiki"
    assert captured == {
        "args": [
            "lark-cli",
            "drive",
            "+search",
            "--as",
            "user",
            "--format",
            "json",
            "--query",
            "制度",
            "--page-size",
            "10",
            "--sort",
            "edit_time",
            "--doc-types",
            "docx,wiki",
            "--edited-since",
            "7d",
            "--mine",
            "--only-title",
        ],
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": False,
    }


def test_feishu_mcp_provider_drive_search_rejects_unknown_doc_type_before_cli(monkeypatch) -> None:
    calls = []

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request("feishu_drive_search", query="制度", doc_types=["docx", "unknown"])

    try:
        execute_feishu_mcp_tool(None, request)
    except ValueError as exc:
        assert "doc_types to be one of" in str(exc)
    else:
        raise AssertionError("expected invalid doc_types to raise")

    assert calls == []


def test_feishu_api_runtime_confirmed_calendar_create_event_executes_runtime() -> None:
    client = _FakeFeishuClient(
        [
            {"code": 0, "msg": "success", "data": {"event": {"event_id": "event/1", "summary": "经营例会"}}},
            {"code": 0, "msg": "success", "data": {}},
        ]
    )
    request = _tool_request(
        "feishu_calendar_create_event",
        confirmed=True,
        client=client,
        calendar_id="primary",
        summary="经营例会",
        description="复盘本周重点",
        start="2026-06-13T10:00:00+08:00",
        end="2026-06-13T11:00:00+08:00",
        attendee_ids="ou_owner,oc_team,omm_room",
        rrule="FREQ=WEEKLY;COUNT=2",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书日程已创建：经营例会"
    assert client.post_calls == [
        (
            "/open-apis/calendar/v4/calendars/primary/events",
            {
                "attendee_ability": "can_modify_event",
                "description": "复盘本周重点",
                "end_time": {"timestamp": "1781319600"},
                "free_busy_status": "busy",
                "recurrence": "FREQ=WEEKLY;COUNT=2",
                "reminders": [{"minutes": 5}],
                "start_time": {"timestamp": "1781316000"},
                "summary": "经营例会",
                "vchat": {"vc_type": "vc"},
            },
        ),
        (
            "/open-apis/calendar/v4/calendars/primary/events/event%2F1/attendees?user_id_type=open_id",
            {
                "attendees": [
                    {"type": "user", "user_id": "ou_owner"},
                    {"type": "chat", "chat_id": "oc_team"},
                    {"type": "resource", "room_id": "omm_room"},
                ],
                "need_notification": True,
            },
        ),
    ]


def test_feishu_api_provider_confirmed_calendar_create_event_delegates_to_mcp_without_client(monkeypatch) -> None:
    captured = {}

    def fake_mcp_realtime(context, request):
        captured["context"] = context
        captured["tool_name"] = request.tool_name
        captured["params"] = request.params
        return "飞书日程已通过 MCP 实时桥创建。"

    monkeypatch.setattr("app.services.tools.providers.feishu_mcp.execute_feishu_mcp_realtime_tool", fake_mcp_realtime)
    request = _tool_request(
        "feishu_calendar_create_event",
        confirmed=True,
        calendar_id="primary",
        summary="经营例会",
        start="2026-06-13T10:00:00+08:00",
        end="2026-06-13T11:00:00+08:00",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书日程已通过 MCP 实时桥创建。"
    assert captured["context"] is None
    assert captured["tool_name"] == "feishu_calendar_create_event"
    assert captured["params"]["confirmed"] is True
    assert captured["params"]["summary"] == "经营例会"


def test_feishu_mcp_realtime_bridge_executes_calendar_create_event_with_cli(monkeypatch) -> None:
    captured = []

    def fake_run(args, capture_output, text, timeout, check):
        captured.append(args)
        return SimpleNamespace(
            returncode=0,
            stdout='{"data":{"event":{"event_id":"event/1","summary":"经营例会"}}}',
            stderr="",
        )

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    context = ToolContext(db=None, company_id=uuid4(), actor=BotActor(role="owner", access_scope="company"))
    request = _tool_request(
        "feishu_calendar_create_event",
        confirmed=True,
        calendar_id="primary",
        summary="经营例会",
        description="复盘本周重点",
        start="2026-06-13T10:00:00+08:00",
        end="2026-06-13T11:00:00+08:00",
        attendee_ids=["ou_owner", "oc_team", "omm_room"],
        rrule="FREQ=WEEKLY;UNTIL=20260704T020000Z",
    )

    answer = execute_feishu_mcp_realtime_tool(context, request)

    assert answer == "飞书日程已通过 CLI 创建：经营例会"
    assert captured == [
        [
            "lark-cli",
            "calendar",
            "+create",
            "--as",
            "user",
            "--format",
            "json",
            "--calendar-id",
            "primary",
            "--summary",
            "经营例会",
            "--start",
            "2026-06-13T10:00:00+08:00",
            "--end",
            "2026-06-13T11:00:00+08:00",
            "--description",
            "复盘本周重点",
            "--attendee-ids",
            "ou_owner,oc_team,omm_room",
            "--rrule",
            "FREQ=WEEKLY;UNTIL=20260704T020000Z",
        ]
    ]


def test_feishu_api_provider_confirmed_bitable_record_upsert_prefers_cli_with_field_validation(monkeypatch) -> None:
    captured = []

    def fake_run(args, capture_output, text, timeout, check):
        captured.append(args)
        if args[2] == "+field-list":
            return SimpleNamespace(
                returncode=0,
                stdout='{"data":{"items":[{"field_id":"fld_1","field_name":"状态","type":"text"}]}}',
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout='{"data":{"record":{"record_id":"rec_1"}}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_bitable_record_upsert",
        confirmed=True,
        validate_fields=True,
        app_token="app/token",
        table_id="tbl_1",
        fields={"状态": "跟进中"},
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书多维表格记录已通过 CLI 创建：rec_1"
    assert captured == [
        [
            "lark-cli",
            "base",
            "+field-list",
            "--as",
            "user",
            "--format",
            "json",
            "--base-token",
            "app/token",
            "--table-id",
            "tbl_1",
        ],
        [
            "lark-cli",
            "base",
            "+record-upsert",
            "--as",
            "user",
            "--format",
            "json",
            "--base-token",
            "app/token",
            "--table-id",
            "tbl_1",
            "--json",
            '{"状态":"跟进中"}',
        ],
    ]


def test_feishu_api_provider_confirmed_bitable_record_upload_attachment_prefers_cli(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{"file_tokens":["file_1"]}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_bitable_record_upload_attachment",
        confirmed=True,
        app_token="app/token",
        table_id="tbl_1",
        record_id="rec/1",
        field_id="fld_attachment",
        files=["attachments/customer.pdf", "attachments/photo.png"],
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书多维表格记录附件已通过 CLI 上传：rec/1"
    assert captured == {
        "args": [
            "lark-cli",
            "base",
            "+record-upload-attachment",
            "--as",
            "user",
            "--format",
            "json",
            "--base-token",
            "app/token",
            "--table-id",
            "tbl_1",
            "--record-id",
            "rec/1",
            "--field-id",
            "fld_attachment",
            "--file",
            "attachments/customer.pdf",
            "--file",
            "attachments/photo.png",
        ],
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": False,
    }


def test_feishu_api_provider_confirmed_bitable_record_remove_attachment_prefers_cli_yes(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_bitable_record_remove_attachment",
        confirmed=True,
        app_token="app/token",
        table_id="tbl_1",
        record_id="rec/1",
        field_id="fld_attachment",
        file_tokens=["file_1", "file_2"],
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书多维表格记录附件已通过 CLI 移除：rec/1"
    assert captured == {
        "args": [
            "lark-cli",
            "base",
            "+record-remove-attachment",
            "--as",
            "user",
            "--format",
            "json",
            "--base-token",
            "app/token",
            "--table-id",
            "tbl_1",
            "--record-id",
            "rec/1",
            "--field-id",
            "fld_attachment",
            "--file-token",
            "file_1",
            "--file-token",
            "file_2",
            "--yes",
        ],
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": False,
    }


def test_feishu_api_provider_confirmed_bitable_batch_delete_prefers_cli_yes(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        return SimpleNamespace(returncode=0, stdout='{"data":{}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_bitable_record_batch_delete",
        confirmed=True,
        app_token="app/token",
        table_id="tbl_1",
        record_id_list=["rec_1", "rec_2"],
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书多维表格记录已通过 CLI 批量删除：2 条"
    assert captured["args"] == [
        "lark-cli",
        "base",
        "+record-delete",
        "--as",
        "user",
        "--format",
        "json",
        "--base-token",
        "app/token",
        "--table-id",
        "tbl_1",
        "--record-id",
        "rec_1",
        "--record-id",
        "rec_2",
        "--yes",
    ]


def test_feishu_api_provider_confirmed_bitable_table_delete_prefers_cli_yes(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_bitable_table_delete",
        confirmed=True,
        app_token="app/token",
        table_id="tbl_1",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书多维表格数据表已通过 CLI 删除：tbl_1"
    assert captured == {
        "args": [
            "lark-cli",
            "base",
            "+table-delete",
            "--as",
            "user",
            "--format",
            "json",
            "--base-token",
            "app/token",
            "--table-id",
            "tbl_1",
            "--yes",
        ],
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": False,
    }


def test_feishu_api_provider_confirmed_bitable_field_update_prefers_cli_yes(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        return SimpleNamespace(returncode=0, stdout='{"data":{"field":{"name":"客户状态"}}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_bitable_field_update",
        confirmed=True,
        app_token="app/token",
        table_id="tbl_1",
        field_id="fld_1",
        field={"name": "客户状态", "type": "select", "multiple": False, "options": [{"name": "跟进中"}]},
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书多维表格字段已通过 CLI 更新：客户状态"
    assert captured["args"] == [
        "lark-cli",
        "base",
        "+field-update",
        "--as",
        "user",
        "--format",
        "json",
        "--base-token",
        "app/token",
        "--table-id",
        "tbl_1",
        "--field-id",
        "fld_1",
        "--json",
        '{"name":"客户状态","type":"select","multiple":false,"options":[{"name":"跟进中"}]}',
        "--yes",
    ]


def test_feishu_api_provider_confirmed_bitable_field_delete_prefers_cli_yes(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_bitable_field_delete",
        confirmed=True,
        app_token="app/token",
        table_id="tbl_1",
        field_id="fld/1",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书多维表格字段已通过 CLI 删除：fld/1"
    assert captured == {
        "args": [
            "lark-cli",
            "base",
            "+field-delete",
            "--as",
            "user",
            "--format",
            "json",
            "--base-token",
            "app/token",
            "--table-id",
            "tbl_1",
            "--field-id",
            "fld/1",
            "--yes",
        ],
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": False,
    }


def test_feishu_api_provider_confirmed_bitable_view_delete_prefers_cli_yes(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_bitable_view_delete",
        confirmed=True,
        app_token="app/token",
        table_id="tbl_1",
        view_id="viw/1",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书多维表格视图已通过 CLI 删除：viw/1"
    assert captured == {
        "args": [
            "lark-cli",
            "base",
            "+view-delete",
            "--as",
            "user",
            "--format",
            "json",
            "--base-token",
            "app/token",
            "--table-id",
            "tbl_1",
            "--view-id",
            "viw/1",
            "--yes",
        ],
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": False,
    }


def test_feishu_api_provider_confirmed_bitable_view_set_filter_prefers_cli(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_bitable_view_set_filter",
        confirmed=True,
        app_token="app/token",
        table_id="tbl_1",
        view_id="viw/1",
        filter={"logic": "and", "conditions": [["状态", "==", "跟进中"]]},
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书多维表格视图筛选已通过 CLI 更新：viw/1"
    assert captured == {
        "args": [
            "lark-cli",
            "base",
            "+view-set-filter",
            "--as",
            "user",
            "--format",
            "json",
            "--base-token",
            "app/token",
            "--table-id",
            "tbl_1",
            "--view-id",
            "viw/1",
            "--json",
            '{"logic":"and","conditions":[["状态","==","跟进中"]]}',
        ],
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": False,
    }


def test_feishu_api_provider_confirmed_bitable_view_set_sort_prefers_cli(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_bitable_view_set_sort",
        confirmed=True,
        app_token="app/token",
        table_id="tbl_1",
        view_id="viw/1",
        sort={"sort_config": [{"field": "优先级", "desc": True}]},
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书多维表格视图排序已通过 CLI 更新：viw/1"
    assert captured == {
        "args": [
            "lark-cli",
            "base",
            "+view-set-sort",
            "--as",
            "user",
            "--format",
            "json",
            "--base-token",
            "app/token",
            "--table-id",
            "tbl_1",
            "--view-id",
            "viw/1",
            "--json",
            '{"sort_config":[{"field":"优先级","desc":true}]}',
        ],
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": False,
    }


def test_feishu_api_provider_confirmed_bitable_view_set_group_prefers_cli(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_bitable_view_set_group",
        confirmed=True,
        app_token="app/token",
        table_id="tbl_1",
        view_id="viw/1",
        group={"group_config": [{"field": "状态", "desc": False}]},
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书多维表格视图分组已通过 CLI 更新：viw/1"
    assert captured == {
        "args": [
            "lark-cli",
            "base",
            "+view-set-group",
            "--as",
            "user",
            "--format",
            "json",
            "--base-token",
            "app/token",
            "--table-id",
            "tbl_1",
            "--view-id",
            "viw/1",
            "--json",
            '{"group_config":[{"field":"状态","desc":false}]}',
        ],
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": False,
    }


def test_feishu_api_provider_confirmed_bitable_view_set_visible_fields_prefers_cli(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_bitable_view_set_visible_fields",
        confirmed=True,
        app_token="app/token",
        table_id="tbl_1",
        view_id="viw/1",
        visible_fields=["客户名称", "状态"],
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书多维表格视图可见字段已通过 CLI 更新：viw/1"
    assert captured == {
        "args": [
            "lark-cli",
            "base",
            "+view-set-visible-fields",
            "--as",
            "user",
            "--format",
            "json",
            "--base-token",
            "app/token",
            "--table-id",
            "tbl_1",
            "--view-id",
            "viw/1",
            "--json",
            '{"visible_fields":["客户名称","状态"]}',
        ],
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": False,
    }


def test_feishu_api_provider_confirmed_bitable_view_set_card_prefers_cli(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_bitable_view_set_card",
        confirmed=True,
        app_token="app/token",
        table_id="tbl_1",
        view_id="viw/1",
        card={"cover_field": "fld_cover"},
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书多维表格视图卡片配置已通过 CLI 更新：viw/1"
    assert captured == {
        "args": [
            "lark-cli",
            "base",
            "+view-set-card",
            "--as",
            "user",
            "--format",
            "json",
            "--base-token",
            "app/token",
            "--table-id",
            "tbl_1",
            "--view-id",
            "viw/1",
            "--json",
            '{"cover_field":"fld_cover"}',
        ],
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": False,
    }


def test_feishu_api_provider_confirmed_bitable_view_set_timebar_prefers_cli(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_bitable_view_set_timebar",
        confirmed=True,
        app_token="app/token",
        table_id="tbl_1",
        view_id="viw/1",
        timebar={"start_time": "fld_start", "end_time": "fld_end", "title": "fld_title"},
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书多维表格视图时间轴配置已通过 CLI 更新：viw/1"
    assert captured == {
        "args": [
            "lark-cli",
            "base",
            "+view-set-timebar",
            "--as",
            "user",
            "--format",
            "json",
            "--base-token",
            "app/token",
            "--table-id",
            "tbl_1",
            "--view-id",
            "viw/1",
            "--json",
            '{"start_time":"fld_start","end_time":"fld_end","title":"fld_title"}',
        ],
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": False,
    }


def test_feishu_api_provider_rejects_unsupported_bitable_view_type_before_cli(monkeypatch) -> None:
    calls = []

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_bitable_view_create",
        confirmed=True,
        app_token="app/token",
        table_id="tbl_1",
        view={"name": "表单视图", "type": "form"},
    )

    try:
        execute_feishu_mcp_tool(None, request)
    except ValueError as exc:
        assert "view type must be one of" in str(exc)
    else:
        raise AssertionError("expected unsupported bitable view type to raise")

    assert calls == []


def test_feishu_api_runtime_confirmed_im_send_message_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"message_id": "om_1"}})
    request = _tool_request(
        "feishu_im_send_message",
        confirmed=True,
        client=client,
        chat_id="oc_team",
        text="经营例会已创建",
        idempotency_key="00000000-0000-4000-8000-000000000000",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书消息已发送：om_1"
    assert client.post_calls == [
        (
            "/open-apis/im/v1/messages?receive_id_type=chat_id",
            {
                "content": '{"text":"经营例会已创建"}',
                "msg_type": "text",
                "receive_id": "oc_team",
                "uuid": "00000000-0000-4000-8000-000000000000",
            },
        )
    ]


def test_feishu_api_provider_confirmed_im_send_message_prefers_cli(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{"message_id":"om_cli_1"}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_im_send_message",
        confirmed=True,
        chat_id="oc_team",
        text="经营例会已创建",
        idempotency_key="00000000-0000-4000-8000-000000000000",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书消息已通过 CLI 发送：om_cli_1"
    assert captured == {
        "args": [
            "lark-cli",
            "im",
            "+messages-send",
            "--as",
            "bot",
            "--format",
            "json",
            "--chat-id",
            "oc_team",
            "--text",
            "经营例会已创建",
            "--idempotency-key",
            "00000000-0000-4000-8000-000000000000",
        ],
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": False,
    }


def test_feishu_api_runtime_confirmed_im_create_chat_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"chat_id": "oc_new"}})
    request = _tool_request(
        "feishu_im_create_chat",
        confirmed=True,
        client=client,
        name="经营例会群",
        description="dry-run 验证后创建",
        user_id_list=["ou_owner"],
        bot_id_list=["cli_bot"],
        chat_mode="group",
        chat_type="private",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书群已创建：oc_new"
    assert client.post_calls == [
        (
            "/open-apis/im/v1/chats?user_id_type=open_id",
            {
                "bot_id_list": ["cli_bot"],
                "chat_mode": "group",
                "chat_type": "private",
                "description": "dry-run 验证后创建",
                "name": "经营例会群",
                "user_id_list": ["ou_owner"],
            },
        )
    ]


def test_feishu_api_provider_confirmed_im_create_chat_prefers_cli_without_client(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{"chat_id":"oc_new"}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_im_create_chat",
        confirmed=True,
        name="经营例会群",
        description="dry-run 验证后创建",
        user_id_list=["ou_owner"],
        bot_id_list=["cli_bot"],
        chat_mode="group",
        chat_type="private",
        **{"as": "bot"},
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书群已通过 CLI 创建：oc_new"
    assert captured == {
        "args": [
            "lark-cli",
            "im",
            "+chat-create",
            "--as",
            "bot",
            "--format",
            "json",
            "--name",
            "经营例会群",
            "--description",
            "dry-run 验证后创建",
            "--users",
            "ou_owner",
            "--bots",
            "cli_bot",
            "--type",
            "private",
            "--chat-mode",
            "group",
        ],
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": False,
    }


def test_feishu_api_runtime_confirmed_im_auto_join_public_chats_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_im_auto_join_public_chats",
        confirmed=True,
        client=client,
        chat_ids=["oc_team"],
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书公开群加入已执行：成功 1 个。"
    assert client.post_calls == [("/open-apis/im/v1/chats/oc_team/members/me_join", {})]


def test_feishu_api_provider_confirmed_im_auto_join_public_chats_prefers_cli_without_client(monkeypatch) -> None:
    captured = []

    def fake_run(args, capture_output, text, timeout, check):
        captured.append(args)
        return SimpleNamespace(returncode=0, stdout='{"code":0,"data":{}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_im_auto_join_public_chats",
        confirmed=True,
        chat_ids=["oc_team", "oc_ops"],
        **{"as": "user"},
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书公开群加入已通过 CLI 执行：成功 2 个。"
    assert captured == [
        [
            "lark-cli",
            "api",
            "POST",
            "/open-apis/im/v1/chats/:chat_id/members/me_join",
            "--as",
            "user",
            "--format",
            "json",
            "--params",
            '{"chat_id":"oc_team"}',
            "--data",
            "{}",
        ],
        [
            "lark-cli",
            "api",
            "POST",
            "/open-apis/im/v1/chats/:chat_id/members/me_join",
            "--as",
            "user",
            "--format",
            "json",
            "--params",
            '{"chat_id":"oc_ops"}',
            "--data",
            "{}",
        ],
    ]


def test_feishu_api_runtime_calendar_create_event_rolls_back_when_attendee_add_fails() -> None:
    client = _FakeFeishuClient(
        [{"code": 0, "msg": "success", "data": {"event": {"event_id": "event_1", "summary": "经营例会"}}}],
        post_error_at=2,
    )
    request = _tool_request(
        "feishu_calendar_create_event",
        confirmed=True,
        client=client,
        summary="经营例会",
        start="2026-06-13T10:00:00+08:00",
        end="2026-06-13T11:00:00+08:00",
        attendee_ids=["ou_owner"],
    )

    try:
        execute_feishu_api_write_tool(None, request)
    except RuntimeError as exc:
        assert "post failed" in str(exc)
    else:
        raise AssertionError("expected attendee failure to raise")
    assert client.delete_calls == [("/open-apis/calendar/v4/calendars/primary/events/event_1", {})]


def test_feishu_api_runtime_confirmed_task_create_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"task": {"summary": "跟进客户"}}})
    request = _tool_request(
        "feishu_task_create",
        confirmed=True,
        client=client,
        summary="跟进客户",
        description="确认合同推进状态",
        assignees=["ou_owner", "ou_sales"],
        follower="ou_finance",
        due={"timestamp": "1781000000000", "is_all_day": True},
        idempotency_key="00000000-0000-4000-8000-000000000000",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书任务已创建：跟进客户"
    assert client.post_calls == [
        (
            "/open-apis/task/v2/tasks?user_id_type=open_id",
            {
                "summary": "跟进客户",
                "description": "确认合同推进状态",
                "due": {"timestamp": "1781000000000", "is_all_day": True},
                "members": [
                    {"id": "ou_owner", "type": "user", "role": "assignee"},
                    {"id": "ou_sales", "type": "user", "role": "assignee"},
                    {"id": "ou_finance", "type": "user", "role": "follower"},
                ],
                "client_token": "00000000-0000-4000-8000-000000000000",
            },
        )
    ]


def test_feishu_api_provider_confirmed_task_create_prefers_cli(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{"task":{"summary":"跟进客户"}}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_task_create",
        confirmed=True,
        summary="跟进客户",
        description="确认合同推进状态",
        assignees=["ou_owner", "ou_sales"],
        follower="ou_finance",
        due={"timestamp": "1781000000000", "is_all_day": True},
        idempotency_key="00000000-0000-4000-8000-000000000000",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书任务已通过 CLI 创建：跟进客户"
    assert captured["args"][:8] == [
        "lark-cli",
        "task",
        "+create",
        "--as",
        "user",
        "--format",
        "json",
        "--data",
    ]
    assert captured["args"][9:] == [
        "--idempotency-key",
        "00000000-0000-4000-8000-000000000000",
    ]
    assert json.loads(captured["args"][8]) == {
        "summary": "跟进客户",
        "description": "确认合同推进状态",
        "due": {"timestamp": "1781000000000", "is_all_day": True},
        "members": [
            {"id": "ou_owner", "type": "user", "role": "assignee"},
            {"id": "ou_sales", "type": "user", "role": "assignee"},
            {"id": "ou_finance", "type": "user", "role": "follower"},
        ],
    }
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["timeout"] == 60
    assert captured["check"] is False


def test_feishu_api_runtime_confirmed_task_subtask_create_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"subtask": {"summary": "准备报价单"}}})
    request = _tool_request(
        "feishu_task_subtask_create",
        confirmed=True,
        client=client,
        parent_task_guid="parent/1",
        summary="准备报价单",
        description="拆解客户跟进事项",
        assignees=["ou_owner"],
        tasklists=[{"tasklist_guid": "tl_1"}],
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书子任务已创建：准备报价单"
    assert client.post_calls == [
        (
            "/open-apis/task/v2/tasks/parent%2F1/subtasks?user_id_type=open_id",
            {
                "summary": "准备报价单",
                "description": "拆解客户跟进事项",
                "members": [{"id": "ou_owner", "type": "user", "role": "assignee"}],
                "tasklists": [{"tasklist_guid": "tl_1"}],
            },
        )
    ]


def test_feishu_api_runtime_confirmed_task_complete_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"task": {"summary": "跟进客户"}}})
    request = _tool_request(
        "feishu_task_complete",
        confirmed=True,
        client=client,
        task_guid="task/1",
        completed_at="1781281267000",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书任务已完成：跟进客户"
    assert client.patch_calls == [
        (
            "/open-apis/task/v2/tasks/task%2F1?user_id_type=open_id",
            {"task": {"completed_at": "1781281267000"}, "update_fields": ["completed_at"]},
        )
    ]


def test_feishu_api_provider_confirmed_task_complete_prefers_cli(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{"task":{"summary":"跟进客户"}}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request("feishu_task_complete", confirmed=True, task_guid="task/1")

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书任务已通过 CLI 完成：跟进客户"
    assert captured == {
        "args": [
            "lark-cli",
            "task",
            "+complete",
            "--as",
            "user",
            "--format",
            "json",
            "--task-id",
            "task/1",
        ],
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": False,
    }


def test_feishu_api_runtime_confirmed_task_reopen_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"task": {"summary": "跟进客户"}}})
    request = _tool_request(
        "feishu_task_reopen",
        confirmed=True,
        client=client,
        task_guid="task/1",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书任务已重新打开：跟进客户"
    assert client.patch_calls == [
        (
            "/open-apis/task/v2/tasks/task%2F1?user_id_type=open_id",
            {"task": {"completed_at": "0"}, "update_fields": ["completed_at"]},
        )
    ]


def test_feishu_api_runtime_confirmed_task_update_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"task": {"summary": "新标题"}}})
    request = _tool_request(
        "feishu_task_update",
        confirmed=True,
        client=client,
        task_guid="task/1",
        summary="新标题",
        description="新描述",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书任务已更新：新标题"
    assert client.patch_calls == [
        (
            "/open-apis/task/v2/tasks/task%2F1?user_id_type=open_id",
            {"task": {"summary": "新标题", "description": "新描述"}, "update_fields": ["summary", "description"]},
        )
    ]


def test_feishu_api_runtime_confirmed_task_delete_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_task_delete",
        confirmed=True,
        client=client,
        task_guid="task/1",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书任务已删除：task/1"
    assert client.delete_calls == [("/open-apis/task/v2/tasks/task%2F1", {})]


def test_feishu_api_provider_confirmed_task_delete_prefers_cli(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request("feishu_task_delete", confirmed=True, task_guid="task/1")

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书任务已通过 CLI 删除：task/1"
    assert captured == {
        "args": [
            "lark-cli",
            "task",
            "tasks",
            "delete",
            "--as",
            "user",
            "--format",
            "json",
            "--params",
            '{"task_guid":"task/1"}',
            "--yes",
        ],
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": False,
    }


def test_feishu_api_provider_confirmed_task_update_prefers_cli(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{"task":{"summary":"新标题"}}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_task_update",
        confirmed=True,
        task_guid="task/1",
        summary="新标题",
        description="新描述",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书任务已通过 CLI 更新：新标题"
    assert captured["args"][:10] == [
        "lark-cli",
        "task",
        "+update",
        "--as",
        "user",
        "--format",
        "json",
        "--task-id",
        "task/1",
        "--data",
    ]
    assert json.loads(captured["args"][10]) == {"summary": "新标题", "description": "新描述"}
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["timeout"] == 60
    assert captured["check"] is False


def test_feishu_api_runtime_confirmed_task_update_reminders_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"task": {"summary": "跟进客户"}}})
    request = _tool_request(
        "feishu_task_update_reminders",
        confirmed=True,
        client=client,
        task_guid="task/1",
        relative_fire_minutes=[15],
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书任务提醒已更新：跟进客户"
    assert client.patch_calls == [
        (
            "/open-apis/task/v2/tasks/task%2F1?user_id_type=open_id",
            {
                "task": {"positive_reminders": [{"relative_fire_minute": 15}]},
                "update_fields": ["positive_reminders"],
            },
        )
    ]


def test_feishu_api_runtime_confirmed_task_update_reminders_can_clear_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"task": {"summary": "跟进客户"}}})
    request = _tool_request(
        "feishu_task_update_reminders",
        confirmed=True,
        client=client,
        task_guid="task/1",
        positive_reminders=[],
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书任务提醒已清空：跟进客户"
    assert client.patch_calls == [
        (
            "/open-apis/task/v2/tasks/task%2F1?user_id_type=open_id",
            {"task": {"positive_reminders": []}, "update_fields": ["positive_reminders"]},
        )
    ]


def test_feishu_api_provider_confirmed_task_update_reminders_prefers_mcp_cli(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_task_update_reminders",
        confirmed=True,
        task_guid="task/1",
        relative_fire_minutes=[15, 30],
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书任务提醒已通过 CLI 更新：task/1"
    assert captured == {
        "args": [
            "lark-cli",
            "task",
            "+reminder",
            "--as",
            "user",
            "--format",
            "json",
            "--task-id",
            "task/1",
            "--set",
            "15m,30m",
        ],
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": False,
    }


def test_feishu_api_provider_confirmed_task_clear_reminders_prefers_mcp_cli(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_task_update_reminders",
        confirmed=True,
        task_guid="task/1",
        positive_reminders=[],
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书任务提醒已通过 CLI 清空：task/1"
    assert captured["args"] == [
        "lark-cli",
        "task",
        "+reminder",
        "--as",
        "user",
        "--format",
        "json",
        "--task-id",
        "task/1",
        "--remove",
    ]


def test_feishu_api_runtime_confirmed_task_assign_members_executes_runtime() -> None:
    client = _FakeFeishuClient([{"code": 0, "msg": "success", "data": {}}, {"code": 0, "msg": "success", "data": {}}])
    request = _tool_request(
        "feishu_task_assign_members",
        confirmed=True,
        client=client,
        task_guid="task/1",
        add_assignees=["ou_1", "ou_2"],
        remove_assignees=["ou_3"],
        idempotency_key="00000000-0000-4000-8000-000000000000",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书任务负责人已更新：新增 2 个，移除 1 个。"
    assert client.post_calls == [
        (
            "/open-apis/task/v2/tasks/task%2F1/add_members?user_id_type=open_id",
            {
                "client_token": "00000000-0000-4000-8000-000000000000",
                "members": [
                    {"id": "ou_1", "role": "assignee", "type": "user"},
                    {"id": "ou_2", "role": "assignee", "type": "user"},
                ],
            },
        ),
        (
            "/open-apis/task/v2/tasks/task%2F1/remove_members?user_id_type=open_id",
            {"members": [{"id": "ou_3", "role": "assignee", "type": "user"}]},
        ),
    ]


def test_feishu_api_provider_confirmed_task_assign_members_prefers_mcp_cli(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_task_assign_members",
        confirmed=True,
        task_guid="task/1",
        add_assignees=["ou_1", "ou_2"],
        remove_assignees=["ou_3"],
        idempotency_key="00000000-0000-4000-8000-000000000000",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书任务负责人已通过 CLI 更新：新增 2 个，移除 1 个。"
    assert captured == {
        "args": [
            "lark-cli",
            "task",
            "+assign",
            "--as",
            "user",
            "--format",
            "json",
            "--task-id",
            "task/1",
            "--add",
            "ou_1,ou_2",
            "--remove",
            "ou_3",
            "--idempotency-key",
            "00000000-0000-4000-8000-000000000000",
        ],
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": False,
    }


def test_feishu_api_runtime_confirmed_task_update_followers_executes_runtime() -> None:
    client = _FakeFeishuClient([{"code": 0, "msg": "success", "data": {}}, {"code": 0, "msg": "success", "data": {}}])
    request = _tool_request(
        "feishu_task_update_followers",
        confirmed=True,
        client=client,
        task_guid="task/1",
        add_followers=["ou_1"],
        remove_followers=["ou_2"],
        idempotency_key="00000000-0000-4000-8000-000000000000",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书任务关注人已更新：新增 1 个，移除 1 个。"
    assert client.post_calls == [
        (
            "/open-apis/task/v2/tasks/task%2F1/add_members?user_id_type=open_id",
            {
                "client_token": "00000000-0000-4000-8000-000000000000",
                "members": [{"id": "ou_1", "role": "follower", "type": "user"}],
            },
        ),
        (
            "/open-apis/task/v2/tasks/task%2F1/remove_members?user_id_type=open_id",
            {"members": [{"id": "ou_2", "role": "follower", "type": "user"}]},
        ),
    ]


def test_feishu_api_provider_confirmed_task_update_followers_prefers_mcp_cli(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_task_update_followers",
        confirmed=True,
        task_guid="task/1",
        add_followers=["ou_1"],
        remove_followers=["ou_2"],
        idempotency_key="00000000-0000-4000-8000-000000000000",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书任务关注人已通过 CLI 更新：新增 1 个，移除 1 个。"
    assert captured["args"] == [
        "lark-cli",
        "task",
        "+followers",
        "--as",
        "user",
        "--format",
        "json",
        "--task-id",
        "task/1",
        "--add",
        "ou_1",
        "--remove",
        "ou_2",
        "--idempotency-key",
        "00000000-0000-4000-8000-000000000000",
    ]


def test_feishu_api_runtime_confirmed_task_comment_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"comment": {"comment_id": "cmt_1"}}})
    request = _tool_request(
        "feishu_task_comment",
        confirmed=True,
        client=client,
        task_guid="task_1",
        content="已跟进客户",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书任务评论已添加：cmt_1"
    assert client.post_calls == [
        (
            "/open-apis/task/v2/comments?user_id_type=open_id",
            {"resource_id": "task_1", "resource_type": "task", "content": "已跟进客户"},
        )
    ]


def test_feishu_api_provider_confirmed_task_comment_prefers_cli(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{"comment":{"comment_id":"cmt_1"}}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request("feishu_task_comment", confirmed=True, task_guid="task/1", content="已跟进客户")

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书任务评论已通过 CLI 添加：task/1"
    assert captured == {
        "args": [
            "lark-cli",
            "task",
            "+comment",
            "--as",
            "user",
            "--format",
            "json",
            "--task-id",
            "task/1",
            "--content",
            "已跟进客户",
        ],
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": False,
    }


def test_feishu_api_runtime_confirmed_task_upload_attachment_executes_runtime() -> None:
    client = _FakeFeishuClient(
        {"code": 0, "msg": "success", "data": {"attachment": {"file_name": "客户资料.pdf"}}}
    )
    request = _tool_request(
        "feishu_task_upload_attachment",
        confirmed=True,
        client=client,
        resource_id="task_1",
        resource_type="task",
        file_path="attachments/customer.pdf",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书任务附件已上传：客户资料.pdf"
    assert client.post_calls == [
        (
            "/open-apis/task/v2/attachments/upload?user_id_type=open_id",
            {
                "resource_id": "task_1",
                "resource_type": "task",
                "file": {"path": "attachments/customer.pdf"},
            },
        )
    ]


def test_feishu_api_provider_confirmed_task_upload_attachment_prefers_cli(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{"attachment":{"file_name":"客户资料.pdf"}}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_task_upload_attachment",
        confirmed=True,
        resource_id="task/1",
        file_path="attachments/customer.pdf",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书任务附件已通过 CLI 上传：task/1"
    assert captured == {
        "args": [
            "lark-cli",
            "task",
            "+upload-attachment",
            "--as",
            "user",
            "--format",
            "json",
            "--resource-id",
            "task/1",
            "--resource-type",
            "task",
            "--file",
            "attachments/customer.pdf",
            "--user-id-type",
            "open_id",
        ],
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": False,
    }


def test_feishu_api_runtime_confirmed_tasklist_create_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"tasklist": {"name": "销售跟进"}}})
    request = _tool_request(
        "feishu_tasklist_create",
        confirmed=True,
        client=client,
        name="销售跟进",
        editors=["ou_1", "ou_2"],
        archive_tasklist=True,
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书任务清单已创建：销售跟进"
    assert client.post_calls == [
        (
            "/open-apis/task/v2/tasklists?user_id_type=open_id",
            {
                "name": "销售跟进",
                "archive_tasklist": True,
                "members": [
                    {"id": "ou_1", "type": "user", "role": "editor"},
                    {"id": "ou_2", "type": "user", "role": "editor"},
                ],
            },
        )
    ]


def test_feishu_api_provider_confirmed_tasklist_create_archive_prefers_cli_schema(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{"tasklist":{"name":"归档清单"}}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_tasklist_create",
        confirmed=True,
        name="归档清单",
        editors=["ou_1"],
        archive_tasklist=True,
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书任务清单已通过 CLI 创建：归档清单"
    assert captured == {
        "args": [
            "lark-cli",
            "task",
            "tasklists",
            "create",
            "--as",
            "user",
            "--format",
            "json",
            "--params",
            '{"user_id_type":"open_id"}',
            "--data",
            '{"name":"归档清单","archive_tasklist":true,'
            '"members":[{"id":"ou_1","type":"user","role":"editor"}]}',
        ],
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": False,
    }


def test_feishu_api_provider_confirmed_tasklist_create_archive_rejects_initial_tasks() -> None:
    request = _tool_request(
        "feishu_tasklist_create",
        confirmed=True,
        name="归档清单",
        archive_tasklist=True,
        tasks=[{"summary": "初始化任务"}],
    )

    try:
        execute_feishu_mcp_tool(None, request)
    except ValueError as exc:
        assert "does not support initial tasks" in str(exc)
    else:
        raise AssertionError("expected archive tasklist schema branch to reject initial tasks")


def test_feishu_api_runtime_confirmed_tasklist_update_members_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"tasklist": {"name": "销售跟进"}}})
    request = _tool_request(
        "feishu_tasklist_update_members",
        confirmed=True,
        client=client,
        tasklist_guid="tl/1",
        add_members=["ou_1", "ou_2"],
        remove_members=["ou_3"],
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书任务清单成员已更新：新增 2 个，移除 1 个。"
    assert client.post_calls == [
        (
            "/open-apis/task/v2/tasklists/tl%2F1/add_members?user_id_type=open_id",
            {
                "members": [
                    {"id": "ou_1", "type": "user", "role": "editor"},
                    {"id": "ou_2", "type": "user", "role": "editor"},
                ]
            },
        ),
        (
            "/open-apis/task/v2/tasklists/tl%2F1/remove_members?user_id_type=open_id",
            {"members": [{"id": "ou_3", "type": "user", "role": "editor"}]},
        ),
    ]


def test_feishu_api_runtime_confirmed_tasklist_delete_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_tasklist_delete",
        confirmed=True,
        client=client,
        tasklist_guid="tl/1",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书任务清单已删除：tl/1"
    assert client.delete_calls == [("/open-apis/task/v2/tasklists/tl%2F1", {})]


def test_feishu_api_runtime_confirmed_tasklist_update_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"tasklist": {"name": "销售跟进2026"}}})
    request = _tool_request(
        "feishu_tasklist_update",
        confirmed=True,
        client=client,
        tasklist_guid="tl/1",
        name="销售跟进2026",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书任务清单已更新：销售跟进2026"
    assert client.patch_calls == [
        (
            "/open-apis/task/v2/tasklists/tl%2F1?user_id_type=open_id",
            {"tasklist": {"name": "销售跟进2026"}, "update_fields": ["name"]},
        )
    ]


def test_feishu_api_runtime_confirmed_task_section_create_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"section": {"name": "销售跟进"}}})
    request = _tool_request(
        "feishu_task_section_create",
        confirmed=True,
        client=client,
        name="销售跟进",
        resource_type="tasklist",
        resource_id="tl/1",
        insert_after="sec_0",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书任务分组已创建：销售跟进"
    assert client.post_calls == [
        (
            "/open-apis/task/v2/sections?user_id_type=open_id",
            {"name": "销售跟进", "resource_type": "tasklist", "resource_id": "tl/1", "insert_after": "sec_0"},
        )
    ]


def test_feishu_api_runtime_confirmed_task_section_update_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"section": {"name": "已完成跟进"}}})
    request = _tool_request(
        "feishu_task_section_update",
        confirmed=True,
        client=client,
        section_guid="sec/1",
        name="已完成跟进",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书任务分组已更新：已完成跟进"
    assert client.patch_calls == [
        (
            "/open-apis/task/v2/sections/sec%2F1?user_id_type=open_id",
            {"section": {"name": "已完成跟进"}, "update_fields": ["name"]},
        )
    ]


def test_feishu_api_runtime_confirmed_task_section_delete_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_task_section_delete",
        confirmed=True,
        client=client,
        section_guid="sec/1",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书任务分组已删除：sec/1"
    assert client.delete_calls == [("/open-apis/task/v2/sections/sec%2F1", {})]


def test_feishu_api_runtime_confirmed_tasklist_set_members_executes_runtime() -> None:
    client = _FakeFeishuClient(
        [
            {
                "code": 0,
                "msg": "success",
                "data": {
                    "tasklist": {
                        "members": [
                            {"id": "ou_old", "type": "user", "role": "editor"},
                            {"id": "ou_keep", "type": "user", "role": "editor"},
                        ]
                    }
                },
            },
            {"code": 0, "msg": "success", "data": {}},
            {"code": 0, "msg": "success", "data": {}},
        ]
    )
    request = _tool_request(
        "feishu_tasklist_set_members",
        confirmed=True,
        client=client,
        tasklist_guid="tl/1",
        set_members=["ou_keep", "ou_new"],
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书任务清单成员已全量替换：新增 1 个，移除 1 个。"
    assert client.calls == [
        (
            "/open-apis/task/v2/tasklists/tl%2F1",
            {"user_id_type": "open_id"},
        )
    ]
    assert client.post_calls == [
        (
            "/open-apis/task/v2/tasklists/tl%2F1/add_members?user_id_type=open_id",
            {"members": [{"id": "ou_new", "type": "user", "role": "editor"}]},
        ),
        (
            "/open-apis/task/v2/tasklists/tl%2F1/remove_members?user_id_type=open_id",
            {"members": [{"id": "ou_old", "type": "user", "role": "editor"}]},
        ),
    ]


def test_feishu_api_runtime_confirmed_task_add_to_tasklist_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_task_add_to_tasklist",
        confirmed=True,
        client=client,
        task_guid="task/1",
        tasklist_guid="tl_1",
        section_guid="sec_1",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书任务已加入清单：tl_1"
    assert client.post_calls == [
        (
            "/open-apis/task/v2/tasks/task%2F1/add_tasklist?user_id_type=open_id",
            {"tasklist_guid": "tl_1", "section_guid": "sec_1"},
        )
    ]


def test_feishu_api_runtime_confirmed_task_set_ancestor_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_task_set_ancestor",
        confirmed=True,
        client=client,
        task_guid="task/1",
        ancestor_guid="parent_1",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书任务已设置父任务：parent_1"
    assert client.post_calls == [
        (
            "/open-apis/task/v2/tasks/task%2F1/set_ancestor_task?user_id_type=open_id",
            {"ancestor_guid": "parent_1"},
        )
    ]


def test_feishu_api_runtime_confirmed_task_clear_ancestor_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_task_clear_ancestor",
        confirmed=True,
        client=client,
        task_guid="task/1",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书任务父任务关系已清空。"
    assert client.post_calls == [
        (
            "/open-apis/task/v2/tasks/task%2F1/set_ancestor_task?user_id_type=open_id",
            {},
        )
    ]


def test_feishu_api_provider_confirmed_task_reopen_prefers_cli(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        return SimpleNamespace(returncode=0, stdout='{"data":{"task":{"summary":"跟进客户"}}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request("feishu_task_reopen", confirmed=True, task_guid="task/1")

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书任务已通过 CLI 重新打开：跟进客户"
    assert captured["args"] == [
        "lark-cli",
        "task",
        "+reopen",
        "--as",
        "user",
        "--format",
        "json",
        "--task-id",
        "task/1",
    ]


def test_feishu_api_provider_confirmed_task_subtask_create_prefers_cli_schema_shape(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        return SimpleNamespace(returncode=0, stdout='{"data":{"subtask":{"summary":"准备报价单"}}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_task_subtask_create",
        confirmed=True,
        parent_task_guid="parent/1",
        summary="准备报价单",
        description="拆解客户跟进事项",
        assignees=["ou_owner"],
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书子任务已通过 CLI 创建：准备报价单"
    assert captured["args"][:9] == [
        "lark-cli",
        "task",
        "subtasks",
        "create",
        "--as",
        "user",
        "--format",
        "json",
        "--params",
    ]
    assert json.loads(captured["args"][9]) == {"task_guid": "parent/1", "user_id_type": "open_id"}
    assert captured["args"][10] == "--data"
    assert json.loads(captured["args"][11]) == {
        "summary": "准备报价单",
        "description": "拆解客户跟进事项",
        "members": [{"id": "ou_owner", "type": "user", "role": "assignee"}],
    }


def test_feishu_api_provider_confirmed_tasklist_members_prefers_cli(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        return SimpleNamespace(returncode=0, stdout='{"data":{}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_tasklist_update_members",
        confirmed=True,
        tasklist_guid="tl/1",
        add_members=["ou_1", "ou_2"],
        remove_members=["ou_3"],
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书任务清单成员已通过 CLI 更新：新增 2 个，移除 1 个。"
    assert captured["args"] == [
        "lark-cli",
        "task",
        "+tasklist-members",
        "--as",
        "user",
        "--format",
        "json",
        "--tasklist-id",
        "tl/1",
        "--add",
        "ou_1,ou_2",
        "--remove",
        "ou_3",
    ]


def test_feishu_api_provider_confirmed_tasklist_delete_prefers_cli(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request("feishu_tasklist_delete", confirmed=True, tasklist_guid="tl/1")

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书任务清单已通过 CLI 删除：tl/1"
    assert captured == {
        "args": [
            "lark-cli",
            "task",
            "tasklists",
            "delete",
            "--as",
            "user",
            "--format",
            "json",
            "--params",
            '{"tasklist_guid":"tl/1"}',
            "--yes",
        ],
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": False,
    }


def test_feishu_api_provider_confirmed_tasklist_update_prefers_cli_patch(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{"tasklist":{"name":"销售跟进2026"}}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request("feishu_tasklist_update", confirmed=True, tasklist_guid="tl/1", name="销售跟进2026")

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书任务清单已通过 CLI 更新：销售跟进2026"
    assert captured == {
        "args": [
            "lark-cli",
            "task",
            "tasklists",
            "patch",
            "--as",
            "user",
            "--format",
            "json",
            "--params",
            '{"tasklist_guid":"tl/1","user_id_type":"open_id"}',
            "--data",
            '{"tasklist":{"name":"销售跟进2026"},"update_fields":["name"]}',
        ],
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": False,
    }


def test_feishu_api_provider_confirmed_task_section_create_prefers_cli_schema(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check
        return SimpleNamespace(returncode=0, stdout='{"data":{"section":{"name":"销售跟进"}}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_task_section_create",
        confirmed=True,
        name="销售跟进",
        resource_type="tasklist",
        resource_id="tl/1",
        insert_after="sec_0",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书任务分组已通过 CLI 创建：销售跟进"
    assert captured == {
        "args": [
            "lark-cli",
            "task",
            "sections",
            "create",
            "--as",
            "user",
            "--format",
            "json",
            "--params",
            '{"user_id_type":"open_id"}',
            "--data",
            '{"name":"销售跟进","resource_type":"tasklist","resource_id":"tl/1","insert_after":"sec_0"}',
        ],
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": False,
    }


def test_feishu_api_provider_confirmed_task_section_update_prefers_cli_schema(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        return SimpleNamespace(returncode=0, stdout='{"data":{"section":{"name":"已完成跟进"}}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_task_section_update",
        confirmed=True,
        section_guid="sec/1",
        name="已完成跟进",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书任务分组已通过 CLI 更新：已完成跟进"
    assert captured["args"] == [
        "lark-cli",
        "task",
        "sections",
        "patch",
        "--as",
        "user",
        "--format",
        "json",
        "--params",
        '{"section_guid":"sec/1","user_id_type":"open_id"}',
        "--data",
        '{"section":{"name":"已完成跟进"},"update_fields":["name"]}',
    ]


def test_feishu_api_provider_confirmed_task_section_delete_prefers_cli_schema_with_yes(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        return SimpleNamespace(returncode=0, stdout='{"data":{}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request("feishu_task_section_delete", confirmed=True, section_guid="sec/1")

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书任务分组已通过 CLI 删除：sec/1"
    assert captured["args"] == [
        "lark-cli",
        "task",
        "sections",
        "delete",
        "--as",
        "user",
        "--format",
        "json",
        "--params",
        '{"section_guid":"sec/1"}',
        "--yes",
    ]


def test_feishu_api_provider_confirmed_task_clear_ancestor_prefers_cli_without_ancestor(monkeypatch) -> None:
    captured = {}

    def fake_run(args, capture_output, text, timeout, check):
        captured["args"] = args
        return SimpleNamespace(returncode=0, stdout='{"data":{}}', stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request("feishu_task_clear_ancestor", confirmed=True, task_guid="task/1")

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书任务父任务关系已通过 CLI 清空。"
    assert captured["args"] == [
        "lark-cli",
        "task",
        "+set-ancestor",
        "--as",
        "user",
        "--format",
        "json",
        "--task-id",
        "task/1",
    ]


def test_feishu_api_runtime_confirmed_bitable_record_create_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"record": {"record_id": "rec_1"}}})
    request = _tool_request(
        "feishu_bitable_record_create",
        confirmed=True,
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        fields={"任务名称": "拜访潜在客户", "工时": 10},
        client_token="00000000-0000-4000-8000-000000000000",
        ignore_consistency_check=True,
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书多维表格记录已创建：rec_1"
    assert client.post_calls == [
        (
            "/open-apis/bitable/v1/apps/app%2Ftoken/tables/tbl_1/records?user_id_type=open_id&client_token=00000000-0000-4000-8000-000000000000&ignore_consistency_check=true",
            {"fields": {"任务名称": "拜访潜在客户", "工时": 10}},
        )
    ]


def test_feishu_api_runtime_validates_bitable_record_fields_before_write() -> None:
    client = _FakeFeishuClient(
        [
            {"data": {"items": [{"field_id": "fld_1", "field_name": "任务名称", "type": "text"}]}},
            {"code": 0, "msg": "success", "data": {"record": {"record_id": "rec_1"}}},
        ]
    )
    request = _tool_request(
        "feishu_bitable_record_create",
        confirmed=True,
        client=client,
        validate_fields=True,
        app_token="app/token",
        table_id="tbl_1",
        fields={"任务名称": "拜访潜在客户"},
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书多维表格记录已创建：rec_1"
    assert client.calls == [
        (
            "/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1/fields",
            {"limit": 200, "offset": 0},
        )
    ]
    assert client.post_calls == [
        (
            "/open-apis/bitable/v1/apps/app%2Ftoken/tables/tbl_1/records?user_id_type=open_id",
            {"fields": {"任务名称": "拜访潜在客户"}},
        )
    ]


def test_feishu_api_runtime_rejects_unknown_bitable_write_field() -> None:
    client = _FakeFeishuClient({"data": {"items": [{"field_id": "fld_1", "field_name": "任务名称", "type": "text"}]}})
    request = _tool_request(
        "feishu_bitable_record_update",
        confirmed=True,
        client=client,
        validate_fields=True,
        app_token="app/token",
        table_id="tbl_1",
        record_id="rec_1",
        fields={"不存在字段": "值"},
    )

    try:
        execute_feishu_api_write_tool(None, request)
    except ValueError as exc:
        assert "unknown fields 不存在字段" in str(exc)
    else:
        raise AssertionError("expected unknown bitable field to raise")

    assert client.calls == [
        (
            "/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1/fields",
            {"limit": 200, "offset": 0},
        )
    ]
    assert client.put_calls == []


def test_feishu_api_runtime_rejects_readonly_bitable_write_field() -> None:
    client = _FakeFeishuClient({"data": {"items": [{"field_id": "fld_1", "field_name": "计算结果", "type": "formula"}]}})
    request = _tool_request(
        "feishu_bitable_record_batch_update",
        confirmed=True,
        client=client,
        validate_fields=True,
        app_token="app/token",
        table_id="tbl_1",
        record_id_list=["rec_1"],
        patch={"计算结果": "不可写"},
    )

    try:
        execute_feishu_api_write_tool(None, request)
    except ValueError as exc:
        assert "readonly fields 计算结果" in str(exc)
    else:
        raise AssertionError("expected readonly bitable field to raise")

    assert client.calls == [
        (
            "/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1/fields",
            {"limit": 200, "offset": 0},
        )
    ]
    assert client.post_calls == []


def test_feishu_api_runtime_confirmed_bitable_record_batch_create_executes_runtime() -> None:
    client = _FakeFeishuClient(
        {"code": 0, "msg": "success", "data": {"records": [{"record_id": "rec_1"}, {"record_id": "rec_2"}]}}
    )
    request = _tool_request(
        "feishu_bitable_record_batch_create",
        confirmed=True,
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        fields=["任务名称", "状态"],
        rows=[["拜访", "待处理"], ["跟进", "处理中"]],
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书多维表格记录已批量创建：2 条"
    assert client.post_calls == [
        (
            "/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1/records/batch_create",
            {"fields": ["任务名称", "状态"], "rows": [["拜访", "待处理"], ["跟进", "处理中"]]},
        )
    ]


def test_feishu_api_runtime_confirmed_bitable_record_batch_update_executes_runtime() -> None:
    client = _FakeFeishuClient(
        {"code": 0, "msg": "success", "data": {"records": [{"record_id": "rec_1"}, {"record_id": "rec_2"}]}}
    )
    request = _tool_request(
        "feishu_bitable_record_batch_update",
        confirmed=True,
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        record_id_list=["rec_1", "rec_2"],
        patch={"状态": "已完成"},
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书多维表格记录已批量更新：2 条"
    assert client.post_calls == [
        (
            "/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1/records/batch_update",
            {"record_id_list": ["rec_1", "rec_2"], "patch": {"状态": "已完成"}},
        )
    ]


def test_feishu_api_runtime_confirmed_bitable_record_batch_delete_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_bitable_record_batch_delete",
        confirmed=True,
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        record_id_list=["rec_1", "rec_2"],
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书多维表格记录已批量删除：2 条"
    assert client.post_calls == [
        (
            "/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1/records/batch_delete",
            {"record_id_list": ["rec_1", "rec_2"]},
        )
    ]


def test_feishu_api_runtime_confirmed_bitable_record_upsert_creates_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"record": {"record_id": "rec_1"}}})
    request = _tool_request(
        "feishu_bitable_record_upsert",
        confirmed=True,
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        fields={"客户名称": "星河科技", "状态": "跟进中"},
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书多维表格记录已创建：rec_1"
    assert client.post_calls == [
        (
            "/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1/records",
            {"客户名称": "星河科技", "状态": "跟进中"},
        )
    ]


def test_feishu_api_runtime_confirmed_bitable_record_upsert_updates_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"record": {"record_id": "rec_1"}}})
    request = _tool_request(
        "feishu_bitable_record_upsert",
        confirmed=True,
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        record_id="rec/1",
        fields={"状态": "已成交"},
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书多维表格记录已按 record_id 更新：rec_1"
    assert client.patch_calls == [
        (
            "/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1/records/rec%2F1",
            {"状态": "已成交"},
        )
    ]


def test_feishu_api_runtime_confirmed_bitable_record_upload_attachment_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_bitable_record_upload_attachment",
        confirmed=True,
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        record_id="rec/1",
        field_id="fld_attachment",
        file_tokens=["file_1", "file_2"],
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书多维表格记录附件已追加：rec/1"
    assert client.post_calls == [
        (
            "/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1/append_attachments",
            {
                "attachments": {
                    "rec/1": {
                        "fld_attachment": [
                            {"file_token": "file_1"},
                            {"file_token": "file_2"},
                        ]
                    }
                }
            },
        )
    ]


def test_feishu_api_runtime_confirmed_bitable_record_remove_attachment_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_bitable_record_remove_attachment",
        confirmed=True,
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        record_id="rec/1",
        field_id="fld_attachment",
        file_tokens=["file_1", "file_2"],
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书多维表格记录附件已移除：rec/1"
    assert client.post_calls == [
        (
            "/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1/remove_attachments",
            {
                "attachments": {
                    "rec/1": {
                        "fld_attachment": [
                            {"file_token": "file_1"},
                            {"file_token": "file_2"},
                        ]
                    }
                }
            },
        )
    ]


def test_feishu_api_runtime_confirmed_bitable_record_update_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"record": {"record_id": "rec_1"}}})
    request = _tool_request(
        "feishu_bitable_record_update",
        confirmed=True,
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        record_id="rec/1",
        fields={"任务名称": "更新客户关系"},
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书多维表格记录已更新：rec_1"
    assert client.put_calls == [
        (
            "/open-apis/bitable/v1/apps/app%2Ftoken/tables/tbl_1/records/rec%2F1?user_id_type=open_id",
            {"fields": {"任务名称": "更新客户关系"}},
        )
    ]


def test_feishu_api_runtime_confirmed_bitable_record_delete_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_bitable_record_delete",
        confirmed=True,
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        record_id="rec/1",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书多维表格记录已删除：rec/1"
    assert client.delete_calls == [
        (
            "/open-apis/bitable/v1/apps/app%2Ftoken/tables/tbl_1/records/rec%2F1?user_id_type=open_id",
            {},
        )
    ]


def test_feishu_api_runtime_confirmed_bitable_table_create_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"table": {"table_id": "tbl_new", "name": "客户档案"}}})
    request = _tool_request(
        "feishu_bitable_table_create",
        confirmed=True,
        client=client,
        app_token="app/token",
        name="客户档案",
        fields=[
            {"name": "客户名称", "type": "text"},
            {"name": "状态", "type": "select", "options": [{"name": "跟进中"}, {"name": "已成交"}]},
        ],
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书多维表格数据表已创建：客户档案"
    assert client.post_calls == [
        (
            "/open-apis/base/v3/bases/app%2Ftoken/tables",
            {
                "name": "客户档案",
                "fields": [
                    {"name": "客户名称", "type": "text"},
                    {"name": "状态", "type": "select", "options": [{"name": "跟进中"}, {"name": "已成交"}]},
                ],
            },
        )
    ]


def test_feishu_api_runtime_confirmed_bitable_table_update_executes_runtime() -> None:
    client = _FakeFeishuClient(
        {"code": 0, "msg": "success", "data": {"table": {"table_id": "tbl_1", "name": "客户档案2026"}}}
    )
    request = _tool_request(
        "feishu_bitable_table_update",
        confirmed=True,
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        name="客户档案2026",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书多维表格数据表已重命名：客户档案2026"
    assert client.patch_calls == [
        (
            "/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1",
            {"name": "客户档案2026"},
        )
    ]


def test_feishu_api_runtime_confirmed_bitable_table_delete_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_bitable_table_delete",
        confirmed=True,
        client=client,
        app_token="app/token",
        table_id="tbl_1",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书多维表格数据表已删除：tbl_1"
    assert client.delete_calls == [("/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1", {})]


def test_feishu_api_runtime_confirmed_bitable_field_create_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"field": {"field_id": "fld_1", "name": "状态"}}})
    request = _tool_request(
        "feishu_bitable_field_create",
        confirmed=True,
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        field={"name": "状态", "type": "select", "multiple": False, "options": [{"name": "跟进中"}]},
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书多维表格字段已创建：状态"
    assert client.post_calls == [
        (
            "/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1/fields",
            {"name": "状态", "type": "select", "multiple": False, "options": [{"name": "跟进中"}]},
        )
    ]


def test_feishu_api_runtime_confirmed_bitable_field_delete_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_bitable_field_delete",
        confirmed=True,
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        field_id="fld/1",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书多维表格字段已删除：fld/1"
    assert client.delete_calls == [("/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1/fields/fld%2F1", {})]


def test_feishu_api_runtime_confirmed_bitable_field_update_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"field": {"field_id": "fld_1", "name": "客户状态"}}})
    request = _tool_request(
        "feishu_bitable_field_update",
        confirmed=True,
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        field_id="fld_1",
        field={"name": "客户状态", "type": "select", "multiple": False, "options": [{"name": "跟进中"}]},
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书多维表格字段已更新：客户状态"
    assert client.put_calls == [
        (
            "/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1/fields/fld_1",
            {"name": "客户状态", "type": "select", "multiple": False, "options": [{"name": "跟进中"}]},
        )
    ]


def test_feishu_api_runtime_rejects_bitable_formula_field_update() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_bitable_field_update",
        confirmed=True,
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        field_id="fld_1",
        field={"name": "计算结果", "type": "formula", "property": {"expression": "1+1"}},
    )

    try:
        execute_feishu_api_write_tool(None, request)
    except ValueError as exc:
        assert "formula/lookup field update is not enabled" in str(exc)
    else:
        raise AssertionError("expected formula field update to raise")

    assert client.put_calls == []


def test_feishu_api_runtime_confirmed_bitable_view_create_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"view": {"view_id": "viw_1", "name": "客户跟进视图"}}})
    request = _tool_request(
        "feishu_bitable_view_create",
        confirmed=True,
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        view={"name": "客户跟进视图", "type": "grid"},
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书多维表格视图已创建：客户跟进视图"
    assert client.post_calls == [
        (
            "/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1/views",
            {"name": "客户跟进视图", "type": "grid"},
        )
    ]


def test_feishu_api_runtime_confirmed_bitable_view_delete_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_bitable_view_delete",
        confirmed=True,
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        view_id="viw/1",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书多维表格视图已删除：viw/1"
    assert client.delete_calls == [("/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1/views/viw%2F1", {})]


def test_feishu_api_runtime_confirmed_bitable_view_rename_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"view": {"view_id": "viw_1", "name": "客户跟进视图2026"}}})
    request = _tool_request(
        "feishu_bitable_view_rename",
        confirmed=True,
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        view_id="viw_1",
        name="客户跟进视图2026",
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书多维表格视图已重命名：客户跟进视图2026"
    assert client.patch_calls == [
        (
            "/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1/views/viw_1",
            {"name": "客户跟进视图2026"},
        )
    ]


def test_feishu_api_runtime_confirmed_bitable_view_set_filter_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_bitable_view_set_filter",
        confirmed=True,
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        view_id="viw/1",
        filter={"logic": "AND", "conditions": [["状态", "==", "跟进中"]]},
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书多维表格视图筛选已更新：viw/1"
    assert client.put_calls == [
        (
            "/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1/views/viw%2F1/filter",
            {"logic": "and", "conditions": [["状态", "==", "跟进中"]]},
        )
    ]


def test_feishu_api_runtime_confirmed_bitable_view_set_sort_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_bitable_view_set_sort",
        confirmed=True,
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        view_id="viw/1",
        sort={"sort_config": [{"field": "优先级", "desc": True}]},
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书多维表格视图排序已更新：viw/1"
    assert client.put_calls == [
        (
            "/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1/views/viw%2F1/sort",
            {"sort_config": [{"field": "优先级", "desc": True}]},
        )
    ]


def test_feishu_api_runtime_confirmed_bitable_view_set_group_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_bitable_view_set_group",
        confirmed=True,
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        view_id="viw/1",
        group={"group_config": [{"field": "状态", "desc": False}]},
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书多维表格视图分组已更新：viw/1"
    assert client.put_calls == [
        (
            "/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1/views/viw%2F1/group",
            {"group_config": [{"field": "状态", "desc": False}]},
        )
    ]


def test_feishu_api_provider_bitable_view_get_visible_fields_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"visible_fields": ["客户名称", "状态"]}})
    request = _tool_request(
        "feishu_bitable_view_get_visible_fields",
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        view_id="viw/1",
    )

    answer = execute_feishu_api_tool(None, request)

    assert answer == "飞书多维表格视图可见字段：2 个"
    assert client.calls == [
        (
            "/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1/views/viw%2F1/visible_fields",
            {},
        )
    ]


def test_feishu_api_provider_delegates_bitable_view_visible_fields_realtime_read_to_mcp_cli(monkeypatch) -> None:
    calls = []

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(
            {
                "args": args,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "check": check,
            }
        )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"data": {"visible_fields": ["客户名称", "状态"]}}),
            stderr="",
        )

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_bitable_view_get_visible_fields",
        app_token="app/token",
        table_id="tbl_1",
        view_id="viw/1",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书多维表格视图可见字段已通过 CLI 读取：2 个"
    assert calls == [
        {
            "args": [
                "lark-cli",
                "base",
                "+view-get-visible-fields",
                "--as",
                "user",
                "--format",
                "json",
                "--base-token",
                "app/token",
                "--table-id",
                "tbl_1",
                "--view-id",
                "viw/1",
            ],
            "capture_output": True,
            "text": True,
            "timeout": 60,
            "check": False,
        }
    ]


def test_feishu_api_runtime_confirmed_bitable_view_set_visible_fields_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_bitable_view_set_visible_fields",
        confirmed=True,
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        view_id="viw/1",
        visible_fields_config={"visible_fields": ["客户名称", "状态"]},
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书多维表格视图可见字段已更新：viw/1"
    assert client.put_calls == [
        (
            "/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1/views/viw%2F1/visible_fields",
            {"visible_fields": ["客户名称", "状态"]},
        )
    ]


def test_feishu_api_provider_bitable_view_get_card_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"cover_field": "fld_cover"}})
    request = _tool_request(
        "feishu_bitable_view_get_card",
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        view_id="viw/1",
    )

    answer = execute_feishu_api_tool(None, request)

    assert answer == "飞书多维表格视图卡片配置已读取。"
    assert client.calls == [
        (
            "/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1/views/viw%2F1/card",
            {},
        )
    ]


def test_feishu_api_provider_delegates_bitable_view_card_realtime_read_to_mcp_cli(monkeypatch) -> None:
    calls = []

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(
            {
                "args": args,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "check": check,
            }
        )
        return SimpleNamespace(returncode=0, stdout=json.dumps({"data": {"cover_field": "fld_cover"}}), stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request("feishu_bitable_view_get_card", app_token="app/token", table_id="tbl_1", view_id="viw/1")

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书多维表格视图卡片配置已通过 CLI 读取。"
    assert calls == [
        {
            "args": [
                "lark-cli",
                "base",
                "+view-get-card",
                "--as",
                "user",
                "--format",
                "json",
                "--base-token",
                "app/token",
                "--table-id",
                "tbl_1",
                "--view-id",
                "viw/1",
            ],
            "capture_output": True,
            "text": True,
            "timeout": 60,
            "check": False,
        }
    ]


def test_feishu_api_runtime_confirmed_bitable_view_set_card_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_bitable_view_set_card",
        confirmed=True,
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        view_id="viw/1",
        card={"cover_field": "fld_cover"},
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书多维表格视图卡片配置已更新：viw/1"
    assert client.put_calls == [
        (
            "/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1/views/viw%2F1/card",
            {"cover_field": "fld_cover"},
        )
    ]


def test_feishu_api_provider_bitable_view_get_timebar_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {"start_time": "fld_start"}})
    request = _tool_request(
        "feishu_bitable_view_get_timebar",
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        view_id="viw/1",
    )

    answer = execute_feishu_api_tool(None, request)

    assert answer == "飞书多维表格视图时间轴配置已读取。"
    assert client.calls == [
        (
            "/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1/views/viw%2F1/timebar",
            {},
        )
    ]


def test_feishu_api_provider_delegates_bitable_view_timebar_realtime_read_to_mcp_cli(monkeypatch) -> None:
    calls = []

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(
            {
                "args": args,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "check": check,
            }
        )
        return SimpleNamespace(returncode=0, stdout=json.dumps({"data": {"start_time": "fld_start"}}), stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_bitable_view_get_timebar",
        app_token="app/token",
        table_id="tbl_1",
        view_id="viw/1",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书多维表格视图时间轴配置已通过 CLI 读取。"
    assert calls == [
        {
            "args": [
                "lark-cli",
                "base",
                "+view-get-timebar",
                "--as",
                "user",
                "--format",
                "json",
                "--base-token",
                "app/token",
                "--table-id",
                "tbl_1",
                "--view-id",
                "viw/1",
            ],
            "capture_output": True,
            "text": True,
            "timeout": 60,
            "check": False,
        }
    ]


def test_feishu_api_runtime_confirmed_bitable_view_set_timebar_executes_runtime() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_bitable_view_set_timebar",
        confirmed=True,
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        view_id="viw/1",
        timebar={"start_time": "fld_start", "end_time": "fld_end", "title": "fld_title"},
    )

    answer = execute_feishu_api_write_tool(None, request)

    assert answer == "飞书多维表格视图时间轴配置已更新：viw/1"
    assert client.put_calls == [
        (
            "/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1/views/viw%2F1/timebar",
            {"start_time": "fld_start", "end_time": "fld_end", "title": "fld_title"},
        )
    ]


def test_feishu_api_runtime_rejects_unsupported_bitable_view_type() -> None:
    client = _FakeFeishuClient({"code": 0, "msg": "success", "data": {}})
    request = _tool_request(
        "feishu_bitable_view_create",
        confirmed=True,
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        view={"name": "表单视图", "type": "form"},
    )

    try:
        execute_feishu_api_write_tool(None, request)
    except ValueError as exc:
        assert "view type must be one of" in str(exc)
    else:
        raise AssertionError("expected unsupported view type to raise")

    assert client.post_calls == []


def test_feishu_api_provider_reads_bitable_fields() -> None:
    client = _FakeFeishuClient({"data": {"items": [{"field_id": "fld_1", "field_name": "客户名称"}]}})
    request = _tool_request(
        "feishu_bitable_field_list",
        client=client,
        app_token="app/token",
        table_id="tbl_1",
        limit=250,
        offset=-1,
    )

    answer = execute_feishu_api_tool(None, request)

    assert answer == "飞书多维表格字段已读取 1 条：客户名称"
    assert client.calls == [
        (
            "/open-apis/base/v3/bases/app%2Ftoken/tables/tbl_1/fields",
            {"limit": 200, "offset": 0},
        )
    ]


def test_feishu_api_provider_delegates_bitable_field_realtime_read_to_mcp_cli(monkeypatch) -> None:
    calls = []

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(
            {
                "args": args,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "check": check,
            }
        )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"data": {"items": [{"field_id": "fld_1", "field_name": "客户名称"}]}}),
            stderr="",
        )

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_bitable_field_list",
        app_token="app/token",
        table_id="tbl_1",
        limit=250,
        offset=-1,
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书多维表格字段已通过 CLI 读取 1 条：客户名称"
    assert calls == [
        {
            "args": [
                "lark-cli",
                "base",
                "+field-list",
                "--as",
                "user",
                "--format",
                "json",
                "--base-token",
                "app/token",
                "--table-id",
                "tbl_1",
                "--limit",
                "200",
                "--offset",
                "0",
            ],
            "capture_output": True,
            "text": True,
            "timeout": 60,
            "check": False,
        }
    ]


def test_feishu_api_provider_reads_calendar_events() -> None:
    client = _FakeFeishuClient({"data": {"items": [{"summary": "经营例会"}]}})
    request = _tool_request("calendar_qa", client=client, page_size=10)

    answer = execute_feishu_api_tool(None, request)

    assert "飞书日程已读取 1 条：经营例会" == answer
    assert client.calls == [
        ("/open-apis/calendar/v4/calendars/primary/events", {"page_size": 50, "start_time": client.any, "end_time": client.any})
    ]


def test_feishu_api_provider_delegates_calendar_realtime_read_to_mcp_cli(monkeypatch) -> None:
    calls = []

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(
            {
                "args": args,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "check": check,
            }
        )
        return SimpleNamespace(returncode=0, stdout=json.dumps({"data": {"items": [{"summary": "经营例会"}]}}), stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "calendar_qa",
        calendar_id="primary",
        start="2026-06-13",
        end="2026-06-13",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书日程已通过 CLI 读取 1 条：经营例会"
    assert calls == [
        {
            "args": [
                "lark-cli",
                "calendar",
                "+agenda",
                "--as",
                "user",
                "--format",
                "json",
                "--calendar-id",
                "primary",
                "--start",
                "2026-06-13",
                "--end",
                "2026-06-13",
            ],
            "capture_output": True,
            "text": True,
            "timeout": 60,
            "check": False,
        }
    ]


def test_feishu_api_provider_reads_tasks() -> None:
    client = _FakeFeishuClient({"data": {"items": [{"guid": "task_1", "summary": "跟进客户"}], "has_more": False}})
    request = _tool_request("task_qa", client=client, page_size=20, page_token="next")

    answer = execute_feishu_api_tool(None, request)

    assert answer == "飞书任务已读取 1 条：跟进客户"
    assert client.calls == [("/open-apis/task/v2/tasks", {"page_size": 20, "page_token": "next"})]


def test_feishu_api_provider_delegates_task_realtime_read_to_mcp_cli(monkeypatch) -> None:
    calls = []

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(
            {
                "args": args,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "check": check,
            }
        )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"data": {"items": [{"guid": "task_1", "summary": "跟进客户"}]}}),
            stderr="",
        )

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "task_qa",
        query="客户",
        page_size=20,
        page_token="next",
        due_start="2026-06-01",
        due_end="2026-06-30",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书任务已通过 CLI 读取 1 条：跟进客户"
    assert calls == [
        {
            "args": [
                "lark-cli",
                "task",
                "+get-my-tasks",
                "--as",
                "user",
                "--format",
                "json",
                "--query",
                "客户",
                "--due-start",
                "2026-06-01",
                "--due-end",
                "2026-06-30",
                "--page-token",
                "next",
                "--page-limit",
                "20",
            ],
            "capture_output": True,
            "text": True,
            "timeout": 60,
            "check": False,
        }
    ]


def test_feishu_api_provider_reads_okr_cycles() -> None:
    client = _FakeFeishuClient({"data": {"items": [{"id": "cycle_1", "tenant_cycle_id": "tenant_1"}]}})
    request = _tool_request(
        "feishu_okr_cycle_list",
        client=client,
        user_id="ou_1",
        user_id_type="open_id",
        page_size=250,
    )

    answer = execute_feishu_api_tool(None, request)

    assert answer == "飞书 OKR 周期已读取 1 条：cycle_1"
    assert client.calls == [
        (
            "/open-apis/okr/v2/cycles",
            {"user_id": "ou_1", "user_id_type": "open_id", "page_size": 100},
        )
    ]


def test_feishu_api_provider_delegates_okr_cycle_realtime_read_to_mcp_cli(monkeypatch) -> None:
    calls = []

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(
            {
                "args": args,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "check": check,
            }
        )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"data": {"items": [{"id": "cycle_1", "tenant_cycle_id": "tenant_1"}]}}),
            stderr="",
        )

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_okr_cycle_list",
        user_id="ou_1",
        user_id_type="open_id",
        time_range="2026-01--2026-12",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书 OKR 周期已通过 CLI 读取 1 条：cycle_1"
    assert calls == [
        {
            "args": [
                "lark-cli",
                "okr",
                "+cycle-list",
                "--as",
                "user",
                "--format",
                "json",
                "--user-id",
                "ou_1",
                "--user-id-type",
                "open_id",
                "--time-range",
                "2026-01--2026-12",
            ],
            "capture_output": True,
            "text": True,
            "timeout": 60,
            "check": False,
        }
    ]


def test_feishu_api_provider_reads_okr_objectives() -> None:
    client = _FakeFeishuClient({"data": {"items": [{"id": "obj_1", "content": "提升交付质量"}]}})
    request = _tool_request(
        "feishu_okr_objective_list",
        client=client,
        cycle_id="cycle/1",
        user_id_type="open_id",
        department_id_type="open_department_id",
        page_size=20,
        page_token="next",
    )

    answer = execute_feishu_api_tool(None, request)

    assert answer == "飞书 OKR 目标已读取 1 条：提升交付质量"
    assert client.calls == [
        (
            "/open-apis/okr/v2/cycles/cycle%2F1/objectives",
            {
                "user_id_type": "open_id",
                "department_id_type": "open_department_id",
                "page_size": 20,
                "page_token": "next",
            },
        )
    ]


def test_feishu_api_provider_delegates_okr_objective_realtime_read_to_mcp_cli(monkeypatch) -> None:
    calls = []

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(
            {
                "args": args,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "check": check,
            }
        )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"data": {"objectives": [{"id": "obj_1", "content": "提升交付质量"}]}}),
            stderr="",
        )

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request("feishu_okr_objective_list", cycle_id="cycle/1")

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书 OKR 目标已通过 CLI 读取 1 条：提升交付质量"
    assert calls == [
        {
            "args": [
                "lark-cli",
                "okr",
                "+cycle-detail",
                "--as",
                "user",
                "--format",
                "json",
                "--cycle-id",
                "cycle/1",
            ],
            "capture_output": True,
            "text": True,
            "timeout": 60,
            "check": False,
        }
    ]


def test_feishu_api_provider_reads_contact_department_children() -> None:
    client = _FakeFeishuClient({"data": {"items": [{"name": "销售部", "department_id": "od_sales"}]}})
    request = _tool_request(
        "feishu_contact_department_children",
        client=client,
        department_id="od/root",
        department_id_type="open_department_id",
        user_id_type="open_id",
        fetch_child=True,
        page_size=200,
        page_token="next",
    )

    answer = execute_feishu_api_tool(None, request)

    assert answer == "飞书通讯录子部门已读取 1 条：销售部"
    assert client.calls == [
        (
            "/open-apis/contact/v3/departments/od%2Froot/children",
            {
                "department_id_type": "open_department_id",
                "user_id_type": "open_id",
                "fetch_child": True,
                "page_size": 50,
                "page_token": "next",
            },
        )
    ]


def test_feishu_api_provider_delegates_contact_department_children_realtime_read_to_mcp_cli(monkeypatch) -> None:
    calls = []

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(
            {
                "args": args,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "check": check,
            }
        )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"data": {"items": [{"name": "销售部", "department_id": "od_sales"}]}}),
            stderr="",
        )

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_contact_department_children",
        department_id="od/root",
        department_id_type="open_department_id",
        user_id_type="open_id",
        fetch_child=True,
        page_size=200,
        page_token="next",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书通讯录子部门已通过 CLI 读取 1 条：销售部"
    assert calls == [
        {
            "args": [
                "lark-cli",
                "api",
                "GET",
                "/open-apis/contact/v3/departments/:department_id/children",
                "--as",
                "bot",
                "--format",
                "json",
                "--params",
                '{"department_id":"od/root","department_id_type":"open_department_id","user_id_type":"open_id","page_size":50,"fetch_child":true,"page_token":"next"}',
            ],
            "capture_output": True,
            "text": True,
            "timeout": 60,
            "check": False,
        }
    ]


def test_feishu_api_provider_reads_contact_department_users() -> None:
    client = _FakeFeishuClient({"data": {"items": [{"name": "张三", "open_id": "ou_1"}]}})
    request = _tool_request(
        "feishu_contact_department_users",
        client=client,
        department_id="od_sales",
        department_id_type="open_department_id",
        user_id_type="open_id",
        page_size=1,
    )

    answer = execute_feishu_api_tool(None, request)

    assert answer == "飞书通讯录部门用户已读取 1 条：张三"
    assert client.calls == [
        (
            "/open-apis/contact/v3/users/find_by_department",
            {
                "department_id": "od_sales",
                "department_id_type": "open_department_id",
                "user_id_type": "open_id",
                "page_size": 1,
            },
        )
    ]


def test_feishu_api_provider_delegates_contact_department_users_realtime_read_to_mcp_cli(monkeypatch) -> None:
    calls = []

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(
            {
                "args": args,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "check": check,
            }
        )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"data": {"items": [{"name": "张三", "open_id": "ou_1"}]}}),
            stderr="",
        )

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_contact_department_users",
        department_id="od_sales",
        department_id_type="open_department_id",
        user_id_type="open_id",
        page_size=1,
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书通讯录部门用户已通过 CLI 读取 1 条：张三"
    assert calls == [
        {
            "args": [
                "lark-cli",
                "api",
                "GET",
                "/open-apis/contact/v3/users/find_by_department",
                "--as",
                "bot",
                "--format",
                "json",
                "--params",
                '{"department_id":"od_sales","department_id_type":"open_department_id","user_id_type":"open_id","page_size":1}',
            ],
            "capture_output": True,
            "text": True,
            "timeout": 60,
            "check": False,
        }
    ]


def test_feishu_api_provider_reads_contact_authorized_scopes() -> None:
    client = _FakeFeishuClient(
        {"data": {"department_ids": ["od_1", "od_2"], "user_ids": ["ou_1"], "group_ids": ["g_1"]}}
    )
    request = _tool_request(
        "feishu_contact_scope_list",
        client=client,
        department_id_type="open_department_id",
        user_id_type="open_id",
        page_size=200,
        page_token="next",
    )

    answer = execute_feishu_api_tool(None, request)

    assert answer == "飞书通讯录授权范围已读取：部门 2 个，用户 1 个，用户组 1 个。"
    assert client.calls == [
        (
            "/open-apis/contact/v3/scopes",
            {
                "department_id_type": "open_department_id",
                "user_id_type": "open_id",
                "page_size": 100,
                "page_token": "next",
            },
        )
    ]


def test_feishu_api_provider_delegates_contact_scope_realtime_read_to_mcp_cli(monkeypatch) -> None:
    calls = []

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(
            {
                "args": args,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "check": check,
            }
        )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {"data": {"department_ids": ["od_1", "od_2"], "user_ids": ["ou_1"], "group_ids": ["g_1"]}}
            ),
            stderr="",
        )

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_contact_scope_list",
        department_id_type="open_department_id",
        user_id_type="open_id",
        page_size=200,
        page_token="next",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书通讯录授权范围已通过 CLI 读取：部门 2 个，用户 1 个，用户组 1 个。"
    assert calls == [
        {
            "args": [
                "lark-cli",
                "api",
                "GET",
                "/open-apis/contact/v3/scopes",
                "--as",
                "bot",
                "--format",
                "json",
                "--params",
                '{"department_id_type":"open_department_id","user_id_type":"open_id","page_size":100,"page_token":"next"}',
            ],
            "capture_output": True,
            "text": True,
            "timeout": 60,
            "check": False,
        }
    ]


def test_feishu_api_provider_reads_contact_organization_snapshot() -> None:
    client = _FakeFeishuClient(
        [
            {"data": {"items": [{"name": "销售部", "department_id": "od_sales", "parent_department_id": "0"}]}},
            {"data": {"items": []}},
            {"data": {"items": [{"name": "张三", "open_id": "ou_1"}]}},
            {"data": {"items": []}},
        ]
    )
    request = _tool_request(
        "feishu_contact_organization_snapshot",
        client=client,
        root_department_id="0",
        max_departments=10,
        max_users=20,
    )

    answer = execute_feishu_api_tool(None, request)

    assert answer == "飞书通讯录组织快照已读取：部门 1 个，人员 1 人。"
    assert client.calls == [
        (
            "/open-apis/contact/v3/departments/0/children",
            {"department_id_type": "department_id", "user_id_type": "open_id", "page_size": 50},
        ),
        (
            "/open-apis/contact/v3/departments/od_sales/children",
            {"department_id_type": "department_id", "user_id_type": "open_id", "page_size": 50},
        ),
        (
            "/open-apis/contact/v3/users/find_by_department",
            {
                "department_id": "0",
                "department_id_type": "department_id",
                "user_id_type": "open_id",
                "page_size": 50,
            },
        ),
        (
            "/open-apis/contact/v3/users/find_by_department",
            {
                "department_id": "od_sales",
                "department_id_type": "department_id",
                "user_id_type": "open_id",
                "page_size": 50,
            },
        ),
    ]


def test_feishu_api_provider_delegates_contact_organization_snapshot_realtime_read_to_mcp_cli(monkeypatch) -> None:
    calls = []
    payloads = [
        {"data": {"items": [{"name": "销售部", "department_id": "od_sales", "parent_department_id": "0"}]}},
        {"data": {"items": []}},
        {"data": {"items": [{"name": "张三", "open_id": "ou_1"}]}},
        {"data": {"items": []}},
    ]

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(
            {
                "args": args,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "check": check,
            }
        )
        return SimpleNamespace(returncode=0, stdout=json.dumps(payloads.pop(0)), stderr="")

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_contact_organization_snapshot",
        root_department_id="0",
        max_departments=10,
        max_users=20,
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书通讯录组织快照已通过 CLI 读取：部门 1 个，人员 1 人。"
    assert calls == [
        {
            "args": [
                "lark-cli",
                "api",
                "GET",
                "/open-apis/contact/v3/departments/:department_id/children",
                "--as",
                "bot",
                "--format",
                "json",
                "--params",
                '{"department_id":"0","department_id_type":"department_id","user_id_type":"open_id","page_size":50}',
            ],
            "capture_output": True,
            "text": True,
            "timeout": 60,
            "check": False,
        },
        {
            "args": [
                "lark-cli",
                "api",
                "GET",
                "/open-apis/contact/v3/departments/:department_id/children",
                "--as",
                "bot",
                "--format",
                "json",
                "--params",
                '{"department_id":"od_sales","department_id_type":"department_id","user_id_type":"open_id","page_size":50}',
            ],
            "capture_output": True,
            "text": True,
            "timeout": 60,
            "check": False,
        },
        {
            "args": [
                "lark-cli",
                "api",
                "GET",
                "/open-apis/contact/v3/users/find_by_department",
                "--as",
                "bot",
                "--format",
                "json",
                "--params",
                '{"department_id":"0","department_id_type":"department_id","user_id_type":"open_id","page_size":50}',
            ],
            "capture_output": True,
            "text": True,
            "timeout": 60,
            "check": False,
        },
        {
            "args": [
                "lark-cli",
                "api",
                "GET",
                "/open-apis/contact/v3/users/find_by_department",
                "--as",
                "bot",
                "--format",
                "json",
                "--params",
                '{"department_id":"od_sales","department_id_type":"department_id","user_id_type":"open_id","page_size":50}',
            ],
            "capture_output": True,
            "text": True,
            "timeout": 60,
            "check": False,
        },
    ]


def test_feishu_api_provider_reads_bitable_tables() -> None:
    client = _FakeFeishuClient({"data": {"items": [{"table_id": "tbl_1", "name": "客户台账"}]}})
    request = _tool_request("bitable_qa", client=client, app_token="app/token")

    answer = execute_feishu_api_tool(None, request)

    assert answer == "飞书多维表格数据表已读取 1 条：客户台账"
    assert client.calls == [("/open-apis/bitable/v1/apps/app%2Ftoken/tables", {"page_size": 100})]


def test_feishu_api_provider_delegates_bitable_table_realtime_read_to_mcp_cli(monkeypatch) -> None:
    calls = []

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(
            {
                "args": args,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "check": check,
            }
        )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"data": {"items": [{"table_id": "tbl_1", "name": "客户台账"}]}}),
            stderr="",
        )

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request("bitable_qa", app_token="app/token", page_size=20, offset=10)

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书多维表格数据表已通过 CLI 读取 1 条：客户台账"
    assert calls == [
        {
            "args": [
                "lark-cli",
                "base",
                "+table-list",
                "--as",
                "user",
                "--format",
                "json",
                "--base-token",
                "app/token",
                "--offset",
                "10",
                "--limit",
                "20",
            ],
            "capture_output": True,
            "text": True,
            "timeout": 60,
            "check": False,
        }
    ]


def test_feishu_api_provider_reads_bitable_records() -> None:
    client = _FakeFeishuClient({"data": {"items": [{"record_id": "rec_1", "fields": {"客户": "甲公司"}}]}})
    request = _tool_request(
        "bitable_qa",
        client=client,
        app_token="app_1",
        table_id="tbl_1",
        field_names="客户,阶段",
        view_id="vew_1",
    )

    answer = execute_feishu_api_tool(None, request)

    assert answer == "飞书多维表格记录已读取 1 条：rec_1"
    assert client.calls == [
        (
            "/open-apis/bitable/v1/apps/app_1/tables/tbl_1/records",
            {"page_size": 100, "view_id": "vew_1", "field_names": "客户,阶段"},
        )
    ]


def test_feishu_api_provider_delegates_bitable_record_realtime_read_to_mcp_cli(monkeypatch) -> None:
    calls = []

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(
            {
                "args": args,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "check": check,
            }
        )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"data": {"items": [{"record_id": "rec_1", "fields": {"客户": "甲公司"}}]}}),
            stderr="",
        )

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "bitable_qa",
        app_token="app_1",
        table_id="tbl_1",
        field_names="客户,阶段",
        view_id="vew_1",
        filter={"logic": "and", "conditions": [["阶段", "==", "成交"]]},
        sort=[{"field": "客户", "desc": False}],
        page_size=50,
        offset=0,
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书多维表格记录已通过 CLI 读取 1 条：rec_1"
    assert calls == [
        {
            "args": [
                "lark-cli",
                "base",
                "+record-list",
                "--as",
                "user",
                "--format",
                "json",
                "--base-token",
                "app_1",
                "--table-id",
                "tbl_1",
                "--view-id",
                "vew_1",
                "--field-id",
                "客户",
                "--field-id",
                "阶段",
                "--filter-json",
                '{"logic":"and","conditions":[["阶段","==","成交"]]}',
                "--sort-json",
                '[{"field":"客户","desc":false}]',
                "--offset",
                "0",
                "--limit",
                "50",
            ],
            "capture_output": True,
            "text": True,
            "timeout": 60,
            "check": False,
        }
    ]


def test_feishu_api_provider_reads_mail_messages() -> None:
    client = _FakeFeishuClient({"data": {"items": ["msg_1"]}})
    request = _tool_request("mail_qa", client=client, page_size=50)

    answer = execute_feishu_api_tool(None, request)

    assert answer == "飞书邮箱邮件已读取 1 条：msg_1"
    assert client.calls == [("/open-apis/mail/v1/user_mailboxes/me/messages", {"folder_id": "INBOX", "page_size": 20})]


def test_feishu_api_provider_delegates_mail_realtime_read_to_mcp_cli(monkeypatch) -> None:
    calls = []

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(
            {
                "args": args,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "check": check,
            }
        )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"data": {"messages": [{"message_id": "msg_1", "subject": "客户预算"}]}}),
            stderr="",
        )

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "mail_qa",
        query="预算",
        user_mailbox_id="me",
        folder_id="INBOX",
        page_size=20,
        page_token="next",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书邮箱邮件已通过 CLI 读取 1 条：客户预算"
    assert calls == [
        {
            "args": [
                "lark-cli",
                "mail",
                "+triage",
                "--as",
                "user",
                "--format",
                "json",
                "--mailbox",
                "me",
                "--query",
                "预算",
                "--page-token",
                "next",
                "--max",
                "20",
                "--filter",
                '{"folder":"INBOX"}',
            ],
            "capture_output": True,
            "text": True,
            "timeout": 60,
            "check": False,
        }
    ]


def test_feishu_mcp_provider_reads_mail_folders_with_cli(monkeypatch) -> None:
    calls = []

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(
            {
                "args": args,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "check": check,
            }
        )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"data": {"folders": [{"folder_id": "INBOX", "name": "收件箱"}]}}),
            stderr="",
        )

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request("feishu_mail_folder_list", user_mailbox_id="me")

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书邮箱文件夹已通过 CLI 读取 1 个：收件箱"
    assert calls == [
        {
            "args": [
                "lark-cli",
                "mail",
                "user_mailbox.folders",
                "list",
                "--as",
                "user",
                "--format",
                "json",
                "--params",
                '{"user_mailbox_id":"me"}',
            ],
            "capture_output": True,
            "text": True,
            "timeout": 60,
            "check": False,
        }
    ]


def test_feishu_mcp_provider_reads_mail_message_detail_with_cli(monkeypatch) -> None:
    calls = []

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(
            {
                "args": args,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "check": check,
            }
        )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"data": {"message_id": "msg_1", "subject": "客户预算"}}),
            stderr="",
        )

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request("feishu_mail_message_get", user_mailbox_id="me", message_id="msg_1", html=False)

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书邮箱邮件详情已通过 CLI 读取：客户预算"
    assert calls == [
        {
            "args": [
                "lark-cli",
                "mail",
                "+message",
                "--as",
                "user",
                "--format",
                "json",
                "--mailbox",
                "me",
                "--message-id",
                "msg_1",
                "--html",
                "false",
            ],
            "capture_output": True,
            "text": True,
            "timeout": 60,
            "check": False,
        }
    ]


def test_feishu_mcp_provider_searches_vc_meetings_with_cli(monkeypatch) -> None:
    calls = []

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(
            {
                "args": args,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "check": check,
            }
        )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"data": {"items": [{"meeting_id": "m_1", "topic": "经营复盘"}]}}),
            stderr="",
        )

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_vc_meeting_search",
        query="复盘",
        start_time=datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
        end_time=datetime(2026, 6, 14, 0, 0, tzinfo=UTC),
        page_size=30,
        page_token="next",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书历史会议已通过 CLI 读取 1 条：经营复盘"
    assert calls == [
        {
            "args": [
                "lark-cli",
                "vc",
                "+search",
                "--as",
                "user",
                "--format",
                "json",
                "--query",
                "复盘",
                "--start",
                "2026-06-01T00:00:00+00:00",
                "--end",
                "2026-06-14T00:00:00+00:00",
                "--page-token",
                "next",
                "--page-size",
                "30",
            ],
            "capture_output": True,
            "text": True,
            "timeout": 60,
            "check": False,
        }
    ]


def test_feishu_mcp_provider_searches_im_chats_with_cli(monkeypatch) -> None:
    calls = []

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(
            {
                "args": args,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "check": check,
            }
        )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"data": {"items": [{"chat_id": "oc_1", "name": "经营群"}]}}),
            stderr="",
        )

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request("feishu_im_chat_search", query="经营", page_size=20)

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书群聊已通过 CLI 搜索 1 个：经营群"
    assert calls == [
        {
            "args": [
                "lark-cli",
                "im",
                "+chat-search",
                "--as",
                "user",
                "--format",
                "json",
                "--query",
                "经营",
                "--page-size",
                "20",
            ],
            "capture_output": True,
            "text": True,
            "timeout": 60,
            "check": False,
        }
    ]


def test_feishu_mcp_provider_lists_drive_files_with_cli(monkeypatch) -> None:
    calls = []

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(
            {
                "args": args,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "check": check,
            }
        )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"data": {"files": [{"token": "doc_1", "name": "制度手册", "type": "docx"}]}}),
            stderr="",
        )

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request("feishu_drive_file_list", folder_token="fld_1", page_size=50, page_token="next")

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书云空间文件已通过 CLI 读取 1 个：制度手册"
    assert calls == [
        {
            "args": [
                "lark-cli",
                "drive",
                "files",
                "list",
                "--as",
                "user",
                "--format",
                "json",
                "--params",
                '{"page_size":50,"page_token":"next","folder_token":"fld_1"}',
            ],
            "capture_output": True,
            "text": True,
            "timeout": 60,
            "check": False,
        }
    ]


def test_feishu_mcp_provider_lists_im_messages_with_cli(monkeypatch) -> None:
    calls = []

    def fake_run(args, capture_output, text, timeout, check):
        calls.append(
            {
                "args": args,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "check": check,
            }
        )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"data": {"items": [{"message_id": "om_1", "summary": "预算确认"}]}}),
            stderr="",
        )

    monkeypatch.setattr("app.services.tools.providers.lark_cli.subprocess.run", fake_run)
    request = _tool_request(
        "feishu_im_message_list",
        chat_id="oc_1",
        start_time=datetime(2026, 6, 13, 9, 0, tzinfo=UTC),
        end_time=datetime(2026, 6, 13, 18, 0, tzinfo=UTC),
        page_size=20,
        page_token="next",
    )

    answer = execute_feishu_mcp_tool(None, request)

    assert answer == "飞书群聊消息已通过 CLI 读取 1 条：预算确认"
    assert calls == [
        {
            "args": [
                "lark-cli",
                "im",
                "+chat-messages-list",
                "--as",
                "user",
                "--format",
                "json",
                "--chat-id",
                "oc_1",
                "--page-size",
                "20",
                "--start",
                "2026-06-13T09:00:00+00:00",
                "--end",
                "2026-06-13T18:00:00+00:00",
                "--page-token",
                "next",
            ],
            "capture_output": True,
            "text": True,
            "timeout": 60,
            "check": False,
        }
    ]


def test_feishu_api_read_provider_requires_runtime_binding() -> None:
    request = _tool_request("feishu_contact_scope_list")

    try:
        execute_feishu_api_read_tool(request)
    except ValueError as exc:
        assert "requires app_config or client" in str(exc)
    else:
        raise AssertionError("expected missing runtime binding to raise")


def test_feishu_mcp_provider_requires_explicit_binding() -> None:
    assert FEISHU_MCP_CONTRACT.provider_name == "feishu_mcp"
    assert FEISHU_MCP_CONTRACT.role == "tool_scheduling"
    assert FEISHU_MCP_CONTRACT.requires_explicit_tool_binding is True
    assert FEISHU_MCP_CONTRACT.default_write_enabled is False
    assert FEISHU_MCP_CONTRACT.action_executor == "lark_cli"
    assert FEISHU_MCP_CONTRACT.performs_action_execution is False
    assert LARK_CLI_EXECUTION_CONTRACT.role == "action_execution"
    assert LARK_CLI_EXECUTION_CONTRACT.allowed_caller == "feishu_mcp_provider"
    assert LARK_CLI_EXECUTION_CONTRACT.allowed_caller_module == "app.services.tools.providers.feishu_mcp"
    assert LARK_CLI_EXECUTION_CONTRACT.accepts_business_capability is False
    assert LARK_CLI_EXECUTION_CONTRACT.schedules_tools is False
    assert LARK_CLI_EXECUTION_CONTRACT.performs_action_execution is True
    try:
        execute_feishu_mcp_tool(None, type("Request", (), {"tool_name": "calendar"})())
    except NotImplementedError as exc:
        assert "requires a CLI executor behind MCP" in str(exc)
    else:
        raise AssertionError("expected unbound MCP tool to raise")


def test_lark_cli_executor_rejects_direct_non_mcp_call() -> None:
    from app.services.tools.providers.lark_cli import run_lark_cli_json

    try:
        run_lark_cli_json(["lark-cli", "--version"], action="direct test")
    except PermissionError as exc:
        assert "must be scheduled by the Feishu MCP provider" in str(exc)
        assert "test_feishu_provider_boundary" in str(exc)
    else:
        raise AssertionError("expected direct CLI executor call to be rejected")


def test_feishu_api_provider_contract_is_sync_only() -> None:
    assert FEISHU_API_PROVIDER_CONTRACT.provider_name == "feishu_api"
    assert FEISHU_API_PROVIDER_CONTRACT.role == "sync_engine_data_sync"
    assert FEISHU_API_PROVIDER_CONTRACT.allowed_entrypoints == (
        "sync_engine",
        "resource_discovery",
        "admin_sync_preview",
    )
    assert FEISHU_API_PROVIDER_CONTRACT.realtime_read_allowed is False
    assert FEISHU_API_PROVIDER_CONTRACT.realtime_write_allowed is False
    assert FEISHU_API_PROVIDER_CONTRACT.mcp_access_allowed is False
    assert FEISHU_API_PROVIDER_CONTRACT.agent_runtime_direct_access is False
    assert FEISHU_API_PROVIDER_CONTRACT.controlled_validation_allowed is True


def test_feishu_api_provider_rejects_missing_entrypoint_for_direct_call() -> None:
    request = _tool_request("calendar_qa", api_entrypoint=None)

    try:
        execute_feishu_api_tool(None, request)
    except PermissionError as exc:
        assert "requires an explicit sync/resource-discovery/admin-preview entrypoint" in str(exc)
        assert "Tool Router -> MCP -> CLI" in str(exc)
    else:
        raise AssertionError("expected direct Feishu API provider call without entrypoint to be rejected")


def test_feishu_mcp_provider_injects_cli_profile_into_lark_cli_args(monkeypatch) -> None:
    captured = {}

    def fake_run(args, *, action):
        captured["args"] = args
        captured["action"] = action
        return {"items": []}

    monkeypatch.setattr("app.services.tools.providers.feishu_mcp._run_lark_cli_json_base", fake_run)

    result = execute_feishu_mcp_tool(
        ToolContext(db=None, company_id=uuid4(), actor=BotActor(role="owner", access_scope="company")),
        ToolRequest(
            tool_name="calendar_qa",
            question="日程",
            normalized_command="日程",
            params={"cli_profile": "company-gaustek", "response_format": "raw_json"},
        ),
    )

    assert json.loads(result) == {"items": []}
    assert captured["action"] == "calendar +agenda"
    assert captured["args"][:3] == ["lark-cli", "--profile", "company-gaustek"]
    assert captured["args"][3:5] == ["calendar", "+agenda"]


def _tool_request(tool_name: str, _context: ToolContext | None = None, **params) -> ToolRequest:
    params.setdefault("api_entrypoint", "controlled_validation")
    request = ToolRequest(tool_name=tool_name, question="测试", normalized_command="测试", params=params)
    if params.get("confirmed") is True and "confirmation_token" not in params:
        params["confirmation_token"] = feishu_write_confirmation_token(_context, request)
        request = ToolRequest(tool_name=tool_name, question="测试", normalized_command="测试", params=params)
    return request


class _FakeFeishuClient:
    any = object()

    def __init__(self, response, *, post_error_at=None):
        self.response = response
        self.post_error_at = post_error_at
        self.calls = []
        self.post_calls = []
        self.put_calls = []
        self.patch_calls = []
        self.delete_calls = []
        self.user_post_calls = []

    async def api_get(self, path, *, params=None):
        normalized = {}
        for key, value in (params or {}).items():
            normalized[key] = self.any if key in {"start_time", "end_time"} else value
        self.calls.append((path, normalized))
        return self._response()

    async def api_post(self, path, payload=None):
        self.post_calls.append((path, payload or {}))
        if self.post_error_at == len(self.post_calls):
            raise RuntimeError("post failed")
        return self._response()

    async def api_post_user(self, path, *, user_access_token, payload=None):
        self.user_post_calls.append((path, user_access_token, payload or {}))
        return self._response()

    async def api_put(self, path, payload=None):
        self.put_calls.append((path, payload or {}))
        return self.response

    async def api_patch(self, path, payload=None):
        self.patch_calls.append((path, payload or {}))
        return self._response()

    async def api_delete(self, path, payload=None):
        self.delete_calls.append((path, payload or {}))
        return self._response()

    def _response(self):
        if isinstance(self.response, list):
            if self.response:
                return self.response.pop(0)
            return {"code": 0, "data": {}}
        return self.response
