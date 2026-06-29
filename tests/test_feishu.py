import asyncio
import json
from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from uuid import uuid4
from types import SimpleNamespace
from zipfile import ZipFile

from app.api.routes.feishu_admin_capability_approval_resource_routes import list_approval_resources
from app.api.routes.feishu_admin_read_tool_approval_routes import list_pending_approval_tasks
from app.api.routes.feishu_admin_read_tool_bitable_table_routes import list_bitable_tables
from app.api.routes.feishu_admin_read_tool_calendar_routes import list_calendar_events
from app.api.routes.feishu_admin_read_tool_contact_department_routes import list_contact_departments
from app.api.routes.feishu_admin_read_tool_drive_routes import list_drive_files
from app.api.routes.feishu_admin_read_tool_im_chat_routes import search_im_chats
from app.api.routes.feishu_admin_read_tool_im_message_routes import list_im_messages
from app.api.routes.feishu_admin_read_tool_knowledge_routes import get_document_content
from app.api.routes.feishu_admin_read_tool_mail_folder_routes import list_mail_folders
from app.api.routes.feishu_admin_read_tool_mail_message_detail_routes import get_mail_message_detail
from app.api.routes.feishu_admin_read_tool_mail_message_list_routes import list_mail_messages
from app.api.routes.feishu_admin_read_tool_meeting_routes import list_meetings
from app.api.routes.feishu_admin_read_tool_wiki_node_routes import list_wiki_nodes
from app.api.routes.feishu_admin_read_tool_wiki_space_routes import list_wiki_spaces
from app.api.routes.feishu_admin_read_tool_request_models import (
    FeishuApprovalPendingRequest,
    FeishuBitableTablesRequest,
    FeishuCalendarEventsRequest,
    FeishuChatSearchRequest,
    FeishuContactDepartmentsRequest,
    FeishuDocumentContentRequest,
    FeishuDriveFilesRequest,
    FeishuListMessagesRequest,
    FeishuMailFoldersRequest,
    FeishuMailMessageDetailRequest,
    FeishuMailMessagesRequest,
    FeishuMeetingsRequest,
    FeishuTasksRequest,
    FeishuWikiNodesRequest,
    FeishuWikiSpacesRequest,
)
from app.api.routes.feishu_admin_read_tool_task_routes import list_tasks
from app.api.routes.feishu_admin_sync_request_models import FeishuResourceDiscoverRequest
from app.api.routes.feishu_admin_write_approval_routes import submit_approval_action
from app.api.routes.feishu_admin_write_im_auto_join_routes import auto_join_public_chats
from app.api.routes.feishu_admin_write_im_chat_routes import create_chat_with_bot
from app.api.routes.feishu_admin_write_im_message_routes import send_bot_message
from app.api.routes.feishu_admin_write_request_models import (
    FeishuApprovalActionRequest,
    FeishuAutoJoinPublicChatsRequest,
    FeishuCreateChatRequest,
    FeishuSendMessageRequest,
)
from app.services.feishu_admin_write_tools import preview_auto_join_public_chats as _auto_join_public_chats
from app.services.feishu_admin_write_tools import unique_strings as _unique_strings
from app.services.feishu_oauth_helpers import (
    app_config_id_from_oauth_state as _app_config_id_from_oauth_state,
    feishu_oauth_callback_page as _feishu_oauth_callback_page,
)
from app.core.serialization import json_safe
from app.services.feishu.client import FeishuClient, _extract_message_text
from app.services.feishu.client import FeishuClientMode, route_for_feishu_api
from app.services.feishu.approval import FeishuApprovalService
from app.services.feishu import approval_card_responder
from app.services.feishu import sync_commands
from app.shared.file_intelligence import pdf as file_intelligence_pdf
from app.shared.file_intelligence import registry as file_intelligence_registry
from app.services.feishu.approval_cards import approval_card_action_value, approval_card_message_id
from app.services.feishu.approval_attachments import ApprovalAttachmentReadResult, FeishuApprovalAttachmentService, extract_attachment_text
from app.services.feishu.bitable import FeishuBitableService
from app.services.feishu.calendar import FeishuCalendarService
from app.services.feishu.contact import FeishuContactService, build_department_paths, merge_user_department_membership
from app.services.feishu.drive import FeishuDriveService
from app.services.feishu.im import dedupe_chat_candidates, extract_chat_items
from app.services.feishu.mail import FeishuMailService
from app.services.feishu.meeting import FeishuMeetingService
from app.services.feishu.task import FeishuTaskService, extract_task_items
from app.services.feishu.user_accounts import (
    sanitized_feishu_user_account,
    upsert_feishu_user_account,
    upsert_feishu_user_identity_resource,
)
from app.services.feishu import command_handlers as command_handler_module
from app.services.feishu.command_handlers import (
    approval_attachment_results,
    load_approval_context,
    normalize_command_with_context,
    store_approval_context,
)
from app.services.feishu.approval_card_entrypoint import (
    _execute_feishu_approval_action,
    _ensure_feishu_pending_approval_attachment_summaries,
    _fetch_feishu_pending_approval_tasks,
    _record_feishu_approval_action_audit,
)
from app.services.feishu import approval_enrichment
from app.services.feishu.approval_advice import approval_attachment_basis, rule_approval_decision_recommendation
from app.services.feishu.approval_advice import approval_text_decision_advice as _approval_text_decision_advice
from app.services.feishu.approval_resources import (
    attach_synced_approval_attachments as _attach_synced_approval_attachments,
)
from app.services.feishu.command_parser import (
    extract_command_text,
    get_sender_open_id as _get_sender_open_id,
    is_approval_approve_request as _is_approval_approve_request,
    is_approval_reject_request as _is_approval_reject_request,
    normalize_command as _normalize_command,
    should_reply_to_message as _should_reply_to_message,
)
from app.services.feishu.approval_formatters import (
    approval_instance_code as _approval_instance_code,
    format_approval_detail_lines as _format_approval_detail_lines,
    format_pending_approval_tasks as _format_pending_approval_tasks,
    readable_approval_name,
    select_approval_item as _select_approval_item,
)
from app.services.feishu.approval import (
    approval_amount as _approval_amount,
    approval_attachment_refs as _approval_attachment_refs,
    approval_form_fields as _approval_form_fields,
)
from app.services.feishu.approval import extract_approval_task_items as _extract_approval_task_items
from app.services.feishu.identity import get_sender_identity as _get_sender_identity
from app.services.feishu.work_event_replies import approval_instance_status
from app.services.feishu.approval_card_entrypoint import (
    build_feishu_approval_action_card,
    handle_feishu_card_action_message,
    handle_feishu_card_action_response,
)
import app.services.feishu.resources as resource_module
from app.services.feishu.resources import extract_resource_candidates_from_payload, extract_resource_candidates_from_text
from app.services.feishu.resources import (
    DEFAULT_FEISHU_RESOURCE_KINDS,
    discover_feishu_resources,
    _discover_approval_resources,
    _discover_drive_folder_tree,
    _discovered_resource_payload,
    _discover_user_drive_files,
    _dedupe_discovered,
    _docs_search_resources,
    _explicit_approval_resources,
    _explicit_bitable_table_resources,
    _explicit_document_resources,
    _explicit_folder_resources,
    _explicit_wiki_resources,
    _folder_tokens_from_resources,
    _folder_tokens_from_v5_resource,
    _link_local_work_events_to_resource,
    _mailbox_candidates,
    _normalize_discovery_kinds,
    _resource_coverage,
)
from app.services.resource_registry import LegacyFeishuResourceMigration, _source_type_for_feishu_item, feishu_discovered_resource_spec
from app.services.feishu.sync import (
    _api_item_to_event,
    _approval_codes_for_sync,
    _approval_metadata_for_sync,
    _bitable_record_to_index_event,
    _contact_department_to_event,
    _contact_user_to_event,
    _decode_mail_body,
    _enrich_approval_item_with_attachments,
    _enrich_approval_item_with_ai_advice,
    _enrich_approval_item_with_instance_detail,
    _sync_contacts,
    _upsert_bot_user_access_from_contact,
    get_feishu_sync_plan,
    sync_feishu_information,
)
from app.services.feishu.sync_commands import _mail_folder_id_from_resource
from app.services.permissions import infer_permission_profile
from app.services.tools.base import ToolProvider, ToolResult


def test_feishu_sync_plan_keeps_api_as_sync_layer_not_realtime_agent_path() -> None:
    plan_by_key = {item["key"]: item for item in get_feishu_sync_plan()}

    for key in ("calendar", "tasks", "meetings"):
        assert plan_by_key[key]["sync_mode"] == "api_snapshot + scheduled_ingest"
        assert "live_api" not in plan_by_key[key]["sync_mode"]

    assert "Tool Router/MCP/CLI" in plan_by_key["calendar"]["notes"]
    assert "API 只负责" in plan_by_key["calendar"]["notes"]


def test_extract_message_text_from_string() -> None:
    assert _extract_message_text({"content": "hello"}) == "hello"


def test_extract_message_text_from_json_string() -> None:
    assert _extract_message_text({"content": '{"text":"帮助"}'}) == "帮助"


def test_extract_message_text_from_history_body() -> None:
    assert _extract_message_text({"body": {"content": '{"text":"历史消息"}'}}) == "历史消息"


def test_extract_message_text_from_dict() -> None:
    assert _extract_message_text({"content": {"text": "hello"}}) == "hello"


def test_json_safe_converts_nested_uuid_to_string() -> None:
    value = uuid4()
    payload = {"company_id": value, "settings": {"owner_ids": [value], "nested": {"id": value}}}

    converted = json_safe(payload)

    assert converted == {
        "company_id": str(value),
        "settings": {"owner_ids": [str(value)], "nested": {"id": str(value)}},
    }


def test_extract_feishu_command_from_json_message_content() -> None:
    payload = {"event": {"message": {"content": '{"text":"今日日报"}'}}}

    assert extract_command_text(payload) == "今日日报"


def test_normalize_command_alias() -> None:
    assert _normalize_command("/日报") == "今日日报"


def test_normalize_approval_command_alias() -> None:
    assert _normalize_command("同步审批") == "同步审批"
    assert _normalize_command("审批") == "最近审批"
    assert _normalize_command("帮我查下未审批的单子") == "最近审批"
    assert _normalize_command("那现在有需要我批复的审批吗") == "最近审批"
    assert _normalize_command("有没有需要我处理的单子") == "最近审批"
    assert _normalize_command("你建议我同意还是拒绝") == "审批建议"
    assert _normalize_command("这笔付款我该不该同意") == "审批建议"
    assert _normalize_command("帮我审批通过") == "审批通过请求"
    assert _normalize_command("确认同意") == "确认审批通过"
    assert _normalize_command("帮我把组织架构以Xmind的形式输出给我") == "组织架构"
    assert _normalize_command("我是企业老板，你又忘记了。") == "身份确认"
    assert _normalize_command("现在你是离线的吗") == "在线状态"
    assert _normalize_command("你是谁?") == "机器人身份"
    assert _normalize_command("固势的总经理是谁") == "管理人员查询"
    assert _normalize_command("驾驶舱") == "驾驶舱概览"
    assert _normalize_command("今日重点") == "驾驶舱今日重点"
    assert _normalize_command("风险预警") == "驾驶舱风险"
    assert _normalize_command("审批动态") == "驾驶舱审批"
    assert _normalize_command("以后回答简洁点") == "以后回答简洁点"


def test_normalize_command_uses_approval_context(monkeypatch) -> None:
    monkeypatch.setattr(command_handler_module, "load_approval_context", lambda *args, **kwargs: [{"id": "approval_1"}])

    app_config = SimpleNamespace(id=uuid4())
    identity = SimpleNamespace(open_id="ou_1")

    assert normalize_command_with_context(app_config, identity, "oc_1", "展开一下", "展开一下") == "审批详情请求"
    assert normalize_command_with_context(app_config, identity, "oc_1", "通过它", "通过它") == "审批通过请求"
    assert normalize_command_with_context(app_config, identity, "oc_1", "拒绝这个", "拒绝这个") == "审批拒绝请求"
    assert normalize_command_with_context(app_config, identity, "oc_1", "你建议呢", "你建议呢") == "审批建议"


def test_list_pending_approval_tasks_routes_through_tool_router(monkeypatch) -> None:
    company_id = uuid4()
    app_config_id = uuid4()
    app_config = SimpleNamespace(id=app_config_id, company_id=company_id)
    captured = []

    class FakeDb:
        def __init__(self):
            self.added = []
            self.committed = False

        def add(self, item):
            self.added.append(item)

        def commit(self):
            self.committed = True

    def fake_execute_agent_tool(context, request):
        captured.append((context, request))
        if request.tool_name == "feishu_approval_instance_get":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MCP,
                answer=json.dumps(
                    {
                        "data": {
                            "instance_code": "inst_1",
                            "approval_name": "付款明细审批",
                            "serial_number": "SN-1",
                            "form": '[{"name":"付款金额","value":3724}]',
                        }
                    }
                ),
            )
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.FEISHU_MCP,
            answer=json.dumps(
                {
                    "data": {
                        "tasks": [
                            {
                                "task_id": "task_1",
                                "process_code": "inst_1",
                                "definition_code": "approval_xxx",
                                "definition_name": "付款审批",
                            }
                        ]
                    }
                }
            ),
        )

    monkeypatch.setattr(
        "app.api.routes.feishu_admin_read_tool_approval_routes.get_feishu_app_or_404",
        lambda db, received_id: app_config,
    )
    monkeypatch.setattr("app.services.feishu_admin_read_tools.execute_agent_tool", fake_execute_agent_tool)

    db = FakeDb()
    result = asyncio.run(
        list_pending_approval_tasks(
            app_config_id,
            FeishuApprovalPendingRequest(open_id="ou_owner", limit=8),
            db=db,
        )
    )

    assert result["available"] is True
    assert result["items"][0]["approval_code"] == "approval_xxx"
    assert result["items"][0]["approval_name"] == "付款审批"
    assert result["items"][0]["process_code"] == "inst_1"
    assert result["items"][0]["instance_detail"]["form"] == '[{"name":"付款金额","value":3724}]'
    assert captured[0][0].company_id == company_id
    assert captured[0][0].actor.domains == ("approval",)
    assert captured[0][1].tool_name == "feishu_approval_task_query"
    assert captured[0][1].params == {
        "open_id": "ou_owner",
        "topic": "1",
        "user_id_type": "open_id",
        "page_size": 8,
        "response_format": "raw_json",
    }
    assert captured[1][1].tool_name == "feishu_approval_instance_get"
    assert captured[1][1].params == {
        "instance_code": "inst_1",
        "user_id_type": "open_id",
        "response_format": "raw_json",
    }
    assert db.committed is True
    assert db.added[0].action == "feishu.approvals.pending"
    assert db.added[0].payload["available"] is True


def test_submit_approval_action_routes_through_tool_router(monkeypatch) -> None:
    company_id = uuid4()
    app_config_id = uuid4()
    app_config = SimpleNamespace(id=app_config_id, company_id=company_id)
    captured = {}

    class FakeDb:
        def __init__(self):
            self.added = []
            self.committed = False

        def add(self, item):
            self.added.append(item)

        def commit(self):
            self.committed = True

    def fake_execute_agent_tool(context, request):
        captured["context"] = context
        captured["request"] = request
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.FEISHU_MCP,
            answer="Dry-run only. confirmation_token: token_1",
            metadata={
                "dry_run": True,
                "confirmed": False,
                "has_confirmation_token": False,
                "write_target": {"kind": "approval_task", "identifiers": {"task_id": "task_1"}},
            },
        )

    monkeypatch.setattr(
        "app.api.routes.feishu_admin_write_approval_routes.get_feishu_app_or_404",
        lambda db, received_id: app_config,
    )
    monkeypatch.setattr("app.services.feishu_admin_write_tools.execute_agent_tool", fake_execute_agent_tool)

    db = FakeDb()
    result = asyncio.run(
        submit_approval_action(
            app_config_id,
            FeishuApprovalActionRequest(
                open_id="ou_admin",
                approval_code="approval_1",
                instance_code="instance_1",
                task_id="task_1",
                action="approve",
            ),
            db=db,
        )
    )

    assert result["ok"] is True
    assert result["status"] == "success"
    assert result["answer"].startswith("Dry-run only.")
    assert captured["context"].company_id == company_id
    assert captured["context"].actor.open_id == "ou_admin"
    assert captured["request"].tool_name == "feishu_approval_task_approve"
    assert captured["request"].params["app_config"] is app_config
    assert captured["request"].params["dry_run"] is True
    assert captured["request"].params["confirmed"] is False
    assert db.committed is True
    assert db.added[0].action == "feishu.approvals.approve"
    assert db.added[0].payload["tool_name"] == "feishu_approval_task_approve"
    assert db.added[0].payload["write_target"]["identifiers"]["task_id"] == "task_1"


def test_send_bot_message_routes_through_im_tool_router(monkeypatch) -> None:
    company_id = uuid4()
    app_config_id = uuid4()
    app_config = SimpleNamespace(id=app_config_id, company_id=company_id)
    captured = {}

    class FakeDb:
        def __init__(self):
            self.added = []
            self.committed = False

        def add(self, item):
            self.added.append(item)

        def commit(self):
            self.committed = True

    def fake_execute_agent_tool(context, request):
        captured["context"] = context
        captured["request"] = request
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.FEISHU_MCP,
            answer="Dry-run only. confirmation_token: token_1",
            metadata={
                "dry_run": True,
                "confirmed": False,
                "has_confirmation_token": False,
                "write_target": {
                    "operation": "im.send_message",
                    "identifiers": {"receive_id": "oc_team"},
                    "param_keys": ["receive_id", "receive_id_type", "text"],
                },
            },
        )

    monkeypatch.setattr(
        "app.api.routes.feishu_admin_write_im_message_routes.get_feishu_app_or_404",
        lambda db, received_id: app_config,
    )
    monkeypatch.setattr("app.services.feishu_admin_write_tools.execute_agent_tool", fake_execute_agent_tool)

    db = FakeDb()
    result = asyncio.run(
        send_bot_message(
            app_config_id,
            FeishuSendMessageRequest(
                receive_id_type="chat_id",
                receive_id="oc_team",
                content={"text": "经营例会已创建"},
                actor_open_id="ou_admin",
            ),
            db=db,
        )
    )

    assert result["ok"] is True
    assert result["answer"].startswith("Dry-run only.")
    assert captured["context"].company_id == company_id
    assert captured["context"].actor.open_id == "ou_admin"
    assert captured["context"].actor.domains == ("im",)
    assert captured["request"].tool_name == "feishu_im_send_message"
    assert captured["request"].params["app_config"] is app_config
    assert captured["request"].params["receive_id_type"] == "chat_id"
    assert captured["request"].params["receive_id"] == "oc_team"
    assert captured["request"].params["text"] == "经营例会已创建"
    assert captured["request"].params["dry_run"] is True
    assert db.committed is True
    assert db.added[0].action == "feishu.bot.send"
    assert db.added[0].payload["tool_name"] == "feishu_im_send_message"
    assert db.added[0].payload["write_target"]["identifiers"]["receive_id"] == "oc_team"


def test_admin_read_routes_use_tool_router_for_realtime_feishu_reads(monkeypatch) -> None:
    company_id = uuid4()
    app_config_id = uuid4()
    app_config = SimpleNamespace(id=app_config_id, company_id=company_id)
    calls = []

    class FakeDb:
        def __init__(self):
            self.added = []
            self.committed = False

        def add(self, item):
            self.added.append(item)

        def commit(self):
            self.committed = True

    def fake_execute_agent_tool(context, request):
        calls.append((context, request))
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.FEISHU_MCP,
            answer=json.dumps({"data": {"items": [{"id": request.tool_name}]}}),
        )

    for module_name in (
        "feishu_admin_read_tool_bitable_table_routes",
        "feishu_admin_read_tool_calendar_routes",
        "feishu_admin_read_tool_contact_department_routes",
        "feishu_admin_read_tool_drive_routes",
        "feishu_admin_read_tool_im_chat_routes",
        "feishu_admin_read_tool_im_message_routes",
        "feishu_admin_read_tool_knowledge_routes",
        "feishu_admin_read_tool_mail_folder_routes",
        "feishu_admin_read_tool_mail_message_detail_routes",
        "feishu_admin_read_tool_mail_message_list_routes",
        "feishu_admin_read_tool_meeting_routes",
        "feishu_admin_read_tool_task_routes",
        "feishu_admin_read_tool_wiki_node_routes",
        "feishu_admin_read_tool_wiki_space_routes",
    ):
        monkeypatch.setattr(
            f"app.api.routes.{module_name}.get_feishu_app_or_404",
            lambda db, received_id: app_config,
        )
    monkeypatch.setattr("app.services.feishu_admin_read_tools.execute_agent_tool", fake_execute_agent_tool)

    db = FakeDb()
    route_calls = [
        (
            list_contact_departments,
            FeishuContactDepartmentsRequest(department_id="0"),
            "feishu_contact_department_children",
            "admin",
        ),
        (
            list_calendar_events,
            FeishuCalendarEventsRequest(start_time=datetime(2026, 6, 13, 9, 0, tzinfo=UTC)),
            "calendar_qa",
            "admin",
        ),
        (
            search_im_chats,
            FeishuChatSearchRequest(query="经营群"),
            "feishu_im_chat_search",
            "admin",
        ),
        (
            list_im_messages,
            FeishuListMessagesRequest(chat_id="oc_1", start_time=datetime(2026, 6, 13, 9, 0, tzinfo=UTC)),
            "feishu_im_message_list",
            "admin",
        ),
        (
            get_document_content,
            FeishuDocumentContentRequest(document_id="doc_1"),
            "feishu_doc_fetch",
            "admin",
        ),
        (
            list_drive_files,
            FeishuDriveFilesRequest(folder_token="fld_1", page_size=50),
            "feishu_drive_file_list",
            "admin",
        ),
        (
            list_wiki_spaces,
            FeishuWikiSpacesRequest(page_size=20),
            "feishu_wiki_space_list",
            "admin",
        ),
        (
            list_wiki_nodes,
            FeishuWikiNodesRequest(space_id="spc_1"),
            "feishu_wiki_node_list",
            "admin",
        ),
        (
            list_bitable_tables,
            FeishuBitableTablesRequest(app_token="app_1", page_token="10"),
            "bitable_qa",
            "admin",
        ),
        (
            list_tasks,
            FeishuTasksRequest(page_size=20),
            "task_qa",
            "admin",
        ),
        (
            list_mail_messages,
            FeishuMailMessagesRequest(user_mailbox_id="owner@example.com"),
            "mail_qa",
            "owner",
        ),
        (
            list_mail_folders,
            FeishuMailFoldersRequest(user_mailbox_id="owner@example.com"),
            "feishu_mail_folder_list",
            "admin",
        ),
        (
            get_mail_message_detail,
            FeishuMailMessageDetailRequest(user_mailbox_id="owner@example.com", message_id="msg_1"),
            "feishu_mail_message_get",
            "admin",
        ),
        (
            list_meetings,
            FeishuMeetingsRequest(start_time=datetime(2026, 6, 13, 9, 0, tzinfo=UTC)),
            "feishu_vc_meeting_search",
            "admin",
        ),
    ]

    for route, request_model, expected_tool, expected_role in route_calls:
        result = asyncio.run(route(app_config_id, request_model, db=db))
        context, request = calls[-1]
        assert result == {"data": {"items": [{"id": expected_tool}]}}
        assert context.company_id == company_id
        assert context.actor.role == expected_role
        assert context.actor.access_scope == "company"
        assert request.tool_name == expected_tool
        assert request.params["response_format"] == "raw_json"
    bitable_request = next(request for _, request in calls if request.tool_name == "bitable_qa")
    assert bitable_request.params["offset"] == 10


def test_send_bot_message_rejects_unconfirmed_real_send(monkeypatch) -> None:
    company_id = uuid4()
    app_config_id = uuid4()
    app_config = SimpleNamespace(id=app_config_id, company_id=company_id)

    class FakeDb:
        def __init__(self):
            self.added = []
            self.committed = False

        def scalar(self, query):
            return None

        def add(self, item):
            self.added.append(item)

        def commit(self):
            self.committed = True

    monkeypatch.setattr(
        "app.api.routes.feishu_admin_write_im_message_routes.get_feishu_app_or_404",
        lambda db, received_id: app_config,
    )

    db = FakeDb()
    result = asyncio.run(
        send_bot_message(
            app_config_id,
            FeishuSendMessageRequest(
                receive_id_type="chat_id",
                receive_id="oc_team",
                content={"text": "经营例会已创建"},
                dry_run=False,
                confirmed=False,
            ),
            db=db,
        )
    )

    assert result["ok"] is False
    assert result["status"] == "denied"
    assert "requires dry_run or confirmed=true" in result["error"]
    assert db.committed is True
    assert db.added[0].action == "tool.im.message.send"
    assert db.added[0].payload["write_mode"] == "pending_confirmation"
    assert db.added[1].action == "feishu.bot.send"
    assert db.added[1].payload["tool_name"] == "feishu_im_send_message"
    assert "confirmation_token" not in db.added[1].payload["write_target"]["param_keys"]


def test_create_chat_with_bot_routes_dry_run_through_tool_router(monkeypatch) -> None:
    company_id = uuid4()
    app_config_id = uuid4()
    app_config = SimpleNamespace(id=app_config_id, company_id=company_id, app_id="cli_bot")

    class FakeDb:
        def __init__(self):
            self.added = []
            self.committed = False

        def scalar(self, query):
            return None

        def add(self, item):
            self.added.append(item)

        def commit(self):
            self.committed = True

    monkeypatch.setattr(
        "app.api.routes.feishu_admin_write_im_chat_routes.get_feishu_app_or_404",
        lambda db, received_id: app_config,
    )

    db = FakeDb()
    result = asyncio.run(
        create_chat_with_bot(
            app_config_id,
            FeishuCreateChatRequest(
                name="经营例会群",
                description="dry-run 验证",
                user_id_list=["ou_owner", "ou_owner"],
                include_current_bot=True,
                actor_open_id="ou_admin",
            ),
            db=db,
        )
    )

    assert result["ok"] is True
    assert result["bot_id_list"] == ["cli_bot"]
    assert "confirmation_token:" in result["answer"]
    assert db.committed is True
    assert db.added[0].action == "tool.im.chat.create"
    assert db.added[0].payload["write_mode"] == "dry_run"
    assert db.added[0].payload["write_target"]["param_keys"] == [
        "bot_id_list",
        "chat_mode",
        "chat_type",
        "description",
        "name",
        "user_id_list",
        "user_id_type",
    ]
    assert db.added[1].action == "feishu.chat.create_with_bot"
    assert db.added[1].payload["tool_name"] == "feishu_im_create_chat"
    assert db.added[1].payload["bot_count"] == 1


def test_create_chat_with_bot_rejects_unconfirmed_real_create(monkeypatch) -> None:
    company_id = uuid4()
    app_config_id = uuid4()
    app_config = SimpleNamespace(id=app_config_id, company_id=company_id, app_id="cli_bot")

    class FakeDb:
        def __init__(self):
            self.added = []
            self.committed = False

        def scalar(self, query):
            return None

        def add(self, item):
            self.added.append(item)

        def commit(self):
            self.committed = True

    monkeypatch.setattr(
        "app.api.routes.feishu_admin_write_im_chat_routes.get_feishu_app_or_404",
        lambda db, received_id: app_config,
    )

    db = FakeDb()
    result = asyncio.run(
        create_chat_with_bot(
            app_config_id,
            FeishuCreateChatRequest(
                name="经营例会群",
                user_id_list=["ou_owner"],
                dry_run=False,
                confirmed=False,
            ),
            db=db,
        )
    )

    assert result["ok"] is False
    assert result["status"] == "denied"
    assert "requires dry_run or confirmed=true" in result["error"]
    assert db.committed is True
    assert db.added[0].action == "tool.im.chat.create"
    assert db.added[0].payload["write_mode"] == "pending_confirmation"
    assert db.added[1].action == "feishu.chat.create_with_bot"
    assert db.added[1].payload["tool_name"] == "feishu_im_create_chat"
    assert "confirmation_token" not in db.added[1].payload["write_target"]["param_keys"]


def test_quick_sync_kinds_detects_relevant_intents() -> None:
    assert sync_commands.quick_sync_kinds("最近审批有什么要处理") == ["approvals"]
    assert sync_commands.quick_sync_kinds("今天公司有什么风险") == ["mail", "approvals", "tasks"]
    assert sync_commands.quick_sync_kinds("会议安排") == ["calendar", "meetings"]


def test_mail_folder_id_from_resource_reads_nested_v5_settings() -> None:
    assert (
        _mail_folder_id_from_resource(
            SimpleNamespace(resource_sub_id=None, config_json={"settings": {"folder_id": "INBOX"}})
        )
        == "INBOX"
    )


def test_sender_identity_defaults_to_chat_member() -> None:
    class FakeDb:
        def scalar(self, query):
            return None

    payload = {"event": {"sender": {"sender_id": {"open_id": "ou_member"}}}}
    identity = _get_sender_identity(FakeDb(), SimpleNamespace(company_id=uuid4()), payload)

    assert identity.role == "member"
    assert identity.access_scope == "chat"
    assert identity.can_query_company is False


def test_sender_identity_uses_database_access() -> None:
    class FakeDb:
        def scalar(self, query):
            return SimpleNamespace(role="admin", access_scope="company", display_name="老板", settings={})

    payload = {"event": {"sender": {"sender_id": {"open_id": "ou_owner"}}}}
    identity = _get_sender_identity(FakeDb(), SimpleNamespace(company_id=uuid4()), payload)

    assert identity.role == "admin"
    assert identity.access_scope == "company"
    assert identity.can_query_company is True


def test_sender_identity_uses_domain_permissions() -> None:
    class FakeDb:
        def scalar(self, query):
            return SimpleNamespace(
                role="manager",
                access_scope="domain",
                display_name="财务负责人",
                settings={"permission_domains": ["finance", "approval"], "allowed_resources": ["付款审批"]},
            )

    payload = {"event": {"sender": {"sender_id": {"open_id": "ou_finance"}}}}
    identity = _get_sender_identity(FakeDb(), SimpleNamespace(company_id=uuid4()), payload)

    assert identity.can_query_company is False
    assert identity.can_query_approvals() is True
    assert identity.can_query_domains("最近付款审批有什么风险") is True
    assert identity.can_query_domains("最近研发项目有什么风险") is False


def test_permission_profile_infers_finance_manager() -> None:
    profile = infer_permission_profile(
        display_name="王财务",
        job_title="财务经理",
        department_names=["财务部"],
        email="finance@example.com",
    )

    assert profile.role == "manager"
    assert profile.access_scope == "domain"
    assert "finance" in profile.domains
    assert "approval" in profile.domains


def test_contact_sync_creates_employee_personal_agent_access() -> None:
    class FakeDb:
        def __init__(self):
            self.added = []

        def scalar(self, query):
            return None

        def add(self, item):
            self.added.append(item)

    app_config = SimpleNamespace(company_id=uuid4())
    db = FakeDb()

    result = _upsert_bot_user_access_from_contact(
        db,
        app_config=app_config,
        user={
            "open_id": "ou_member",
            "name": "测试成员",
            "enterprise_email": "member@example.com",
            "job_title": "工程师",
            "department_ids": ["0"],
        },
    )

    assert result == {"synced": True, "created": True, "open_id": "ou_member"}
    access = next(item for item in db.added if getattr(item, "open_id", None) == "ou_member")
    audit = next(item for item in db.added if getattr(item, "action", None) == "agent.employee.created_from_contact")
    assert access.open_id == "ou_member"
    assert access.role == "member"
    assert access.access_scope == "chat"
    assert access.settings["source"] == "feishu_contacts"
    assert access.settings["agent_model"] == "per_user_personal_agent"
    assert access.settings["agent_profile"]["status"] == "active"
    assert access.settings["agent_profile"]["scope"] == "chat"
    assert access.settings["agent_profile"]["entrypoint"] == "feishu_bot"
    assert access.settings["agent_profile"]["activation_source"] == "feishu_contacts"
    assert access.settings["agent_profile"]["requires_feishu_app_config"] is False
    bundle = access.settings["user_identity_authorizations"]["user_identity_bundle"]
    assert bundle["status"] == "not_authorized"
    assert bundle["authorization_model"] == "bundle_authorization"
    assert bundle["covered_resources"] == ["personal_feishu", "external_mail", "personal_dingtalk", "personal_wechat"]
    assert bundle["owner_open_id"] == "ou_member"
    assert bundle["can_escalate_original_permissions"] is False
    assert audit.company_id == app_config.company_id
    assert audit.actor == "ou_member"
    assert audit.target_type == "employee_agent"
    assert audit.target_id == "ou_member"
    assert audit.payload["status"] == "success"
    assert audit.payload["agent_type"] == "employee_personal_agent"
    assert audit.payload["activation_source"] == "feishu_contacts"
    assert audit.payload["synced_from"] == "contact_sync"
    assert audit.payload["agent_scope"] == "chat"
    assert audit.payload["shared_business_tool_count"] == 9
    assert audit.payload["enterprise_scope_status"] == "available"
    assert audit.payload["enterprise_resources_available_after_agent_created"] is True
    assert audit.payload["user_identity_access_model"] == "bundle_authorization"
    assert audit.payload["user_identity_bundle_status"] == "not_authorized"
    assert audit.payload["can_escalate_original_permissions"] is False


def test_contact_sync_preserves_existing_user_identity_authorizations() -> None:
    existing = SimpleNamespace(
        company_id=uuid4(),
        open_id="ou_member",
        display_name="旧名称",
        role="member",
        access_scope="personal",
        settings={
            "user_identity_authorizations": {
                "personal_feishu": {
                    "status": "authorized",
                    "owner_open_id": "ou_member",
                    "can_escalate_original_permissions": False,
                }
            }
        },
    )

    class FakeDb:
        def scalar(self, query):
            return existing

        def add(self, item):
            raise AssertionError("existing agent should be updated in place")

    _upsert_bot_user_access_from_contact(
        FakeDb(),
        app_config=SimpleNamespace(company_id=existing.company_id),
        user={
            "open_id": "ou_member",
            "name": "新名称",
            "enterprise_email": "member@example.com",
            "job_title": "工程师",
            "department_ids": ["0"],
        },
    )

    assert existing.settings["user_identity_authorizations"]["personal_feishu"]["status"] == "authorized"
    assert existing.settings["agent_profile"]["entrypoint"] == "feishu_bot"
    assert existing.settings["agent_profile"]["activation_source"] == "feishu_contacts"


def test_group_message_requires_bot_mention_to_reply() -> None:
    app_config = SimpleNamespace(app_id="cli_bot", name="大飞哥")
    group_payload = {"event": {"message": {"chat_id": "oc_group", "chat_type": "group", "content": '{"text":"普通消息"}'}}}
    mentioned_payload = {
        "event": {
            "message": {
                "chat_id": "oc_group",
                "chat_type": "group",
                "content": '{"text":"@大飞哥 帮我看看"}',
                "mentions": [{"name": "大飞哥"}],
            }
        }
    }
    private_payload = {"event": {"message": {"chat_id": "ou_direct", "chat_type": "p2p", "content": '{"text":"帮助"}'}}}

    assert _should_reply_to_message(app_config, group_payload) is False
    assert _should_reply_to_message(app_config, mentioned_payload) is True
    assert _should_reply_to_message(app_config, private_payload) is True


def test_feishu_sync_plan_classifies_core_information_types() -> None:
    plan = {item["key"]: item for item in get_feishu_sync_plan()}

    assert plan["messages"]["sync_mode"] == "event_realtime + api_history + work_events"
    assert plan["messages"]["required_params"] == []
    assert plan["mail"]["required_params"] == []
    assert plan["contacts"]["scheduled_ingest"] is True
    assert plan["approvals"]["realtime_event_types"]
    assert plan["approvals"]["required_params"] == []
    assert plan["bitable"]["required_params"] == []


def test_decode_feishu_mail_body_base64() -> None:
    assert _decode_mail_body("5L2g5aW9") == "你好"


def test_feishu_user_oauth_url_contains_app_and_state() -> None:
    app_config = SimpleNamespace(app_id="cli_xxx", app_secret="secret")

    url = FeishuClient(app_config).build_user_oauth_url(state="state-123")

    assert "app_id=cli_xxx" in url
    assert "state=state-123" in url
    assert "redirect_uri=" in url


def test_feishu_oauth_callback_state_and_page_are_safe() -> None:
    app_config_id = uuid4()

    assert _app_config_id_from_oauth_state(f"app_config_id:{app_config_id}") == app_config_id
    assert _app_config_id_from_oauth_state("bad-state") is None

    response = _feishu_oauth_callback_page(False, "<script>alert(1)</script>", state="<bad>")
    html = response.body.decode()

    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;bad&gt;" in html


def test_feishu_user_oauth_account_is_saved_and_registered_as_source() -> None:
    class FakeDb:
        def __init__(self):
            self.added = []

        def scalar(self, query):
            return None

        def add(self, item):
            self.added.append(item)

    app_config = SimpleNamespace(id=uuid4(), company_id=uuid4())
    token_response = {
        "code": 0,
        "data": {
            "access_token": "u-xxx",
            "refresh_token": "r-xxx",
            "expires_in": 7200,
            "refresh_expires_in": 2592000,
            "open_id": "ou_x",
            "union_id": "onion_x",
        },
    }
    user_info = {
        "code": 0,
        "data": {
            "name": "陈俊",
            "email": "jun.chen@example.com",
            "open_id": "ou_x",
            "union_id": "onion_x",
        },
    }

    account = upsert_feishu_user_account(
        FakeDb(),
        app_config=app_config,
        token_response=token_response,
        user_info_response=user_info,
    )
    resource = upsert_feishu_user_identity_resource(FakeDb(), account=account, app_config=app_config)
    payload = sanitized_feishu_user_account(account)

    assert account.provider == "feishu_user"
    assert account.account_type == "personal_feishu_user"
    assert account.external_account_id == "onion_x"
    assert account.display_name == "陈俊"
    assert account.credentials["access_token"] == "u-xxx"
    assert resource.resource_id == "feishu:user:onion_x"
    assert resource.sources[0].source_type == "feishu_user_identity"
    assert resource.sources[0].source_account_id == str(account.id)
    assert resource.sources[0].settings["account_type"] == "personal_feishu_user"
    assert payload["account_type"] == "personal_feishu_user"
    assert "access_token" not in json.dumps(payload)
    assert payload["token_expires_at"]


def test_feishu_user_oauth_source_type_is_preserved_for_discovered_resources() -> None:
    assert _source_type_for_feishu_item({"settings": {"source": "feishu_user_oauth"}}) == "feishu_user_identity"
    assert _source_type_for_feishu_item({"settings": {"source": "feishu_api"}}) == "feishu_app_identity"


def test_user_drive_discovery_requires_feishu_user_account() -> None:
    class ScalarResult:
        def all(self):
            return []

    class FakeDb:
        def scalars(self, query):
            return ScalarResult()

    items, errors = asyncio.run(
        _discover_user_drive_files(
            FakeDb(),
            app_config=SimpleNamespace(company_id=uuid4()),
            client=SimpleNamespace(),
            limit=20,
        )
    )

    assert items == []
    assert errors[0]["kind"] == "feishu_user"
    assert "用户级能力包授权" in errors[0]["error"]


def test_feishu_client_routes_stable_apis_to_sdk_and_advanced_to_raw() -> None:
    assert route_for_feishu_api("/open-apis/im/v1/messages").mode == FeishuClientMode.SDK
    assert route_for_feishu_api("/open-apis/contact/v3/users/find_by_department").mode == FeishuClientMode.SDK
    assert route_for_feishu_api("/open-apis/calendar/v4/calendars").mode == FeishuClientMode.SDK
    assert route_for_feishu_api("/open-apis/vc/v1/meetings").mode == FeishuClientMode.SDK

    assert route_for_feishu_api("/open-apis/wiki/v2/spaces").mode == FeishuClientMode.RAW_HTTP
    assert route_for_feishu_api("/open-apis/bitable/v1/apps").mode == FeishuClientMode.RAW_HTTP
    assert route_for_feishu_api("/open-apis/approval/v4/instances").mode == FeishuClientMode.RAW_HTTP
    assert route_for_feishu_api("/open-apis/new/v1/future").mode == FeishuClientMode.RAW_HTTP


def test_feishu_client_exposes_sdk_and_raw_layers() -> None:
    app_config = SimpleNamespace(app_id="cli_xxx", app_secret="secret")
    client = FeishuClient(app_config)

    assert hasattr(client, "sdk")
    assert hasattr(client, "raw")
    assert client.sdk.available is True
    assert client.route_for("im").mode == FeishuClientMode.SDK


def test_feishu_approval_service_prefers_registered_resources() -> None:
    class ScalarResult:
        def __init__(self, items):
            self.items = items

        def all(self):
            return self.items

    class FakeDb:
        def scalars(self, query):
            return ScalarResult(
                [
                    SimpleNamespace(
                        resource_id="AE296D4C-57FA-4A96-9B[PHONE_REDACTED]A36",
                        resource_name="污染审批",
                    ),
                    SimpleNamespace(
                        resource_id="approval_xxx",
                        resource_name="付款审批",
                    )
                ]
            )

    service = FeishuApprovalService(SimpleNamespace(company_id=uuid4(), app_id="cli_xxx", app_secret="secret"))

    resources = service.list_approval_resources(FakeDb())

    assert resources[0].approval_code == "approval_xxx"
    assert resources[0].approval_name == "付款审批"
    assert resources[0].source == "resources"
    assert len(resources) == 1


def test_feishu_approval_service_migrates_legacy_resources(monkeypatch) -> None:
    company_id = uuid4()
    app_config = SimpleNamespace(id=uuid4(), company_id=company_id, app_id="cli_xxx", app_secret="secret")
    legacy_id = uuid4()
    v5_id = uuid4()
    migrated = {}

    class ScalarResult:
        def __init__(self, items):
            self.items = items

        def all(self):
            return self.items

    class FakeDb:
        def __init__(self):
            self.results = [ScalarResult([])]

        def scalars(self, query):
            return self.results.pop(0)

    def fake_migrate(db, *, app_config, resource_type, limit=None):
        migrated["db"] = db
        migrated["app_config"] = app_config
        migrated["resource_type"] = resource_type
        migrated["limit"] = limit
        return [
            LegacyFeishuResourceMigration(
                legacy_id=legacy_id,
                resource_type="approval_code",
                external_id="approval_xxx",
                name="付款审批",
                settings={"source": "legacy_feishu_resources"},
                resource=SimpleNamespace(id=v5_id),
            )
        ]

    monkeypatch.setattr("app.services.feishu.approval.migrate_legacy_feishu_resources", fake_migrate)
    db = FakeDb()
    service = FeishuApprovalService(app_config)

    resources = service.list_approval_resources(db)

    assert resources[0].approval_code == "approval_xxx"
    assert resources[0].approval_name == "付款审批"
    assert resources[0].source == "resources_migrated"
    assert resources[0].resource_id == str(v5_id)
    assert resources[0].legacy_resource_id == str(legacy_id)
    assert migrated["app_config"] is app_config
    assert migrated["resource_type"] == "approval_code"


def test_list_approval_resources_returns_v5_resource_ids(monkeypatch) -> None:
    app_config_id = uuid4()
    app_config = SimpleNamespace(id=app_config_id, company_id=uuid4())
    v5_id = uuid4()
    legacy_id = uuid4()

    class FakeApprovalService:
        def __init__(self, received_app_config):
            assert received_app_config is app_config

        def list_approval_resources(self, db):
            return [
                SimpleNamespace(
                    approval_code="approval_xxx",
                    approval_name="付款审批",
                    source="resources",
                    resource_id=str(v5_id),
                    legacy_resource_id=str(legacy_id),
                )
            ]

    monkeypatch.setattr(
        "app.api.routes.feishu_admin_capability_approval_resource_routes.get_feishu_app_or_404",
        lambda db, received_id: app_config,
    )
    monkeypatch.setattr("app.services.feishu_admin_capabilities.FeishuApprovalService", FakeApprovalService)

    result = list_approval_resources(app_config_id, db=SimpleNamespace())

    assert result["items"] == [
        {
            "approval_code": "approval_xxx",
            "approval_name": "付款审批",
            "source": "resources",
            "resource_id": str(v5_id),
            "legacy_resource_id": str(legacy_id),
        }
    ]


def test_feishu_im_extracts_and_dedupes_chat_items() -> None:
    items = extract_chat_items(
        {
            "items": [
                {"chat_id": "oc_1", "name": "销售群"},
                {"open_chat_id": "oc_2", "chat_name": "研发群"},
                {"chat_id": "oc_1", "name": "重复销售群"},
            ]
        }
    )

    deduped = dedupe_chat_candidates(items)

    assert [item["chat_id"] for item in deduped] == ["oc_1", "oc_2"]
    assert deduped[0]["name"] == "销售群"


def test_feishu_contact_snapshot_merges_user_department_names() -> None:
    class FakeClient:
        async def api_get(self, path, params=None):
            if path.endswith("/departments/0/children"):
                return {"data": {"items": [{"department_id": "od_sales", "name": "销售部"}]}}
            if path.endswith("/departments/od_sales/children"):
                return {"data": {"items": []}}
            assert path == "/open-apis/contact/v3/users/find_by_department"
            if params["department_id"] == "od_sales":
                return {"data": {"items": [{"open_id": "ou_1", "name": "张三"}]}}
            return {"data": {"items": []}}

    result = __import__("asyncio").run(
        FeishuContactService(SimpleNamespace(company_id=uuid4()), client=FakeClient()).snapshot_organization()
    )

    assert result["department_count"] == 1
    assert result["user_count"] == 1
    assert result["departments"][0]["path_names"] == ["销售部"]
    assert result["departments"][0]["path_source_department_ids"] == ["od_sales"]
    assert result["users"][0]["department_names"] == ["销售部"]


def test_feishu_contact_snapshot_builds_department_paths_and_membership_metadata() -> None:
    departments = [
        {"department_id": "od_engineering", "parent_department_id": "0", "name": "工程中心"},
        {"department_id": "od_hardware", "parent_department_id": "od_engineering", "name": "硬件部"},
    ]
    paths = build_department_paths(departments)

    merged = merge_user_department_membership(
        existing={},
        user={
            "open_id": "ou_max",
            "name": "戴留兴",
            "department_ids": ["od_engineering", "od_hardware"],
            "orders": [
                {
                    "department_id": "od_hardware",
                    "is_primary_dept": True,
                    "department_order": 2,
                    "user_order": 10,
                },
                {
                    "department_id": "od_engineering",
                    "is_primary_dept": False,
                    "department_order": 1,
                    "user_order": 20,
                },
            ],
        },
        department_id="od_hardware",
        department_names_by_id={"od_engineering": "工程中心", "od_hardware": "硬件部"},
        department_paths=paths,
    )

    assert paths["od_hardware"] == {"names": ["工程中心", "硬件部"], "ids": ["od_engineering", "od_hardware"]}
    assert merged["department_ids"] == ["od_hardware", "od_engineering"]
    assert merged["department_names"] == ["硬件部", "工程中心"]
    assert merged["department_paths"][0] == {"names": ["工程中心", "硬件部"], "ids": ["od_engineering", "od_hardware"]}
    assert merged["orders_by_department_id"]["od_hardware"]["is_primary_dept"] is True


def test_feishu_calendar_service_lists_primary_events_with_epoch_seconds() -> None:
    calls = {}

    class FakeClient:
        async def api_get(self, path, params=None):
            calls["path"] = path
            calls["params"] = params
            return {"data": {"items": []}}

    result = __import__("asyncio").run(
        FeishuCalendarService(SimpleNamespace(company_id=uuid4()), client=FakeClient()).list_primary_events(
            start_time=datetime(2026, 6, 10, 0, 0, tzinfo=UTC),
            end_time=datetime(2026, 6, 11, 0, 0, tzinfo=UTC),
            page_size=80,
        )
    )

    assert result["data"]["items"] == []
    assert calls["path"] == "/open-apis/calendar/v4/calendars/primary/events"
    assert calls["params"]["start_time"] == "1781049600"
    assert calls["params"]["end_time"] == "1781136000"
    assert calls["params"]["page_size"] == 80


def test_feishu_meeting_service_lists_meetings_with_status() -> None:
    calls = {}

    class FakeClient:
        async def api_get(self, path, params=None):
            calls["path"] = path
            calls["params"] = params
            return {"data": {"meeting_list": []}}

    result = __import__("asyncio").run(
        FeishuMeetingService(SimpleNamespace(company_id=uuid4()), client=FakeClient()).list_meetings(
            start_time=datetime(2026, 6, 9, 0, 0, tzinfo=UTC),
            end_time=datetime(2026, 6, 10, 0, 0, tzinfo=UTC),
            meeting_status=2,
            page_size=20,
        )
    )

    assert result["data"]["meeting_list"] == []
    assert calls["path"] == "/open-apis/vc/v1/meeting_list"
    assert calls["params"]["meeting_status"] == 2
    assert calls["params"]["user_id_type"] == "open_id"
    assert calls["params"]["start_time"] == "1780963200"


def test_feishu_drive_service_lists_files_and_wiki_nodes() -> None:
    calls = []

    class FakeClient:
        async def api_get(self, path, params=None):
            calls.append((path, params))
            return {"data": {"items": []}}

    service = FeishuDriveService(SimpleNamespace(company_id=uuid4()), client=FakeClient())
    __import__("asyncio").run(service.list_files(page_size=20, folder_token="fld_x"))
    __import__("asyncio").run(service.list_wiki_nodes(space_id="spc_x", parent_node_token="nod_x"))

    assert calls[0] == ("/open-apis/drive/v1/files", {"page_size": 20, "folder_token": "fld_x"})
    assert calls[1][0] == "/open-apis/wiki/v2/spaces/spc_x/nodes"
    assert calls[1][1]["parent_node_token"] == "nod_x"


def test_feishu_drive_service_gets_docx_content() -> None:
    calls = {}

    class FakeClient:
        async def api_get(self, path, params=None):
            calls["path"] = path
            return {"data": {"raw_content": "会议纪要正文"}}

    result = __import__("asyncio").run(
        FeishuDriveService(SimpleNamespace(company_id=uuid4()), client=FakeClient()).get_document_content(
            document_id="doccn_x",
            document_type="docx",
        )
    )

    assert calls["path"] == "/open-apis/docx/v1/documents/doccn_x/raw_content"
    assert result["content_text"] == "会议纪要正文"


def test_feishu_drive_service_downloads_file_content_with_fallback() -> None:
    calls = []

    class FakeClient:
        async def download_binary(self, path, params=None):
            calls.append(path)
            if path.startswith("/open-apis/drive/v1/medias/"):
                raise RuntimeError("media download unavailable")
            return b"PDF bytes", "application/pdf"

    data, content_type = __import__("asyncio").run(
        FeishuDriveService(SimpleNamespace(company_id=uuid4()), client=FakeClient()).download_file_content(
            file_token="file_x",
        )
    )

    assert calls == [
        "/open-apis/drive/v1/medias/file_x/download",
        "/open-apis/drive/v1/files/file_x/download",
    ]
    assert data == b"PDF bytes"
    assert content_type == "application/pdf"


def test_sync_feishu_document_content_marks_hot_knowledge_metadata(monkeypatch) -> None:
    captured = {}

    class FakeDb:
        def commit(self):
            captured["committed"] = True

    class FakeClient:
        def __init__(self, app_config):
            self.app_config = app_config

        async def api_get(self, path, params=None):
            captured["path"] = path
            return {"data": {"raw_content": "高频制度正文"}}

    def fake_upsert_work_event(db, data):
        captured["event"] = data
        captured["event_id"] = uuid4()
        return SimpleNamespace(id=captured["event_id"])

    monkeypatch.setattr("app.services.feishu.sync.FeishuClient", FakeClient)
    monkeypatch.setattr("app.services.feishu.sync.start_sync_run", lambda *args, **kwargs: SimpleNamespace(id=uuid4()))
    monkeypatch.setattr("app.services.feishu.sync.finish_sync_run", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.feishu.sync.upsert_work_event", fake_upsert_work_event)
    monkeypatch.setattr(
        "app.services.feishu.sync.extract_items_for_event",
        lambda db, event: captured.setdefault("extracted", True),
    )

    resource_id = uuid4()
    result = asyncio.run(
        sync_feishu_information(
            FakeDb(),
            SimpleNamespace(company_id=uuid4()),
            kinds=["docx"],
            limit=1,
            document_id="doccn_hot",
            document_type="docx",
            extract_items=True,
            sync_context={
                "resource_id": str(resource_id),
                "resource_name": "高频制度文档",
                "resource_type": "doc",
                "data_layer": "knowledge_hot",
                "sync_action": "knowledge_vectorize",
                "query_path": "local_rag",
                "vectorize": "full_chunk_or_summary_chunk",
            },
        )
    )

    event = captured["event"]
    assert captured["path"] == "/open-apis/docx/v1/documents/doccn_hot/raw_content"
    assert result["docx"]["available"] is True
    assert result["docx"]["data_layer"] == "knowledge_hot"
    assert result["docx"]["query_path"] == "local_rag"
    assert result["docx"]["sync_action"] == "knowledge_vectorize"
    assert result["docx"]["vectorize"] == "full_chunk_or_summary_chunk"
    assert result["docx"]["rag_indexing"] == {
        "document_store": "work_events",
        "chunking": "queued",
        "chunk_count": "1",
        "vector_db": "qdrant_vectors",
        "rag_index": "pending",
        "vector_status": "pending",
        "vectorize": "full_chunk_or_summary_chunk",
    }
    assert result["docx"]["document_store"] == {
        "store": "work_events",
        "work_event_ids": [str(captured["event_id"])],
        "chunk_count": 1,
        "vector_db": "qdrant_vectors",
        "rag_index": "pending",
        "vector_status": "pending",
    }
    assert result["docx"]["chunk_count"] == 1
    assert event.business_domain == "knowledge"
    assert event.resource_id == resource_id
    assert event.title == "高频制度文档"
    assert "knowledge_hot" in event.labels
    assert event.payload["data_layer"] == "knowledge_hot"
    assert event.payload["query_path"] == "local_rag"
    assert event.payload["vectorize"] == "full_chunk_or_summary_chunk"
    assert event.payload["rag_indexing"]["document_store"] == "work_events"
    assert event.payload["rag_indexing"]["chunking"] == "queued"
    assert event.payload["rag_indexing"]["chunk_count"] == "1"
    assert event.payload["rag_indexing"]["vector_db"] == "qdrant_vectors"
    assert event.payload["rag_indexing"]["rag_index"] == "pending"
    assert event.payload["document_chunks"][0]["text"] == "高频制度正文"
    assert event.payload["document_chunks"][0]["chunk_id"] == "chunk-1"
    assert event.payload["sync_context"]["sync_action"] == "knowledge_vectorize"
    assert captured["extracted"] is True
    assert captured["committed"] is True


def test_sync_feishu_meetings_keeps_official_min_page_size(monkeypatch) -> None:
    captured = {}

    class FakeClient:
        def __init__(self, app_config):
            self.app_config = app_config

        async def api_get(self, path, params=None):
            captured["path"] = path
            captured["params"] = params
            return {
                "data": {
                    "meeting_list": [{"meeting_id": "123456789", "meeting_topic": "经营例会"}],
                    "has_more": False,
                }
            }

    def fake_upsert_work_event(db, data):
        captured["event"] = data
        return SimpleNamespace(id=uuid4())

    monkeypatch.setattr("app.services.feishu.sync.FeishuClient", FakeClient)
    monkeypatch.setattr("app.services.feishu.sync.start_sync_run", lambda *args, **kwargs: SimpleNamespace(id=uuid4()))
    monkeypatch.setattr("app.services.feishu.sync.finish_sync_run", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.feishu.sync.upsert_work_event", fake_upsert_work_event)

    result = asyncio.run(
        sync_feishu_information(
            SimpleNamespace(commit=lambda: None),
            SimpleNamespace(company_id=uuid4()),
            kinds=["meetings"],
            limit=3,
            max_pages=1,
        )
    )

    assert captured["path"] == "/open-apis/vc/v1/meeting_list"
    assert captured["params"]["page_size"] == 20
    assert result["meetings"]["saved_count"] == 1


def test_sync_feishu_wiki_document_content_resolves_node_for_hot_knowledge(monkeypatch) -> None:
    captured = {"calls": []}

    class FakeDb:
        def commit(self):
            captured["committed"] = True

    class FakeClient:
        def __init__(self, app_config):
            self.app_config = app_config

        async def api_get(self, path, params=None):
            captured["calls"].append((path, params or {}))
            if path == "/open-apis/wiki/v2/spaces/get_node":
                return {
                    "data": {
                        "node": {
                            "node_token": "wikcn_hot",
                            "obj_token": "doccn_hot",
                            "obj_type": "docx",
                            "space_id": "spc_1",
                            "title": "高频 Wiki 制度",
                        }
                    }
                }
            return {"data": {"raw_content": "Wiki 高频制度正文"}}

    def fake_upsert_work_event(db, data):
        captured["event"] = data
        return SimpleNamespace(id=uuid4())

    monkeypatch.setattr("app.services.feishu.sync.FeishuClient", FakeClient)
    monkeypatch.setattr("app.services.feishu.sync.start_sync_run", lambda *args, **kwargs: SimpleNamespace(id=uuid4()))
    monkeypatch.setattr("app.services.feishu.sync.finish_sync_run", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.feishu.sync.upsert_work_event", fake_upsert_work_event)

    resource_id = uuid4()
    result = asyncio.run(
        sync_feishu_information(
            FakeDb(),
            SimpleNamespace(company_id=uuid4()),
            kinds=["wiki"],
            limit=1,
            document_id="wikcn_hot",
            document_type="wiki",
            extract_items=False,
            sync_context={
                "resource_id": str(resource_id),
                "resource_name": "高频 Wiki 制度",
                "resource_type": "doc",
                "data_layer": "knowledge_hot",
                "sync_action": "knowledge_vectorize",
                "query_path": "local_rag",
                "vectorize": "full_chunk_or_summary_chunk",
            },
        )
    )

    event = captured["event"]
    assert captured["calls"] == [
        ("/open-apis/wiki/v2/spaces/get_node", {"token": "wikcn_hot"}),
        ("/open-apis/docx/v1/documents/doccn_hot/raw_content", {}),
    ]
    assert result["wiki"]["available"] is True
    assert result["wiki"]["resolved_document_id"] == "doccn_hot"
    assert result["wiki"]["resolved_document_type"] == "docx"
    assert result["wiki"]["chunk_count"] == 1
    assert event.event_type == "feishu.wiki.content"
    assert event.external_id == "wiki:wikcn_hot"
    assert event.resource_id == resource_id
    assert event.payload["kind"] == "wiki"
    assert event.payload["document_id"] == "wikcn_hot"
    assert event.payload["resolved_document_id"] == "doccn_hot"
    assert event.payload["resolved_document_type"] == "docx"
    assert event.payload["wiki_node"]["space_id"] == "spc_1"
    assert event.payload["document_chunks"][0]["text"] == "Wiki 高频制度正文"
    assert captured["committed"] is True


def test_sync_feishu_document_content_rejects_cold_knowledge_full_sync(monkeypatch) -> None:
    captured = {}

    class FakeDb:
        def commit(self):
            captured["committed"] = True

    class FakeClient:
        def __init__(self, app_config):
            raise AssertionError("cold knowledge document sync must not call Feishu API")

    monkeypatch.setattr("app.services.feishu.sync.FeishuClient", FakeClient)
    monkeypatch.setattr("app.services.feishu.sync.start_sync_run", lambda *args, **kwargs: SimpleNamespace(id=uuid4()))
    monkeypatch.setattr("app.services.feishu.sync.finish_sync_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "app.services.feishu.sync.upsert_work_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("cold knowledge must not write content event")),
    )

    result = asyncio.run(
        sync_feishu_information(
            FakeDb(),
            SimpleNamespace(company_id=uuid4()),
            kinds=["docx"],
            limit=1,
            document_id="doccn_cold",
            document_type="docx",
            extract_items=True,
            sync_context={
                "resource_id": str(uuid4()),
                "resource_name": "历史归档文档",
                "resource_type": "doc",
                "data_layer": "knowledge_cold",
                "sync_action": "document_index_only",
                "query_path": "lark_cli_realtime",
                "vectorize": "outline_and_summary_only",
            },
        )
    )

    assert result["docx"]["available"] is False
    assert result["docx"]["saved_count"] == 0
    assert result["docx"]["data_layer"] == "knowledge_cold"
    assert result["docx"]["query_path"] == "lark_cli_realtime"
    assert result["docx"]["sync_action"] == "document_index_only"
    assert "only allowed for L1 hot knowledge_vectorize" in result["docx"]["error"]
    assert captured["committed"] is True


def test_feishu_bitable_service_lists_tables_and_records() -> None:
    calls = []

    class FakeClient:
        async def api_get(self, path, params=None):
            calls.append((path, params))
            return {"data": {"items": []}}

    service = FeishuBitableService(SimpleNamespace(company_id=uuid4()), client=FakeClient())
    __import__("asyncio").run(service.list_tables(app_token="bascn_x", page_size=50))
    __import__("asyncio").run(
        service.list_records(app_token="bascn_x", table_id="tbl_x", field_names=["客户", "金额"])
    )

    assert calls[0] == ("/open-apis/bitable/v1/apps/bascn_x/tables", {"page_size": 50})
    assert calls[1][0] == "/open-apis/bitable/v1/apps/bascn_x/tables/tbl_x/records"
    assert calls[1][1]["field_names"] == "客户,金额"


def test_bitable_record_sync_keeps_master_data_index_only() -> None:
    company_id = uuid4()
    event = _bitable_record_to_index_event(
        SimpleNamespace(company_id=company_id),
        app_token="bascn_x",
        table_id="tbl_x",
        item={
            "record_id": "rec_x",
            "fields": {
                "客户名称": "固势客户A",
                "项目状态": "进行中",
                "内部长备注": "x" * 500,
            },
        },
    )

    assert event.company_id == company_id
    assert event.event_type == "feishu.bitable.master_data_index"
    assert event.external_id == "bitable:bascn_x:tbl_x:rec_x"
    assert event.payload["data_layer"] == "master_data_index"
    assert event.payload["sync_action"] == "master_data_index"
    assert event.payload["query_path"] == "bitable_api"
    assert event.payload["mode"] == "master_data_index"
    assert event.payload["record_id"] == "rec_x"
    assert event.payload["key_fields"] == {"客户名称": "固势客户A", "项目状态": "进行中"}
    assert "item" not in event.payload
    assert "内部长备注" in event.payload["field_names"]
    assert "x" * 200 not in event.content_text


def test_feishu_task_service_lists_tasks() -> None:
    calls = {}

    class FakeClient:
        async def api_get(self, path, params=None):
            calls["path"] = path
            calls["params"] = params
            return {"data": {"items": [{"guid": "task_1"}]}}

    result = __import__("asyncio").run(
        FeishuTaskService(SimpleNamespace(company_id=uuid4()), client=FakeClient()).list_tasks(page_size=30)
    )

    assert extract_task_items(result["data"]) == [{"guid": "task_1"}]
    assert calls["path"] == "/open-apis/task/v2/tasks"
    assert calls["params"] == {"page_size": 30}


def test_feishu_mail_service_lists_folders_messages_and_detail() -> None:
    calls = []

    class FakeClient:
        async def api_get(self, path, params=None):
            calls.append((path, params))
            return {"data": {"items": []}}

    service = FeishuMailService(SimpleNamespace(company_id=uuid4()), client=FakeClient())
    __import__("asyncio").run(service.list_folders(user_mailbox_id="jun.chen@gaustek.com"))
    __import__("asyncio").run(
        service.list_messages(user_mailbox_id="jun.chen@gaustek.com", folder_id="INBOX", page_size=10)
    )
    __import__("asyncio").run(
        service.get_message_detail(user_mailbox_id="jun.chen@gaustek.com", message_id="msg_1")
    )

    assert calls[0][0] == "/open-apis/mail/v1/user_mailboxes/jun.chen%40gaustek.com/folders"
    assert calls[1] == (
        "/open-apis/mail/v1/user_mailboxes/jun.chen%40gaustek.com/messages",
        {"folder_id": "INBOX", "page_size": 10},
    )
    assert calls[2] == (
        "/open-apis/mail/v1/user_mailboxes/jun.chen%40gaustek.com/messages/msg_1",
        {"format": "plain_text_full"},
    )


def test_feishu_discovered_resource_spec_maps_resource_registry_fields() -> None:
    mail = feishu_discovered_resource_spec(
        {
            "resource_type": "mail_folder",
            "external_id": "jun.chen@gaustek.com:INBOX",
            "name": "Inbox",
            "settings": {"user_mailbox_id": "jun.chen@gaustek.com", "folder_id": "INBOX"},
        }
    )
    bitable = feishu_discovered_resource_spec(
        {
            "resource_type": "bitable_table",
            "external_id": "bascn_x:tbl_x",
            "name": "客户表",
            "settings": {"app_token": "bascn_x", "table_id": "tbl_x"},
        }
    )

    assert mail["resource_id"] == "jun.chen@gaustek.com"
    assert mail["resource_sub_id"] == "INBOX"
    assert mail["sync_mode"] == "scheduled"
    assert mail["permission_level"] == "owner"
    assert mail["data_classification"] == "personal"
    assert mail["business_domain"] == "communications"
    assert bitable["resource_id"] == "bascn_x"
    assert bitable["resource_sub_id"] == "tbl_x"
    assert bitable["data_classification"] == "company"
    assert bitable["business_domain"] == "operations"


def test_feishu_discovered_resource_spec_maps_capabilities_and_wiki() -> None:
    capability = feishu_discovered_resource_spec(
        {
            "resource_type": "capability",
            "external_id": "feishu:contacts",
            "name": "通讯录与组织架构",
            "settings": {"sync_mode": "scheduled", "permission_level": "company"},
        }
    )
    wiki = feishu_discovered_resource_spec(
        {
            "resource_type": "wiki_space",
            "external_id": "spc_x",
            "name": "企业知识库",
            "settings": {},
        }
    )

    assert capability["resource_id"] == "feishu:contacts"
    assert capability["sync_mode"] == "scheduled"
    assert capability["permission_level"] == "company"
    assert wiki["resource_id"] == "spc_x"
    assert wiki["sync_mode"] == "manual_or_scheduled"
    assert wiki["permission_level"] == "department"
    assert wiki["data_classification"] == "company"
    assert wiki["business_domain"] == "knowledge"


def test_feishu_resource_discovery_defaults_cover_v5_core_modules() -> None:
    selected = _normalize_discovery_kinds(DEFAULT_FEISHU_RESOURCE_KINDS)

    assert {"contacts", "calendar", "meetings", "tasks", "chats", "drive", "wiki", "bitable", "approvals"}.issubset(
        selected
    )
    assert "mail" not in selected
    assert _normalize_discovery_kinds(["doc", "meeting", "task", "directory", "approval"]) == {
        "drive",
        "meetings",
        "tasks",
        "contacts",
        "approvals",
    }


def test_explicit_approval_resources_dedupe_codes() -> None:
    items = _explicit_approval_resources([" approval_x ", "", "approval_x", "approval_y"])

    assert [(item["resource_type"], item["external_id"]) for item in items] == [
        ("approval_code", "approval_x"),
        ("approval_code", "approval_y"),
    ]
    assert items[0]["settings"]["source"] == "explicit_config"


def test_explicit_bitable_document_and_wiki_resources() -> None:
    bitable = _explicit_bitable_table_resources(["bascnAPP123456:tblTABLE123456", "bad"])
    docs = _explicit_document_resources(["doccnDOC123456", "shtcnSHEET123456"])
    wiki = _explicit_wiki_resources(["spcSPACE123456"])
    folders = _explicit_folder_resources(["fldFOLDER123456"])

    assert bitable[0]["resource_type"] == "bitable_table"
    assert bitable[0]["external_id"] == "bascnAPP123456:tblTABLE123456"
    assert bitable[0]["settings"]["app_token"] == "bascnAPP123456"
    assert bitable[0]["settings"]["table_id"] == "tblTABLE123456"
    assert docs[0]["resource_type"] == "drive_file"
    assert docs[0]["settings"]["document_type"] == "docx"
    assert docs[1]["settings"]["document_type"] == "sheet"
    assert wiki[0]["resource_type"] == "wiki_space"
    assert folders[0]["resource_type"] == "drive_folder"
    assert folders[0]["settings"]["folder_token"] == "fldFOLDER123456"


def test_extract_resource_candidates_from_text_discovers_feishu_links_and_tokens() -> None:
    text = (
        "客户表 https://gaustek.feishu.cn/base/bascnAPP123456?table=tblTABLE123456 "
        "方案文档 doccnDOC123456 知识库 spcSPACE123456 "
        "文件夹 https://gaustek.feishu.cn/drive/folder/fldFOLDER123456"
    )

    candidates = extract_resource_candidates_from_text(text, source_work_event_id="event-1")
    by_type = {(item["resource_type"], item["external_id"]) for item in candidates}

    assert ("bitable_app", "bascnAPP123456") in by_type
    assert ("bitable_table", "bascnAPP123456:tblTABLE123456") in by_type
    assert ("drive_file", "doccnDOC123456") in by_type
    assert ("wiki_space", "spcSPACE123456") in by_type
    assert ("drive_folder", "fldFOLDER123456") in by_type


def test_resource_discover_request_accepts_deep_resource_identifiers() -> None:
    request = FeishuResourceDiscoverRequest(
        bitable_tables=["bascnAPP123456:tblTABLE123456"],
        document_ids=["doccnDOC123456"],
        wiki_space_ids=["spcSPACE123456"],
        folder_tokens=["fldFOLDER123456"],
        docs_search_keywords=["经营分析"],
    )

    assert request.bitable_tables == ["bascnAPP123456:tblTABLE123456"]
    assert request.document_ids == ["doccnDOC123456"]
    assert request.wiki_space_ids == ["spcSPACE123456"]
    assert request.folder_tokens == ["fldFOLDER123456"]
    assert request.docs_search_keywords == ["经营分析"]


def test_docs_search_resources_registers_bitable_as_drive_file_and_bitable_app() -> None:
    resources = _docs_search_resources(
        [
            {
                "docs_token": "bascnTOKEN123",
                "docs_type": "bitable",
                "title": "经营分析",
                "owner_id": "ou_1",
            },
            {
                "docs_token": "docxTOKEN123",
                "docs_type": "docx",
                "title": "会议纪要",
            },
        ],
        keyword="经营分析",
    )
    by_type = {(item["resource_type"], item["external_id"]) for item in resources}

    assert ("drive_file", "bascnTOKEN123") in by_type
    assert ("bitable_app", "bascnTOKEN123") in by_type
    assert ("drive_file", "docxTOKEN123") in by_type
    assert all(item["settings"]["source"] == "feishu_docs_search" for item in resources)


def test_drive_folder_tree_discovers_children_and_nested_folders() -> None:
    calls = []

    class FakeClient:
        async def api_get(self, path, params=None):
            calls.append((path, params))
            if params["folder_token"] == "fldROOT123456":
                return {
                    "data": {
                        "files": [
                            {"token": "fldCHILD123456", "type": "folder", "name": "项目资料"},
                            {"token": "bascnAPP123456", "type": "bitable", "name": "经营分析"},
                        ]
                    }
                }
            if params["folder_token"] == "fldCHILD123456":
                return {"data": {"files": [{"token": "docxDOC123456", "type": "docx", "name": "项目纪要"}]}}
            return {"data": {"files": []}}

    resources, errors = __import__("asyncio").run(
        _discover_drive_folder_tree(FakeClient(), folder_tokens=["fldROOT123456"], limit=20, max_depth=2)
    )
    by_type = {(item["resource_type"], item["external_id"]) for item in resources}

    assert errors == []
    assert ("drive_folder", "fldROOT123456") in by_type
    assert ("drive_folder", "fldCHILD123456") in by_type
    assert ("bitable_app", "bascnAPP123456") in by_type
    assert ("drive_file", "docxDOC123456") in by_type
    assert calls[0] == ("/open-apis/drive/v1/files", {"page_size": 20, "folder_token": "fldROOT123456"})


def test_folder_tokens_from_v5_resource_reads_configured_settings() -> None:
    tokens = _folder_tokens_from_v5_resource(
        SimpleNamespace(
            resource_id="fldPRIMARY123456",
            resource_sub_id=None,
            config_json={"settings": {"folder_token": "fldSETTINGS123456"}},
        )
    )

    assert tokens == ["fldPRIMARY123456", None, None, "fldSETTINGS123456"]


def test_folder_tokens_from_resources_prefers_v5_and_migrates_legacy_fallback(monkeypatch) -> None:
    legacy_id = uuid4()
    v5_migrated_id = uuid4()
    migrated = {}

    class FakeScalarResult:
        def __init__(self, items):
            self._items = items

        def all(self):
            return self._items

    class FakeDb:
        def __init__(self):
            self.calls = 0

        def scalars(self, query):
            self.calls += 1
            return FakeScalarResult(
                [
                    SimpleNamespace(
                        resource_id="fldV5PRIMARY123456",
                        resource_sub_id=None,
                        config_json={"settings": {"folder_token": "fldV5SETTINGS123456"}},
                    )
                ]
            )

    def fake_migrate(db, *, app_config, resource_type, limit=None):
        migrated["db"] = db
        migrated["app_config"] = app_config
        migrated["resource_type"] = resource_type
        migrated["limit"] = limit
        return [
            LegacyFeishuResourceMigration(
                legacy_id=legacy_id,
                resource_type="drive_folder",
                external_id="fldLEGACY123456",
                name="旧知识库根目录",
                settings={"source": "legacy_feishu_resources", "folder_token": "fldLEGACYSETTINGS123456"},
                resource=SimpleNamespace(id=v5_migrated_id),
            )
        ]

    monkeypatch.setattr("app.services.feishu.resources.migrate_legacy_feishu_resources", fake_migrate)
    app_config = SimpleNamespace(id=uuid4(), company_id=uuid4(), settings={"drive_folder_tokens": ["fldCONFIG123456"]})
    db = FakeDb()
    tokens = _folder_tokens_from_resources(
        db,
        app_config=app_config,
        discovered=[
            {
                "resource_type": "drive_folder",
                "external_id": "fldDISCOVERED123456",
                "settings": {"folder_token": "fldDISCOVERED123456"},
            }
        ],
    )

    assert tokens == [
        "fldDISCOVERED123456",
        "fldV5PRIMARY123456",
        "fldV5SETTINGS123456",
        "fldLEGACY123456",
        "fldLEGACYSETTINGS123456",
        "fldCONFIG123456",
    ]
    assert migrated["db"] is db
    assert migrated["app_config"] is app_config
    assert migrated["resource_type"] == "drive_folder"
    assert migrated["limit"] == 100


def test_resource_discovery_coverage_reports_missing_resource_families() -> None:
    coverage = _resource_coverage(
        {"contacts", "calendar", "chats", "drive", "wiki", "bitable"},
        [
            {"resource_type": "capability", "external_id": "feishu:contacts"},
            {"resource_type": "capability", "external_id": "feishu:calendar"},
            {"resource_type": "chat", "external_id": "oc_1"},
        ],
        [],
    )

    assert coverage["counts"]["contacts"] == 1
    assert coverage["counts"]["calendar"] == 1
    assert coverage["counts"]["chats"] == 1
    assert {"bitable", "drive", "wiki"} <= set(coverage["not_found"])


def test_resource_discovery_coverage_counts_local_mining() -> None:
    coverage = _resource_coverage(
        {"local"},
        [
            {
                "resource_type": "chat",
                "external_id": "oc_1",
                "settings": {"source": "local_work_event"},
            }
        ],
        [],
    )

    assert coverage["counts"]["local"] == 1
    assert coverage["covered"] == ["local"]
    assert coverage["not_found"] == []


def test_unique_strings_keeps_order_and_removes_empty_values() -> None:
    assert _unique_strings(["cli_bot", "", "cli_bot", "cli_other"]) == ["cli_bot", "cli_other"]


def test_auto_join_public_chats_dry_run_searches_candidates() -> None:
    class FakeClient:
        async def api_get(self, path, params=None):
            assert path == "/open-apis/im/v1/chats/search"
            assert params["query"] == "销售"
            return {"data": {"items": [{"chat_id": "oc_1", "name": "销售群"}]}}

    import app.services.feishu_admin_write_tools as write_tools

    original_client = write_tools.FeishuClient
    write_tools.FeishuClient = lambda app_config: FakeClient()
    try:
        result = __import__("asyncio").run(
            _auto_join_public_chats(
                SimpleNamespace(app_id="cli_bot"),
                FeishuAutoJoinPublicChatsRequest(query="销售", dry_run=True),
            )
        )
    finally:
        write_tools.FeishuClient = original_client

    assert result["dry_run"] is True
    assert result["candidate_count"] == 1
    assert result["candidates"][0]["chat_id"] == "oc_1"


def test_auto_join_public_chats_helper_rejects_real_join() -> None:
    calls = []

    class FakeClient:
        async def api_post(self, path, payload=None):
            calls.append((path, payload))
            return {"code": 0}

    import app.services.feishu_admin_write_tools as write_tools

    original_client = write_tools.FeishuClient
    write_tools.FeishuClient = lambda app_config: FakeClient()
    try:
        try:
            __import__("asyncio").run(
                _auto_join_public_chats(
                    SimpleNamespace(app_id="cli_bot"),
                    FeishuAutoJoinPublicChatsRequest(chat_ids=["oc_1"], dry_run=False),
                )
            )
        except ValueError as exc:
            assert "Tool Router" in str(exc)
        else:
            raise AssertionError("expected dry-run helper to reject real join")
    finally:
        write_tools.FeishuClient = original_client

    assert calls == []


def test_auto_join_public_chats_route_dry_run_uses_tool_router_and_searches_candidates(monkeypatch) -> None:
    company_id = uuid4()
    app_config_id = uuid4()
    app_config = SimpleNamespace(id=app_config_id, company_id=company_id, app_id="cli_bot")

    class FakeDb:
        def __init__(self):
            self.added = []
            self.committed = False

        def scalar(self, query):
            return None

        def add(self, item):
            self.added.append(item)

        def commit(self):
            self.committed = True

    class FakeClient:
        async def api_get(self, path, params=None):
            assert path == "/open-apis/im/v1/chats/search"
            assert params["query"] == "销售"
            return {"data": {"items": [{"chat_id": "oc_1", "name": "销售群"}]}}

    monkeypatch.setattr(
        "app.api.routes.feishu_admin_write_im_auto_join_routes.get_feishu_app_or_404",
        lambda db, received_id: app_config,
    )
    monkeypatch.setattr("app.services.feishu_admin_write_tools.FeishuClient", lambda received_config: FakeClient())

    db = FakeDb()
    result = asyncio.run(
        auto_join_public_chats(
            app_config_id,
            FeishuAutoJoinPublicChatsRequest(query="销售", dry_run=True, actor_open_id="ou_admin"),
            db=db,
        )
    )

    assert result["dry_run"] is True
    assert result["candidate_count"] == 1
    assert result["candidates"][0]["chat_id"] == "oc_1"
    assert "confirmation_token:" in result["answer"]
    assert db.committed is True
    assert db.added[0].action == "tool.im.public_chat.auto_join"
    assert db.added[0].payload["write_mode"] == "dry_run"
    assert db.added[1].action == "feishu.chat.public_auto_join"
    assert db.added[1].payload["tool_name"] == "feishu_im_auto_join_public_chats"


def test_auto_join_public_chats_route_rejects_unconfirmed_real_join(monkeypatch) -> None:
    company_id = uuid4()
    app_config_id = uuid4()
    app_config = SimpleNamespace(id=app_config_id, company_id=company_id, app_id="cli_bot")

    class FakeDb:
        def __init__(self):
            self.added = []
            self.committed = False

        def scalar(self, query):
            return None

        def add(self, item):
            self.added.append(item)

        def commit(self):
            self.committed = True

    monkeypatch.setattr(
        "app.api.routes.feishu_admin_write_im_auto_join_routes.get_feishu_app_or_404",
        lambda db, received_id: app_config,
    )

    db = FakeDb()
    result = asyncio.run(
        auto_join_public_chats(
            app_config_id,
            FeishuAutoJoinPublicChatsRequest(chat_ids=["oc_1"], dry_run=False, confirmed=False),
            db=db,
        )
    )

    assert result["ok"] is False
    assert result["status"] == "denied"
    assert "requires dry_run or confirmed=true" in result["error"]
    assert db.committed is True
    assert db.added[0].action == "tool.im.public_chat.auto_join"
    assert db.added[0].payload["write_mode"] == "pending_confirmation"
    assert db.added[1].action == "feishu.chat.public_auto_join"
    assert db.added[1].payload["tool_name"] == "feishu_im_auto_join_public_chats"
    assert "confirmation_token" not in db.added[1].payload["write_target"]["param_keys"]


def test_discovered_resource_payload_uses_v5_resource_as_primary() -> None:
    v5_id = uuid4()
    app_config_id = uuid4()
    legacy_id = uuid4()
    payload = _discovered_resource_payload(
        SimpleNamespace(
            id=v5_id,
            platform="feishu",
            resource_type="bitable_table",
            resource_id="bascn_app",
            resource_sub_id="tbl_table",
            resource_name="客户表",
            sync_mode="scheduled",
            permission_level="department",
            data_classification="company",
            business_domain="sales",
            enabled=True,
            app_config_id=app_config_id,
            config_json={"external_id": "bascn_app:tbl_table", "settings": {"table_id": "tbl_table"}},
        ),
        SimpleNamespace(
            id=legacy_id,
            resource_type="bitable_table",
            external_id="bascn_app:tbl_table",
            name="旧客户表",
            sync_enabled=True,
            settings={"source": "legacy"},
        ),
    )

    assert payload["id"] == str(v5_id)
    assert payload["resource_id"] == "bascn_app"
    assert payload["resource_sub_id"] == "tbl_table"
    assert payload["resource_type"] == "bitable_table"
    assert payload["resource_name"] == "客户表"
    assert payload["external_id"] == "bascn_app:tbl_table"
    assert payload["legacy_id"] == str(legacy_id)
    assert payload["legacy_external_id"] == "bascn_app:tbl_table"
    assert payload["settings"] == {"table_id": "tbl_table"}


def test_discovered_resource_payload_allows_v5_only_resource() -> None:
    v5_id = uuid4()
    payload = _discovered_resource_payload(
        SimpleNamespace(
            id=v5_id,
            platform="feishu",
            resource_type="capability",
            resource_id="feishu:contacts",
            resource_sub_id=None,
            resource_name="通讯录与组织架构",
            sync_mode="scheduled",
            permission_level="company",
            data_classification="company",
            business_domain="administration",
            enabled=True,
            app_config_id=None,
            config_json={
                "external_id": "feishu:contacts",
                "settings": {"source": "capability_registry"},
            },
        )
    )

    assert payload["id"] == str(v5_id)
    assert payload["external_id"] == "feishu:contacts"
    assert payload["legacy_id"] is None
    assert payload["legacy_external_id"] is None
    assert payload["settings"] == {"source": "capability_registry"}


def test_discover_feishu_resources_writes_v5_only_for_new_discovery(monkeypatch) -> None:
    calls = []

    def fake_upsert_discovered_resource(
        db,
        *,
        app_config,
        item,
        legacy_feishu_resource_id=None,
        source_label="feishu_resource_discovery",
    ):
        calls.append(
            {
                "db": db,
                "app_config": app_config,
                "item": item,
                "legacy_feishu_resource_id": legacy_feishu_resource_id,
                "source_label": source_label,
            }
        )
        return SimpleNamespace(
            id=uuid4(),
            platform="feishu",
            resource_type=item["resource_type"],
            resource_id=item["external_id"],
            resource_sub_id=None,
            resource_name=item["name"],
            sync_mode="scheduled",
            permission_level="company",
            data_classification="company",
            business_domain="administration",
            enabled=True,
            app_config_id=app_config.id,
            config_json={"external_id": item["external_id"], "settings": item["settings"]},
        )

    link_saw_flushed = []

    def fake_link_local_work_events_to_resource(db, registered_resource, item):
        link_saw_flushed.append(db.flushed)

    monkeypatch.setattr("app.services.feishu.resources.upsert_feishu_discovered_resource", fake_upsert_discovered_resource)
    monkeypatch.setattr("app.services.feishu.resources._link_local_work_events_to_resource", fake_link_local_work_events_to_resource)
    db = SimpleNamespace(flushed=False, flush=lambda: setattr(db, "flushed", True))
    app_config = SimpleNamespace(id=uuid4(), company_id=uuid4(), app_id="cli_xxx", app_secret="secret")

    result = asyncio.run(
        discover_feishu_resources(
            db,
            app_config,
            kinds=["contacts"],
            include_local_mining=False,
        )
    )

    assert db.flushed is True
    assert link_saw_flushed == [True]
    assert not hasattr(resource_module, "upsert_feishu_resource")
    assert result["saved_count"] == 1
    assert result["items"][0]["legacy_id"] is None
    assert calls[0]["legacy_feishu_resource_id"] is None
    assert calls[0]["source_label"] == "feishu_resource_discovery"


def test_discover_feishu_resources_lists_tables_for_docs_search_bitable(monkeypatch) -> None:
    captured = {}

    async def fake_no_items(*args, **kwargs):
        return [], []

    async def fake_no_items_with_error(*args, **kwargs):
        return [], None

    async def fake_docs_search(*args, **kwargs):
        return [
            {
                "resource_type": "bitable_app",
                "external_id": "bascnSEARCH123456",
                "name": "经营分析",
                "settings": {"source": "feishu_docs_search"},
            }
        ], []

    async def fake_bitable_tables(*args, **kwargs):
        captured["app_tokens"] = kwargs["app_tokens"]
        return [
            {
                "resource_type": "bitable_table",
                "external_id": "bascnSEARCH123456:tblTABLE123456",
                "name": "客户表",
                "settings": {
                    "source": "feishu_api",
                    "app_token": "bascnSEARCH123456",
                    "table_id": "tblTABLE123456",
                },
            }
        ], []

    def fake_upsert_discovered_resource(db, *, app_config, item, **kwargs):
        resource_id = item["external_id"]
        resource_sub_id = None
        if item["resource_type"] == "bitable_table":
            resource_id, resource_sub_id = item["external_id"].split(":", 1)
        return SimpleNamespace(
            id=uuid4(),
            platform="feishu",
            resource_type=item["resource_type"],
            resource_id=resource_id,
            resource_sub_id=resource_sub_id,
            resource_name=item["name"],
            sync_mode="scheduled",
            permission_level="company",
            data_classification="company",
            business_domain="operations",
            enabled=True,
            app_config_id=app_config.id,
            config_json={"external_id": item["external_id"], "settings": item["settings"]},
        )

    monkeypatch.setattr("app.services.feishu.resources._discover_drive_folder_tree", fake_no_items)
    monkeypatch.setattr("app.services.feishu.resources._discover_drive_files", fake_no_items_with_error)
    monkeypatch.setattr("app.services.feishu.resources._discover_docs_by_search", fake_docs_search)
    monkeypatch.setattr("app.services.feishu.resources._discover_user_drive_files", fake_no_items)
    monkeypatch.setattr("app.services.feishu.resources._discover_bitable_tables", fake_bitable_tables)
    monkeypatch.setattr("app.services.feishu.resources._discover_user_bitable_tables", fake_no_items)
    monkeypatch.setattr("app.services.feishu.resources.migrate_legacy_feishu_resources", lambda *args, **kwargs: [])
    monkeypatch.setattr("app.services.feishu.resources.upsert_feishu_discovered_resource", fake_upsert_discovered_resource)

    class EmptyScalarResult:
        def all(self):
            return []

    db = SimpleNamespace(flush=lambda: None, scalars=lambda query: EmptyScalarResult())
    app_config = SimpleNamespace(id=uuid4(), company_id=uuid4(), app_id="cli_xxx", app_secret="secret", settings={})

    result = asyncio.run(
        discover_feishu_resources(
            db,
            app_config,
            kinds=["bitable"],
            include_local_mining=False,
        )
    )

    assert captured["app_tokens"] == ["bascnSEARCH123456"]
    assert {item["resource_type"] for item in result["items"]} == {"bitable_app", "bitable_table"}


def test_extract_resource_candidates_from_payload() -> None:
    payload = {
        "event": {
            "message": {"chat_id": "oc_chat"},
            "bitable": {"app_token": "bascn_app", "table_id": "tbl_table", "table_name": "客户表"},
            "approval": {"approval_code": "approval_xxx", "approval_name": "付款审批"},
            "file": {"file_token": "doccn_file", "type": "docx", "title": "会议纪要"},
        }
    }

    candidates = extract_resource_candidates_from_payload(payload, source_work_event_id="event-1")
    by_type = {(item["resource_type"], item["external_id"]) for item in candidates}

    assert ("chat", "oc_chat") in by_type
    assert ("bitable_app", "bascn_app") in by_type
    assert ("bitable_table", "bascn_app:tbl_table") in by_type
    assert ("approval_code", "approval_xxx") in by_type
    assert ("drive_file", "doccn_file") in by_type


def test_dedupe_discovered_uses_v5_resource_identity_not_source() -> None:
    items = [
        {
            "resource_type": "chat",
            "external_id": "oc_chat",
            "name": "群聊",
            "settings": {"source": "local_work_event"},
        },
        {
            "resource_type": "chat",
            "external_id": "oc_chat",
            "name": "群聊",
            "settings": {"source": "local_work_event_payload"},
        },
        {
            "resource_type": "bitable_table",
            "external_id": "bascn_app:tbl_table",
            "settings": {"source": "explicit_config", "app_token": "bascn_app", "table_id": "tbl_table"},
        },
        {
            "resource_type": "bitable_table",
            "external_id": "bascn_app:tbl_table",
            "settings": {"source": "feishu_api", "app_token": "bascn_app", "table_id": "tbl_table"},
        },
    ]

    deduped = _dedupe_discovered(items)

    assert [(item["resource_type"], item["external_id"]) for item in deduped] == [
        ("chat", "oc_chat"),
        ("bitable_table", "bascn_app:tbl_table"),
    ]


def test_local_chat_resource_discovery_links_existing_work_events() -> None:
    class FakeDb:
        def __init__(self) -> None:
            self.statements = []

        def execute(self, statement):
            self.statements.append(statement)

    resource = type(
        "ResourceStub",
        (),
        {
            "id": uuid4(),
            "company_id": uuid4(),
            "resource_type": "chat",
            "resource_id": "oc_chat",
        },
    )()
    db = FakeDb()

    _link_local_work_events_to_resource(
        db,
        resource,
        {"resource_type": "chat", "external_id": "oc_chat", "settings": {"source": "local_work_event"}},
    )

    assert len(db.statements) == 1
    statement_text = str(db.statements[0])
    assert "UPDATE work_events" in statement_text
    assert "work_events.thread_id = :thread_id_1" in statement_text


def test_non_local_resource_discovery_does_not_link_work_events() -> None:
    class FakeDb:
        def __init__(self) -> None:
            self.statements = []

        def execute(self, statement):
            self.statements.append(statement)

    resource = type(
        "ResourceStub",
        (),
        {
            "id": uuid4(),
            "company_id": uuid4(),
            "resource_type": "chat",
            "resource_id": "oc_chat",
        },
    )()
    db = FakeDb()

    _link_local_work_events_to_resource(
        db,
        resource,
        {"resource_type": "chat", "external_id": "oc_chat", "settings": {"source": "feishu_api"}},
    )

    assert db.statements == []


def test_discover_approval_resources_from_admin_pending_tasks() -> None:
    class ScalarResult:
        def all(self):
            return [SimpleNamespace(open_id="ou_owner", role="owner")]

    class FakeDb:
        def scalars(self, query):
            return ScalarResult()

    class FakeClient:
        async def api_get(self, path, params=None):
            if path == "/open-apis/approval/v4/instances/inst_1":
                return {"data": {"approval_name": "付款审批", "instance_code": "inst_1"}}
            assert path == "/open-apis/approval/v4/tasks/query"
            return {
                "data": {
                    "task_list": [
                        {
                            "definition_code": "approval_xxx",
                            "approval_name": "approval_xxx",
                            "process_code": "inst_1",
                            "task_id": "task_1",
                        }
                    ]
                }
            }

    items, errors = asyncio.run(
        _discover_approval_resources(FakeDb(), FakeClient(), company_id=uuid4(), limit=10)
    )

    assert errors == []
    assert items == [
        {
            "resource_type": "approval_code",
            "external_id": "approval_xxx",
            "name": "付款审批",
            "sync_enabled": True,
            "settings": {
                "source": "feishu_approval_tasks_query",
                "usage": "approval_resource_identifier",
                "discovered_from_role": "owner",
            },
        }
    ]


def test_mailbox_candidates_use_contacts_and_dedupe() -> None:
    class ScalarResult:
        def all(self):
            return [
                SimpleNamespace(settings={"email": "jun.chen@gaustek.com"}, role="owner"),
                SimpleNamespace(settings={"email": "jun.chen@gaustek.com"}, role="owner"),
                SimpleNamespace(settings={"email": "ops@gaustek.com"}, role="admin"),
            ]

    class FakeDb:
        def scalars(self, query):
            return ScalarResult()

    candidates = _mailbox_candidates(FakeDb(), company_id=uuid4(), explicit_mailbox_ids=["owner@gaustek.com"])

    assert candidates[:3] == ["owner@gaustek.com", "jun.chen@gaustek.com", "ops@gaustek.com"]


def test_approval_codes_for_sync_prefers_explicit_code() -> None:
    assert _approval_codes_for_sync(None, app_config=SimpleNamespace(company_id=uuid4()), approval_code=" approval_xxx ") == [
        "approval_xxx"
    ]


def test_approval_metadata_for_sync_migrates_legacy_resources(monkeypatch) -> None:
    company_id = uuid4()
    app_config = SimpleNamespace(id=uuid4(), company_id=company_id)
    legacy_id = uuid4()
    v5_id = uuid4()
    migrated = {}

    class ScalarResult:
        def __init__(self, items):
            self.items = items

        def all(self):
            return self.items

    class FakeDb:
        def __init__(self):
            self.results = [ScalarResult([])]

        def scalars(self, query):
            return self.results.pop(0)

    def fake_migrate(db, *, app_config, resource_type, limit=None):
        migrated["db"] = db
        migrated["app_config"] = app_config
        migrated["resource_type"] = resource_type
        migrated["limit"] = limit
        return [
            LegacyFeishuResourceMigration(
                legacy_id=legacy_id,
                resource_type="approval_code",
                external_id="approval_xxx",
                name="付款审批",
                settings={"source": "legacy_feishu_resources"},
                resource=SimpleNamespace(id=v5_id),
            )
        ]

    monkeypatch.setattr("app.services.feishu.sync.migrate_legacy_feishu_resources", fake_migrate)
    db = FakeDb()

    items = _approval_metadata_for_sync(db, app_config=app_config, approval_code=None)

    assert items == [{"approval_code": "approval_xxx", "approval_name": "付款审批"}]
    assert migrated["app_config"] is app_config
    assert migrated["resource_type"] == "approval_code"


def test_approval_event_title_and_payload_include_context() -> None:
    app_config = SimpleNamespace(company_id=uuid4())
    event = _api_item_to_event(
        app_config,
        "approvals",
        {"approval_instance_id": "inst_1", "instance_code": "PAY-001", "status": "PENDING"},
        item_context={"approval_code": "approval_xxx", "approval_name": "付款审批"},
    )

    assert event.title == "付款审批 - PAY-001"
    assert event.payload["context"]["approval_code"] == "approval_xxx"
    assert "付款审批" in event.labels


def test_extract_approval_task_items_accepts_feishu_task_list() -> None:
    data = {"task_list": [{"task_id": "task_1"}, "bad", {"task_id": "task_2"}]}

    assert _extract_approval_task_items(data) == [{"task_id": "task_1"}, {"task_id": "task_2"}]


def test_fetch_user_pending_tasks_can_skip_instance_detail_enrichment() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.paths = []

        async def api_get(self, path, params=None):
            self.paths.append(path)
            if path == "/open-apis/approval/v4/tasks/query":
                return {
                    "data": {
                        "task_list": [
                            {
                                "task_id": "task_1",
                                "process_code": "approval-1",
                                "definition_code": "definition-1",
                            }
                        ]
                    }
                }
            raise AssertionError(f"unexpected detail fetch: {path}")

    client = FakeClient()
    service = FeishuApprovalService(None, client=client)

    result = asyncio.run(
        service.fetch_user_pending_tasks(
            open_id="ou_user",
            limit=20,
            names_by_code={"definition-1": "报销审批"},
            enrich_details=False,
        )
    )

    assert result["available"] is True
    assert result["items"][0]["approval_name"] == "报销审批"
    assert client.paths == ["/open-apis/approval/v4/tasks/query"]


def test_approval_instance_code_prefers_instance_code_over_serial_number() -> None:
    assert (
        _approval_instance_code(
            {
                "serial_number": "202606210001",
                "instance_code": "5FCA1EB7-2EB0-414D-8630-F61540524341",
            }
        )
        == "5FCA1EB7-2EB0-414D-8630-F61540524341"
    )


def test_approval_text_decision_advice_prefers_runtime_assessment() -> None:
    advice = _approval_text_decision_advice(
        {
            "_approval_assessment": {
                "suggestion": "可通过",
                "reason": "已命中 completed Snapshot",
            }
        },
        "付款审批",
        {},
        amount=None,
        attachments=[],
        attachment_results=[],
    )

    assert advice == "可通过。理由：已命中 completed Snapshot"


def test_format_pending_approval_tasks_is_personal_pending_list() -> None:
    lines = _format_pending_approval_tasks(
        [
            {
                "approval_name": "付款审批",
                "task_title": "余莲莲提交的付款申请",
                "instance_code": "PAY-001",
                "applicant_name": "余莲莲",
                "task_start_time": 1781000000000,
            }
        ],
        limit=8,
    )

    text = "\n".join(lines)
    assert "老板，待你审批 1 个" in text
    assert "1. 付款审批" in text
    assert "建议：" in text
    assert "通过第N条" in text


def test_approval_action_card_contains_clickable_actions() -> None:
    card = build_feishu_approval_action_card(
        [
            {
                "approval_name": "付款审批",
                "initiator_names": ["余莲莲"],
                "instance_detail": {"form": '[{"name":"付款金额","value":3724},{"name":"付款事由","value":"端午节礼品"}]'},
            }
        ],
        chat_id="oc_1",
        receive_id_type="chat_id",
        receive_id="oc_1",
        actor_open_id="ou_owner",
    )

    assert card["header"]["title"]["content"] == "待审批"
    assert "老板，当前待你审批 **1** 个" in card["elements"][0]["text"]["content"]
    assert "**建议：" in card["elements"][1]["text"]["content"]
    assert "理由：" in card["elements"][1]["text"]["content"]
    assert "简洁理由：" not in card["elements"][1]["text"]["content"]
    assert "详情：点下方" not in card["elements"][1]["text"]["content"]
    assert "先展开核验" not in card["elements"][1]["text"]["content"]
    assert card["elements"][2]["layout"] == "flow"
    actions = card["elements"][2]["actions"]
    assert [item["text"]["content"] for item in actions] == ["详情", "通过", "拒绝"]
    assert actions[0]["value"] == {
        "kind": "approval_action",
        "index": 1,
        "chat_id": "oc_1",
        "receive_id_type": "chat_id",
        "receive_id": "oc_1",
        "actor_open_id": "ou_owner",
        "action": "detail",
    }


def test_approval_action_card_can_render_expanded_detail() -> None:
    card = build_feishu_approval_action_card(
        [
            {
                "approval_name": "报销审批",
                "initiator_names": ["王东升"],
                "instance_detail": {
                    "form": '[{"name":"费用汇总","value":1309.2},{"name":"报销事由","value":"客户现场住宿"}]'
                },
            }
        ],
        chat_id="oc_1",
        receive_id_type="chat_id",
        receive_id="oc_1",
        actor_open_id="ou_owner",
        expanded_index=1,
    )

    content = card["elements"][1]["text"]["content"]
    assert "这笔审批的关键信息" in content
    assert "详细理由" in content
    assert card["elements"][2]["actions"][0]["text"]["content"] == "详情"
    assert card["elements"][2]["actions"][0]["value"]["action"] == "detail"


def test_approval_context_preserves_attachment_summaries(monkeypatch) -> None:
    store: dict[str, str] = {}

    class FakeRedis:
        def setex(self, key, ttl, value):
            store[key] = value

        def get(self, key):
            return store.get(key)

    class FakeClient:
        def __init__(self, app_config):
            self.redis = FakeRedis()

    monkeypatch.setattr("app.services.feishu.approval_card_entrypoint.FeishuClient", FakeClient)
    app_config = SimpleNamespace(id=uuid4())
    identity = SimpleNamespace(open_id="ou_owner")
    item = {
        "approval_name": "报销审批",
        "_attachment_results": [
            ApprovalAttachmentReadResult(
                name="发票.pdf",
                token="file_1",
                text_preview="发票金额：1309.2元；费用归属：固势。",
            )
        ],
    }

    store_approval_context(app_config, identity, "oc_1", [item])
    loaded = load_approval_context(app_config, identity, "oc_1")

    assert loaded
    assert approval_attachment_results(loaded[0])[0].text_preview.startswith("发票金额")


def test_approval_card_action_detection() -> None:
    payload = {"event": {"action": {"value": {"kind": "approval_action", "action": "detail", "index": 1}}}}

    assert approval_card_responder.is_approval_card_action(payload) is True


def test_approval_card_action_detection_accepts_action_info_value() -> None:
    payload = {"event": {"action_info": {"value": {"kind": "approval_action", "action": "detail", "index": 1}}}}

    assert approval_card_responder.is_approval_card_action(payload) is True


def test_fetch_pending_approval_tasks_routes_through_tool_router(monkeypatch) -> None:
    company_id = uuid4()
    app_config = SimpleNamespace(company_id=company_id)
    captured = []

    def fake_execute_agent_tool(context, request):
        captured.append((context, request))
        if request.tool_name == "feishu_approval_instance_get":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MCP,
                answer=json.dumps(
                    {
                        "data": {
                            "instance_code": "inst_1",
                            "approval_name": "付款明细审批",
                            "serial_number": "SN-1",
                            "form": '[{"name":"付款金额","value":3724}]',
                        }
                    }
                ),
            )
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.FEISHU_MCP,
            answer=json.dumps(
                {
                    "data": {
                        "tasks": [
                            {
                                "task_id": "task_1",
                                "process_code": "inst_1",
                                "definition_code": "approval_xxx",
                                "definition_name": "付款审批",
                            }
                        ]
                    }
                }
            ),
        )

    monkeypatch.setattr("app.services.feishu.approval_card_entrypoint.execute_agent_tool", fake_execute_agent_tool)

    result = asyncio.run(
        _fetch_feishu_pending_approval_tasks(
            SimpleNamespace(),
            app_config,
            SimpleNamespace(open_id="ou_owner", role="owner", access_scope="company", domains=("approval",)),
            limit=8,
        )
    )

    assert result["available"] is True
    assert result["items"][0]["approval_code"] == "approval_xxx"
    assert result["items"][0]["approval_name"] == "付款审批"
    assert result["items"][0]["process_code"] == "inst_1"
    assert result["items"][0]["instance_detail"]["form"] == '[{"name":"付款金额","value":3724}]'
    assert captured[0][0].company_id == company_id
    assert captured[0][1].tool_name == "feishu_approval_task_query"
    assert captured[0][1].params == {
        "open_id": "ou_owner",
        "topic": "1",
        "user_id_type": "open_id",
        "page_size": 8,
        "response_format": "raw_json",
    }
    assert captured[1][1].tool_name == "feishu_approval_instance_get"
    assert captured[1][1].params == {
        "instance_code": "inst_1",
        "user_id_type": "open_id",
        "response_format": "raw_json",
    }


def test_approval_attachment_summary_routes_download_through_tool_router(monkeypatch, tmp_path) -> None:
    company_id = uuid4()
    attachment_path = tmp_path / "contract.txt"
    attachment_path.write_text("甲方：固势（苏州）科技有限公司；服务内容：居间服务。", encoding="utf-8")
    captured = {}
    item = {
        "instance_detail": {
            "form": json.dumps(
                [
                    {
                        "name": "合同附件",
                        "type": "attachmentV2",
                        "value": [{"file_token": "file_123", "name": "contract.txt"}],
                    }
                ]
            )
        }
    }

    def fake_execute_agent_tool(context, request):
        captured["context"] = context
        captured["request"] = request
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.FEISHU_MCP,
            answer=json.dumps({"downloaded": True, "name": "contract.txt", "output_path": str(attachment_path)}),
        )

    monkeypatch.setattr("app.services.feishu.approval_card_entrypoint.execute_agent_tool", fake_execute_agent_tool)

    asyncio.run(
        _ensure_feishu_pending_approval_attachment_summaries(
            SimpleNamespace(company_id=company_id),
            [item],
        )
    )

    assert captured["context"].company_id == company_id
    assert captured["request"].tool_name == "feishu_approval_attachment_download"
    assert captured["request"].params == {"file_token": "file_123", "name": "contract.txt"}
    assert item["_attachment_total"] == 1
    assert item["_attachment_results"][0].text_preview.startswith("甲方：固势")
    assert not attachment_path.exists()


def test_card_action_sender_open_id_falls_back_to_actor_value() -> None:
    payload = {
        "event": {
            "action": {
                "value": {
                    "kind": "approval_action",
                    "action": "detail",
                    "index": 1,
                    "actor_open_id": "ou_owner",
                }
            }
        }
    }

    assert _get_sender_open_id(payload) == "ou_owner"


def test_approval_card_action_value_accepts_json_string() -> None:
    payload = {
        "event": {
            "action": {
                "value": json.dumps(
                    {
                        "kind": "approval_action",
                        "action": "detail",
                        "index": 1,
                        "actor_open_id": "ou_owner",
                    },
                    ensure_ascii=False,
                )
            }
        }
    }

    assert approval_card_action_value(payload)["actor_open_id"] == "ou_owner"
    assert _get_sender_open_id(payload) == "ou_owner"


def test_approval_card_responder_action_uses_injected_text_sender() -> None:
    calls: dict[str, Any] = {}

    class Identity:
        open_id = "ou_owner"

        def can_query_approvals(self):
            return True

    async def detail_reply(*args, **kwargs):
        calls["detail_kwargs"] = kwargs
        return "这笔审批的关键信息"

    async def prepare_reply(*args, **kwargs):
        calls["prepare_kwargs"] = kwargs
        return "已准备好提交"

    async def send_text_reply(**kwargs):
        calls["send_text"] = kwargs

    payload = {
        "event": {
            "action": {
                "value": {
                    "kind": "approval_action",
                    "action": "approve",
                    "index": 2,
                    "chat_id": "oc_1",
                    "receive_id_type": "chat_id",
                    "receive_id": "oc_1",
                }
            }
        }
    }

    handled = asyncio.run(
        approval_card_responder.handle_approval_card_action(
            None,
            SimpleNamespace(),
            payload,
            get_sender_identity=lambda *args: Identity(),
            permission_denied_reply=lambda identity, action_name: "无权限",
            get_chat_id=lambda payload: "oc_fallback",
            approval_detail_reply=detail_reply,
            prepare_approval_action_reply=prepare_reply,
            send_text_reply=send_text_reply,
            client_factory=lambda app_config: None,
        )
    )

    assert handled is True
    assert calls["prepare_kwargs"]["action"] == "approve"
    assert calls["prepare_kwargs"]["selector_text"] == "第2条"
    assert calls["send_text"]["reply_target"] == {"receive_id_type": "chat_id", "receive_id": "oc_1"}
    assert calls["send_text"]["text"] == "已准备好提交"


def test_approval_card_responder_callback_updates_card_with_injected_updater() -> None:
    calls: dict[str, Any] = {}

    class Identity:
        open_id = "ou_owner"

        def can_query_approvals(self):
            return True

    async def update_message_content(**kwargs):
        calls["update"] = kwargs

    async def send_text_reply(**kwargs):
        calls["send_text"] = kwargs

    async def detail_reply(*args, **kwargs):
        return "detail"

    async def prepare_reply(*args, **kwargs):
        return "prepare"

    payload = {
        "event": {
            "action": {
                "value": {
                    "kind": "approval_action",
                    "action": "detail",
                    "index": 1,
                    "chat_id": "oc_1",
                    "receive_id_type": "chat_id",
                    "receive_id": "oc_1",
                }
            },
            "context": {"open_message_id": "om_1"},
        }
    }

    response = asyncio.run(
        approval_card_responder.handle_card_action_response(
            None,
            SimpleNamespace(),
            payload,
            get_sender_identity=lambda *args: Identity(),
            permission_denied_reply=lambda identity, action_name: "无权限",
            get_chat_id=lambda payload: "oc_fallback",
            short_text=lambda text, limit: text[:limit],
            load_approval_context=lambda app_config, identity, chat_id: [{"approval_name": "报销审批"}],
            build_approval_action_card=lambda items, **kwargs: {"items": items, "expanded_index": kwargs["expanded_index"]},
            approval_detail_reply=detail_reply,
            prepare_approval_action_reply=prepare_reply,
            send_text_reply=send_text_reply,
            update_message_content=update_message_content,
            client_factory=lambda app_config: None,
        )
    )

    assert response == {}
    assert calls["update"]["message_id"] == "om_1"
    assert calls["update"]["content"]["expanded_index"] == 1
    assert "send_text" not in calls


def test_approval_card_message_id_reads_callback_context() -> None:
    payload = {
        "event": {
            "action": {"value": {"kind": "approval_action", "action": "detail"}},
            "context": {"open_message_id": "om_1"},
        }
    }

    assert approval_card_message_id(payload) == "om_1"


def test_approval_card_detail_action_sends_detail_message(monkeypatch) -> None:
    calls = {}
    store: dict[str, str] = {}

    class FakeRedis:
        def setex(self, key, ttl, value):
            store[key] = value

        def get(self, key):
            return store.get(key)

    class FakeClient:
        def __init__(self, app_config):
            self.app_config = app_config
            self.redis = FakeRedis()

        async def update_message_content(self, *, message_id, content):
            calls["message_id"] = message_id
            calls["content"] = content
            return {"code": 0}

        async def send_message(self, **kwargs):
            calls["sent_message"] = kwargs
            return {"code": 0}

    class FakeDb:
        def scalar(self, query):
            return SimpleNamespace(role="admin", access_scope="company", display_name="老板", settings={})

    monkeypatch.setattr("app.services.feishu.approval_card_entrypoint.FeishuClient", FakeClient)
    app_config = SimpleNamespace(id=uuid4(), company_id=uuid4())
    identity = SimpleNamespace(open_id="ou_owner")
    store_approval_context(
        app_config,
        identity,
        "oc_1",
        [
            {
                "approval_name": "报销审批",
                "initiator_names": ["王东升"],
                "instance_detail": {
                    "form": '[{"name":"费用汇总","value":1309.2},{"name":"报销事由","value":"客户现场住宿"}]'
                },
            }
        ],
    )

    payload = {
        "event": {
            "action": {
                "value": {
                    "kind": "approval_action",
                    "action": "detail",
                    "index": 1,
                    "chat_id": "oc_1",
                    "receive_id_type": "chat_id",
                    "receive_id": "oc_1",
                    "actor_open_id": "ou_owner",
                }
            },
            "context": {"open_message_id": "om_1"},
        }
    }

    handled = asyncio.run(handle_feishu_card_action_message(FakeDb(), app_config, payload))

    assert handled is True
    assert "message_id" not in calls
    assert calls["sent_message"]["msg_type"] == "text"
    assert "这笔审批的关键信息" in calls["sent_message"]["content"]["text"]


def test_card_action_response_updates_original_card_without_callback_card(monkeypatch) -> None:
    calls: dict[str, Any] = {}
    store: dict[str, str] = {}

    class FakeRedis:
        def setex(self, key, ttl, value):
            store[key] = value

        def get(self, key):
            return store.get(key)

    class FakeClient:
        def __init__(self, app_config):
            self.app_config = app_config
            self.redis = FakeRedis()

        async def update_message_content(self, *, message_id, content):
            calls["message_id"] = message_id
            calls["content"] = content
            return {"code": 0}

    class FakeDb:
        def scalar(self, query):
            return SimpleNamespace(role="admin", access_scope="company", display_name="老板", settings={})

    monkeypatch.setattr("app.services.feishu.approval_card_entrypoint.FeishuClient", FakeClient)
    app_config = SimpleNamespace(id=uuid4(), company_id=uuid4())
    identity = SimpleNamespace(open_id="ou_owner")
    store_approval_context(
        app_config,
        identity,
        "oc_1",
        [
            {
                "approval_name": "报销审批",
                "initiator_names": ["王东升"],
                "instance_detail": {
                    "form": '[{"name":"费用汇总","value":1309.2},{"name":"报销事由","value":"客户现场住宿"}]'
                },
            }
        ],
    )
    payload = {
        "event": {
            "action": {
                "value": {
                    "kind": "approval_action",
                    "action": "detail",
                    "index": 1,
                    "chat_id": "oc_1",
                    "receive_id_type": "chat_id",
                    "receive_id": "oc_1",
                    "actor_open_id": "ou_owner",
                }
            },
            "context": {"open_message_id": "om_1"},
        }
    }

    response = asyncio.run(handle_feishu_card_action_response(FakeDb(), app_config, payload))

    assert response == {}
    assert calls["message_id"] == "om_1"
    assert "这笔审批的关键信息" in json.dumps(calls["content"], ensure_ascii=False)


def test_format_pending_approval_tasks_includes_instance_form_detail() -> None:
    lines = _format_pending_approval_tasks(
        [
            {
                "approval_name": "付款审批",
                "title": "余莲莲提交的付款申请",
                "serial_number": "PAY-001",
                "initiator_names": ["余莲莲"],
                "instance_detail": {
                    "form": '[{"name":"借款事由","value":"采购备用金"},{"name":"申请金额","value":12000}]'
                },
            }
        ],
        limit=8,
    )

    text = "\n".join(lines)
    assert "采购备用金" in text
    assert "12000元" in text
    assert "建议：" in text


def test_format_pending_approval_tasks_is_concise_and_filters_noisy_form_arrays() -> None:
    lines = _format_pending_approval_tasks(
        [
            {
                "approval_name": "报销审批",
                "title": "王东升提交的报销",
                "serial_number": "BX-001",
                "initiator_names": ["王东升"],
                "instance_detail": {
                    "form": (
                        '[{"name":"费用明细","value":[{"id":"widget1","name":"费用产生日期","value":"2026-06-04"}]},'
                        '{"name":"费用汇总","value":1309.2},{"name":"费用承担公司","value":"固势（苏州）科技有限公司"}]'
                    )
                },
            }
        ],
        limit=8,
    )

    text = "\n".join(lines)
    assert "费用汇总" not in text
    assert "1309.2元" in text
    assert "widget1" not in text
    assert len(text) < 260


def test_format_pending_approval_tasks_shows_attachment_count_without_raw_json() -> None:
    form = (
        '[{"name":"合同附件","type":"attachmentV2","value":['
        '{"file_token":"file_123","name":"居间服务协议.pdf","type":"pdf"}'
        ']},{"name":"用章事由","value":"居间服务协议盖章"}]'
    )

    lines = _format_pending_approval_tasks(
        [
            {
                "approval_name": "用章用印申请",
                "initiator_names": ["余莲莲"],
                "instance_detail": {"form": form},
            }
        ],
        limit=8,
    )

    text = "\n".join(lines)
    assert "附件1" in text
    assert "file_123" not in text
    assert "attachmentV2" not in text


def test_format_pending_approval_tasks_uses_preread_attachment_summary_for_advice() -> None:
    form = (
        '[{"name":"合同附件","type":"attachmentV2","value":['
        '{"file_token":"file_123","name":"居间服务协议.pdf","type":"pdf"}'
        ']},{"name":"用章事由","value":"居间服务协议盖章"}]'
    )

    lines = _format_pending_approval_tasks(
        [
            {
                "approval_name": "用章用印申请",
                "initiator_names": ["余莲莲"],
                "instance_detail": {"form": form},
                "_attachment_results": [
                    ApprovalAttachmentReadResult(
                        name="居间服务协议.pdf",
                        token="file_123",
                        storage_key="approval_attachments/demo.pdf",
                        text_preview="甲方：固势（苏州）科技有限公司；服务内容：居间服务。",
                    )
                ],
            }
        ],
        limit=8,
    )

    text = "\n".join(lines)
    assert "附件已读1/1" in text
    assert "附件：已读摘要 1 个" in text
    assert "建议：可通过" in text
    assert "附件显示合同/协议材料已读取" in text


def test_sync_enriches_approval_item_with_attachment_text() -> None:
    class FakeAttachmentService:
        async def read_attachment_refs(self, refs, *, instance_code=None, max_files=3):
            return [
                ApprovalAttachmentReadResult(
                    name="居间服务协议.pdf",
                    token="file_123",
                    storage_key="approval_attachments/demo.pdf",
                    text_preview="甲方：固势（苏州）科技有限公司；乙方：服务商。",
                )
            ]

    item = {
        "instance_code": "APP-001",
        "form": (
            '[{"name":"合同附件","type":"attachmentV2","value":['
            '{"file_token":"file_123","name":"居间服务协议.pdf","type":"pdf"}]}]'
        ),
    }

    asyncio.run(_enrich_approval_item_with_attachments(item, FakeAttachmentService()))

    assert item["attachment_ingest"]["total"] == 1
    assert item["attachment_ingest"]["read"] == 1
    assert item["attachment_read_results"][0]["text_preview"].startswith("甲方：固势")
    assert item["attachment_summary"].startswith("甲方：固势")


def test_sync_fetches_approval_instance_detail_before_reading_attachments() -> None:
    class FakeClient:
        async def api_get(self, path, *, params=None):
            assert path == "/open-apis/approval/v4/instances/APP-001"
            assert params == {"user_id_type": "open_id"}
            return {
                "data": {
                    "instance_code": "APP-001",
                    "approval_name": "用章用印申请",
                    "form": (
                        '[{"name":"合同附件","type":"attachmentV2","value":['
                        '{"file_token":"file_123","name":"居间服务协议.pdf","type":"pdf"}]}]'
                    ),
                }
            }

    item = {"instance": {"code": "APP-001"}}

    asyncio.run(_enrich_approval_item_with_instance_detail(item, FakeClient()))

    assert item["instance_code"] == "APP-001"
    assert item["approval_name"] == "用章用印申请"
    assert "file_123" in item["form"]


def test_approval_api_item_uses_nested_instance_code_as_external_id() -> None:
    app_config = SimpleNamespace(company_id=uuid4())
    event = _api_item_to_event(
        app_config,
        "approvals",
        {"approval": {"name": "付款申请"}, "instance": {"code": "APP-001"}},
        item_context={"approval_name": "付款审批"},
    )

    assert event.external_id == "approvals:APP-001"
    assert event.title == "付款审批 - APP-001"


def test_pending_approval_merges_synced_attachment_results_from_work_events() -> None:
    company_id = uuid4()
    synced_payload = {
        "item": {
            "instance_code": "APP-001",
            "attachment_ingest": {"total": 1},
            "approval_ai_advice": {
                "conclusion": "谨慎通过",
                "concise_reason": "同步阶段已结合附件和表单研判。",
                "detailed_reason": "附件摘要与表单金额一致；风险点是费用归属；二次确认问题是发票抬头。",
                "source": "llm",
            },
            "attachment_read_results": [
                {
                    "name": "发票.pdf",
                    "token": "file_1",
                    "storage_key": "approval_attachments/demo.pdf",
                    "text_preview": "发票金额：1309.2元；费用归属：固势。",
                }
            ],
        }
    }

    class FakeDb:
        def scalars(self, query):
            return SimpleNamespace(
                all=lambda: [
                    SimpleNamespace(
                        payload=synced_payload,
                        company_id=company_id,
                        event_type="feishu.approvals.snapshot",
                        occurred_at=datetime.now(UTC),
                    )
                ]
            )

    item = {
        "instance_code": "APP-001",
        "approval_name": "报销审批",
        "instance_detail": {
            "form": (
                '[{"name":"发票附件","type":"attachmentV2","value":['
                '{"file_token":"file_1","name":"发票.pdf","type":"pdf"}]},'
                '{"name":"费用汇总","value":1309.2}]'
            )
        },
    }

    _attach_synced_approval_attachments(FakeDb(), SimpleNamespace(company_id=company_id), [item])

    lines = _format_pending_approval_tasks([item], limit=1)
    text = "\n".join(lines)
    assert "附件已读1/1" in text
    assert "建议：谨慎通过" in text
    assert "同步阶段已结合附件和表单研判" in text


def test_sync_enriches_approval_item_with_ai_advice(monkeypatch) -> None:
    def fake_generate_approval_llm_advice(**kwargs):
        assert "合同金额" in kwargs["attachment_summary"]
        return {
            "conclusion": "谨慎通过",
            "concise_reason": "合同摘要和付款申请基本匹配。",
            "detailed_reason": "主要依据是合同金额和付款对象；风险点是付款日期；二次确认问题是合同版本。",
            "source": "llm",
        }

    monkeypatch.setattr("app.services.feishu.sync.generate_approval_llm_advice", fake_generate_approval_llm_advice)
    item = {
        "approval_name": "付款审批",
        "form": '[{"name":"付款金额","value":50000},{"name":"付款事由","value":"设备打板预存"}]',
        "attachment_summary": "合同金额：50000元；供应商：深圳嘉立创科技集团股份有限公司。",
    }

    _enrich_approval_item_with_ai_advice(item)

    assert item["approval_ai_advice"]["conclusion"] == "谨慎通过"
    assert item["approval_ai_advice"]["concise_reason"] == "合同摘要和付款申请基本匹配。"


def test_pending_approval_advice_does_not_reject_only_because_attachment_not_listed() -> None:
    lines = _format_pending_approval_tasks(
        [
            {
                "approval_name": "用章用印申请",
                "initiator_names": ["余莲莲"],
                "instance_detail": {
                    "form": '[{"name":"用章事由","value":"居间服务协议盖章"},{"name":"使用类型","value":"公司用章"}]'
                },
            },
            {
                "approval_name": "报销审批",
                "initiator_names": ["王东升"],
                "instance_detail": {
                    "form": '[{"name":"费用汇总","value":1309.2},{"name":"费用承担公司","value":"固势（苏州）科技有限公司"}]'
                },
            },
        ],
        limit=8,
    )

    text = "\n".join(lines)
    assert "未识别到附件" not in text
    assert "关键凭证缺失时先退回" not in text
    assert "补充后再审" in text


def test_pending_approval_advice_does_not_treat_unread_fetched_attachment_as_missing_material() -> None:
    lines = _format_pending_approval_tasks(
        [
            {
                "approval_name": "用章用印申请",
                "initiator_names": ["余莲莲"],
                "instance_detail": {
                    "form": (
                        '[{"name":"用章事由","value":"居间服务协议盖章"},'
                        '{"name":"附件","type":"attachmentV2","value":["https://internal-api-drive-stream.feishu.cn/file.pdf"]}]'
                    )
                },
                "_attachment_results": [
                    ApprovalAttachmentReadResult(
                        name="居间服务协议.pdf",
                        token="",
                        storage_key="approval_attachments/demo.pdf",
                        content_type="application/pdf",
                        text_preview="",
                    )
                ],
            }
        ],
        limit=8,
    )

    text = "\n".join(lines)
    assert "建议：先展开附件" in text
    assert "补充后再审" not in text
    assert "不是材料缺失" in text


def test_pending_approval_advice_localizes_reserve_fund_without_attachment_warning() -> None:
    lines = _format_pending_approval_tasks(
        [
            {
                "approval_name": "Reserve Fund",
                "initiator_names": ["戴留兴"],
                "instance_detail": {
                    "form": '[{"name":"借款事由","value":"暂借抵扣报销"},{"name":"申请金额","value":50000},{"name":"项目名称","value":"控制器模块测试验证设备项目"}]'
                },
            }
        ],
        limit=8,
    )

    text = "\n".join(lines)
    assert "借款申请" in text
    assert "Reserve Fund" not in text
    assert "附件" not in text
    assert "建议：可通过" in text
    assert "抵扣/归还闭环" in text


def test_approval_attachment_refs_extracts_file_metadata() -> None:
    refs = _approval_attachment_refs(
        '[{"name":"发票附件","type":"attachmentV2","value":[{"file_token":"file_1","name":"发票.pdf","type":"pdf"}]}]'
    )

    assert refs == [
        {
            "field_name": "发票附件",
            "name": "发票.pdf",
            "token": "file_1",
            "type": "pdf",
            "url": "",
        }
    ]


def test_approval_attachment_refs_scans_nested_file_tokens_even_when_field_name_is_generic() -> None:
    refs = _approval_attachment_refs(
        [
            {
                "name": "盖章详情",
                "type": "fieldList",
                "value": [
                    {
                        "name": "文件",
                        "value": '{"fileToken":"file_2","fileName":"居间服务协议.pdf","fileType":"pdf"}',
                    }
                ],
            }
        ]
    )

    assert refs == [
        {
            "field_name": "盖章详情",
            "name": "居间服务协议.pdf",
            "token": "file_2",
            "type": "pdf",
            "url": "",
        }
    ]


def test_approval_attachment_refs_extracts_feishu_authcode_urls_with_ext_names() -> None:
    refs = _approval_attachment_refs(
        [
            {
                "name": "附件",
                "type": "attachmentV2",
                "ext": "200.png,合同.pdf",
                "value": [
                    "https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=abc",
                    "https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=def",
                ],
            }
        ]
    )

    assert refs == [
        {
            "field_name": "附件",
            "name": "200.png",
            "token": "",
            "type": "png",
            "url": "https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=abc",
        },
        {
            "field_name": "附件",
            "name": "合同.pdf",
            "token": "",
            "type": "pdf",
            "url": "https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=def",
        },
    ]


def test_approval_attachment_service_reads_url_attachment() -> None:
    service = FeishuApprovalAttachmentService(SimpleNamespace(company_id=uuid4()))

    async def fake_download_by_url(url: str):
        assert url == "https://internal-api-drive-stream.feishu.cn/file.txt"
        return b"hello attachment", "text/plain"

    service._download_by_url = fake_download_by_url

    result = asyncio.run(
        service.read_attachment_ref(
            {
                "field_name": "附件",
                "name": "说明.txt",
                "token": "",
                "url": "https://internal-api-drive-stream.feishu.cn/file.txt",
            },
            instance_code="APP-001",
        )
    )

    assert result.fetched is True
    assert result.downloaded is False
    assert result.storage_key is None
    assert result.text_preview == "hello attachment"


def test_extract_attachment_text_uses_ocr_for_image(monkeypatch) -> None:
    monkeypatch.setattr(
        file_intelligence_registry,
        "extract_image",
        lambda data, *, filename, mime_type, max_chars: file_intelligence_registry.ExtractionResult(
            success=True,
            text="发票金额 268 元",
            mime_type=mime_type,
            filename=filename,
            extractor="image_ocr",
        ),
    )

    text = extract_attachment_text(b"image-bytes", filename="invoice.png", content_type="image/png")

    assert text == "发票金额 268 元"


def test_extract_attachment_text_uses_ocr_when_pdf_has_no_embedded_text(monkeypatch) -> None:
    monkeypatch.setattr(
        file_intelligence_pdf,
        "extract_pdf_embedded_text",
        lambda data, *, filename, mime_type, max_chars: file_intelligence_pdf.ExtractionResult(
            success=False,
            text="",
            mime_type=mime_type,
            filename=filename,
            extractor="pdf_embedded",
        ),
    )
    monkeypatch.setattr(
        file_intelligence_pdf,
        "extract_pdf_ocr_text",
        lambda data, *, filename, mime_type, max_chars: file_intelligence_pdf.ExtractionResult(
            success=True,
            text="扫描合同 OCR 文本",
            mime_type=mime_type,
            filename=filename,
            extractor="pdf_ocr",
        ),
    )

    text = extract_attachment_text(b"pdf-bytes", filename="scan.pdf", content_type="application/pdf")

    assert text == "扫描合同 OCR 文本"


def test_approval_attachment_refs_does_not_treat_plain_named_fields_as_files() -> None:
    refs = _approval_attachment_refs('[{"name":"申请人","value":{"name":"王东升"}}]')

    assert refs == []


def test_approval_attachment_refs_extracts_nested_field_list_url_files() -> None:
    refs = _approval_attachment_refs(
        [
            {
                "name": "费用明细",
                "type": "fieldList",
                "value": [
                    [
                        {"name": "金额", "type": "amount", "value": 1309.2},
                        {
                            "name": "发票、清单",
                            "type": "attachmentV2",
                            "ext": "滴滴出行行程报销单.pdf,滴滴电子发票.pdf",
                            "value": ["https://example.com/a.pdf", "https://example.com/b.pdf"],
                        },
                    ]
                ],
            }
        ]
    )

    assert [item["name"] for item in refs] == ["滴滴出行行程报销单.pdf", "滴滴电子发票.pdf"]
    assert all(item["field_name"] == "发票、清单" for item in refs)
    assert all(item["url"].startswith("https://example.com/") for item in refs)


def test_format_approval_detail_lines_lists_attachments_without_claiming_content_read() -> None:
    lines = _format_approval_detail_lines(
        {
            "approval_name": "用章用印申请",
            "serial_number": "YZ-001",
            "initiator_names": ["余莲莲"],
            "instance_detail": {
                "form": '[{"name":"合同附件","type":"attachmentV2","value":[{"file_token":"file_123","name":"协议.pdf"}]}]'
            },
        }
    )

    text = "\n".join(lines)
    assert "附件：1 个" in text
    assert "协议.pdf" in text
    assert "尚未执行读取解析" in text


def test_format_approval_detail_lines_includes_detailed_reason_with_attachment_summary() -> None:
    lines = _format_approval_detail_lines(
        {
            "approval_name": "报销审批",
            "serial_number": "BX-001",
            "initiator_names": ["王东升"],
            "instance_detail": {
                "form": (
                    '[{"name":"费用汇总","value":1309.2},'
                    '{"name":"报销事由","value":"客户现场住宿"},'
                    '{"name":"附件","type":"attachmentV2","value":['
                    '{"file_token":"file_1","name":"发票.pdf","type":"pdf"}]}]'
                )
            },
        },
        attachment_results=[
            ApprovalAttachmentReadResult(
                name="发票.pdf",
                token="file_1",
                text_preview="发票金额：1309.2元；购买方：固势（苏州）科技有限公司。",
            )
        ],
    )

    text = "\n".join(lines)
    assert "详细理由" in text
    assert "金额依据" in text
    assert "事由依据" in text
    assert "附件/OCR依据" in text


def test_approval_llm_advice_is_used_in_list_and_detail(monkeypatch) -> None:
    def fake_generate_approval_llm_advice(**kwargs):
        assert "发票金额" in kwargs["attachment_summary"]
        return {
            "conclusion": "谨慎通过",
            "concise_reason": "发票金额与报销金额一致，但需二次确认费用归属。",
            "detailed_reason": "表单金额与附件摘要一致，风险点是费用归属需和项目匹配；二次确认问题是票据抬头和行程是否同一业务。",
            "source": "llm",
        }

    item = {
        "approval_name": "报销审批",
        "serial_number": "BX-002",
        "initiator_names": ["王东升"],
        "instance_detail": {
            "form": (
                '[{"name":"费用汇总","value":1309.2},'
                '{"name":"报销事由","value":"客户现场住宿"},'
                '{"name":"附件","type":"attachmentV2","value":['
                '{"file_token":"file_1","name":"发票.pdf","type":"pdf"}]}]'
            )
        },
        "_attachment_results": [
            ApprovalAttachmentReadResult(
                name="发票.pdf",
                token="file_1",
                text_preview="发票金额：1309.2元；购买方：固势（苏州）科技有限公司。",
            )
        ],
        "_approval_history": ["报销审批 / 状态:approved / 金额:1309.2 / 事由:客户现场住宿"],
    }

    def advice_for_item(value):
        return approval_enrichment.approval_llm_advice_for_item(
            value,
            form_fields=lambda form: _approval_form_fields(form, max_fields=30),
            attachment_refs=_approval_attachment_refs,
            attachment_results=approval_attachment_results,
            rule_recommendation=rule_approval_decision_recommendation,
            readable_name=readable_approval_name,
            approval_amount=_approval_amount,
            attachment_basis=approval_attachment_basis,
            generate_advice=fake_generate_approval_llm_advice,
        )

    approval_enrichment.attach_approval_llm_advice([item], advice_for_item=advice_for_item)

    list_text = "\n".join(_format_pending_approval_tasks([item], limit=1))
    detail_text = "\n".join(_format_approval_detail_lines(item, attachment_results=approval_attachment_results(item)))

    assert "建议：谨慎通过。理由：发票金额与报销金额一致" in list_text
    assert "综合研判" in detail_text
    assert "风险点" in detail_text
    assert "二次确认问题" in detail_text
    assert "发票金额" in detail_text


def test_extract_attachment_text_reads_plain_text() -> None:
    text = extract_attachment_text("合同金额：10000\n供应商：测试公司".encode(), filename="合同.txt", content_type="text/plain")

    assert "合同金额" in text
    assert "测试公司" in text


def test_extract_attachment_text_reads_docx_with_stdlib() -> None:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body><w:p><w:r><w:t>居间服务协议</w:t></w:r></w:p></w:body></w:document>"
            ),
        )

    text = extract_attachment_text(buffer.getvalue(), filename="协议.docx")

    assert "居间服务协议" in text


def test_extract_attachment_text_reads_xlsx_with_stdlib() -> None:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(
            "xl/sharedStrings.xml",
            (
                '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                "<si><t>供应商</t></si><si><t>测试公司</t></si></sst>"
            ),
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            (
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<sheetData><row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row></sheetData></worksheet>'
            ),
        )

    text = extract_attachment_text(buffer.getvalue(), filename="明细.xlsx")

    assert "供应商 | 测试公司" in text


def test_approval_attachment_service_downloads_stores_and_extracts_text() -> None:
    class FakeClient:
        app_config = None

        def __init__(self):
            self.paths = []

        async def download_binary(self, path, params=None):
            self.paths.append(path)
            return "付款对象：测试供应商".encode(), "text/plain"

    app_config = SimpleNamespace(company_id=uuid4())
    client = FakeClient()
    service = FeishuApprovalAttachmentService(app_config, client=client)

    results = __import__("asyncio").run(
        service.read_attachment_refs(
            [{"name": "付款说明.txt", "token": "file_1"}],
            instance_code="PAY-001",
        )
    )

    assert client.paths == ["/open-apis/drive/v1/medias/file_1/download"]
    assert results[0].fetched is True
    assert results[0].downloaded is False
    assert results[0].storage_key is None
    assert "测试供应商" in results[0].text_preview


def test_approval_command_accepts_numbered_selection() -> None:
    assert _is_approval_approve_request("通过第3条") is True
    assert _is_approval_reject_request("拒绝第2条") is True

    items = [
        {"instance_code": "A-001", "task_id": "task_1", "approval_name": "付款审批"},
        {"instance_code": "B-002", "task_id": "task_2", "approval_name": "报销审批"},
    ]

    assert _select_approval_item(items, "通过第2条") == items[1]
    assert _select_approval_item(items, "通过单号A-001") == items[0]


def test_approval_form_fields_maps_radio_options() -> None:
    fields = _approval_form_fields(
        '[{"name":"公司抬头","type":"radioV2","value":"1","option":[{"id":"1","name":"固势科技"}]}]'
    )

    assert fields == [("公司抬头", "固势科技")]


def test_approval_amount_ignores_serial_numbers() -> None:
    fields = {"借款申请单号": "JKSQ20260528001", "申请金额": "50000"}

    assert _approval_amount(fields) == 50000


def test_contact_events_are_recorded_for_sync_layer() -> None:
    app_config = SimpleNamespace(company_id=uuid4())
    department = {"department_id": "od_1", "parent_department_id": "0", "name": "销售部", "member_count": 2}
    user = {"open_id": "ou_1", "name": "张三", "job_title": "销售经理", "department_ids": ["od_1"]}

    department_event = _contact_department_to_event(app_config, department)
    user_event = _contact_user_to_event(app_config, user)

    assert department_event.event_type == "feishu.contacts.department"
    assert department_event.title == "部门：销售部"
    assert user_event.event_type == "feishu.contacts.user"


def test_contact_sync_reports_employee_agent_counts(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, app_config):
            self.app_config = app_config

        async def api_get(self, path, params=None):
            if path.endswith("/children"):
                return {"data": {"items": []}}
            return {
                "data": {
                    "items": [
                        {
                            "open_id": "ou_member",
                            "name": "测试成员",
                            "enterprise_email": "member@example.com",
                            "job_title": "工程师",
                        }
                    ]
                }
            }

    class FakeDb:
        def __init__(self):
            self.added = []

        def scalar(self, query):
            return None

        def add(self, item):
            self.added.append(item)

    monkeypatch.setattr("app.services.feishu.sync.FeishuClient", FakeClient)
    monkeypatch.setattr(
        "app.services.feishu.sync.upsert_work_event",
        lambda db, data: SimpleNamespace(id=uuid4(), event_type=data.event_type),
    )

    result = asyncio.run(
        _sync_contacts(
            FakeDb(),
            SimpleNamespace(company_id=uuid4()),
            limit=1,
            max_pages=1,
            extract_items=False,
        )
    )

    assert result["available"] is True
    assert result["user_count"] == 1
    assert result["agent_synced_count"] == 1
    assert result["agent_created_count"] == 1
    assert result["agent_sync_model"] == "contact_sync_creates_employee_agents"


def test_approval_instance_status_reads_local_history_status() -> None:
    event = SimpleNamespace(payload={"item": {"status": "APPROVED"}})

    assert approval_instance_status(event) == "approved"


def test_approval_instance_status_reads_dict_history_status() -> None:
    item = {"instance_detail": {"form": []}, "status": "PENDING"}

    assert approval_instance_status(item) == "pending"


def test_execute_feishu_approval_action_routes_through_tool_router(monkeypatch) -> None:
    company_id = uuid4()
    app_config = SimpleNamespace(company_id=company_id)
    captured = {}

    def fake_execute_agent_tool(context, request):
        captured["context"] = context
        captured["request"] = request
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.FEISHU_MCP,
            answer="飞书审批任务已提交同意。",
        )

    monkeypatch.setattr("app.services.feishu.approval_card_entrypoint.execute_agent_tool", fake_execute_agent_tool)
    result = __import__("asyncio").run(
        _execute_feishu_approval_action(
            app_config,
            SimpleNamespace(open_id="ou_owner", role="owner", access_scope="company", domains=("approval",)),
            {"approval_code": "approval_xxx", "instance_code": "inst_1", "task_id": "task_1"},
            action="approve",
        )
    )

    assert result["ok"] is True
    assert captured["context"].company_id == company_id
    assert captured["context"].actor.open_id == "ou_owner"
    assert captured["request"].tool_name == "feishu_approval_task_approve"
    assert captured["request"].params["app_config"] is app_config
    assert captured["request"].params["open_id"] == "ou_owner"
    assert captured["request"].params["approval_code"] == "approval_xxx"
    assert captured["request"].params["confirmed"] is True
    assert captured["request"].params["confirmation_token"]


def test_record_approval_action_audit_omits_internal_confirmation_token() -> None:
    class FakeDb:
        def __init__(self):
            self.added = []

        def add(self, item):
            self.added.append(item)

    db = FakeDb()
    company_id = uuid4()

    _record_feishu_approval_action_audit(
        db,
        SimpleNamespace(company_id=company_id),
        SimpleNamespace(open_id="ou_owner"),
        "oc_1",
        {
            "approval_code": "approval_1",
            "instance_code": "instance_1",
            "task_id": "task_1",
            "_confirmation_token": "secret-token",
        },
        "approve",
        {"ok": True},
    )

    assert len(db.added) == 1
    audit = db.added[0]
    assert audit.action == "approval.task.approve"
    assert audit.company_id == company_id
    assert audit.actor == "ou_owner"
    assert audit.target_type == "approval_task"
    assert audit.target_id == "task_1"
    assert audit.payload == {
        "status": "success",
        "approval_code": "approval_1",
        "instance_code": "instance_1",
        "task_id": "task_1",
        "chat_id": "oc_1",
        "confirmed": True,
        "confirmation_token_checked": True,
        "error": None,
        "expected_action": None,
    }
