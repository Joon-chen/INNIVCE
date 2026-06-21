from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.exc import OperationalError

from app.api.routes.v5 import router as v5_router
from app.api.routes.v5_agent_request_models import AgentSettingsUpdate, AgentTracePreviewRequest
from app.api.routes.v5_agent_reply_mode_routes import agent_reply_modes
from app.api.routes.v5_agent_settings_read_routes import agent_settings
from app.api.routes.v5_agent_settings_write_routes import (
    save_agent_settings,
)
from app.api.routes.v5_agent_trace_list_routes import list_agent_traces
from app.api.routes.v5_agent_trace_preview_routes import agent_trace_preview
from app.api.routes.v5_resource_request_models import ResourceBatchSyncRequest
from app.api.routes.v5_system_log_list_routes import list_system_logs
from app.api.routes.v5_system_log_overview_routes import system_logs_overview
from app.api.routes.v5_tool_batch_config_routes import batch_update_tool_configs
from app.api.routes.v5_tool_single_config_routes import update_tool_config
from app.api.routes.v5_tool_execution_routes import execute_tool
from app.api.routes.v5_tool_execution_log_routes import list_tool_executions
from app.api.routes.v5_tool_request_models import (
    ToolBatchUpdateRequest,
    ToolConfigUpdate,
    ToolExecutionRequest,
)
from app.services.agent.runtime import AgentExecutionStep, AgentRuntimeResult, AgentRuntimeTrace
from app.services.tools.base import ToolExecutionStatus, ToolProvider, ToolResult
from app.models.entities import ResourceSyncRun
from app.models.entities import ExtractedItem
from app.schemas.common import WorkEventCreate
from app.services.ai.risk_noise import close_finance_document_noise_risks, close_low_signal_extraction_noise
from app.services.resource_sync_runs import finish_resource_sync_run, start_resource_sync_run
from app.services.resource_sources import upsert_mail_account_resource
from app.services.system_logs import system_log_item_payload
from app.services.integrations.mail import ingest_email_message
from app.services.access_control import AccessPrincipal, can_access_resource, resource_access_policy
from app.services.v5_administration import (
    DEFAULT_V5_PERMISSIONS,
    DEFAULT_V5_ROLES,
    _legacy_resource_identity,
    v5_entrypoint_status,
    v5_os_overview,
)
from app.services.v5_auto_sync import company_auto_sync_due, select_company_auto_sync_resources, sync_company_auto_resources
from app.services.operations_bot_users import bot_user_access_payload, recent_gateway_messages_by_actor
from app.services.v5_owner_actions import owner_actions_from_resource_items
from app.services.v5_resource_status import _resource_sync_payload, _status_order, resource_sync_run_payload, workspace_event_payload
from app.services.v5_resource_governance import set_resource_access_decision
from app.services.v5_resources import normalize_v5_resource_type, resource_to_v5_payload
from app.services.v5_sync_decisions import sync_decision_for_resource
from app.services.v5_sync_policy import enabled_v5_resource_sync_policies, normalize_v5_resource_sync_policy
from app.services.v5_sync_strategy import (
    DEFAULT_REALTIME_RESOURCE_TYPES,
    capability_requirement_for_resource,
    data_layer_for_resource,
    sync_action_for_resource,
    sync_strategy_overview,
)
from app.services.v5_workspace import (
    _clear_resource_access_block,
    _decision_resource,
    _error_message_from_result,
    _is_bot_not_in_chat_error,
    _is_calendar_authorization_error,
    _items_seen_from_result,
    _mark_resource_access_blocked,
    _sync_args_for_resource,
    _work_event_ids_from_result,
    sync_v5_resource,
)


def _fake_cli_doctor_result(returncode: int = 0, output: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, output=output or '{"ok": true, "checks": []}')


def test_v5_router_exposes_foundation_endpoints() -> None:
    paths = {route.path for route in v5_router.routes}

    assert "/api/v5/bootstrap/foundation" in paths
    assert "/api/v5/os/overview" in paths
    assert "/api/v5/entrypoints/status" in paths
    assert "/api/v5/resources" in paths
    assert "/api/v5/resources/company-overview" in paths
    assert "/api/v5/resources/sync-strategy" in paths
    assert "/api/v5/tools" in paths
    assert "/api/v5/tools/batch" in paths
    assert "/api/v5/tools/executions" in paths
    assert "/api/v5/tools/{tool_name}/execute" in paths
    assert "/api/v5/system/logs/overview" in paths
    assert "/api/v5/system/logs" in paths
    assert "/api/v5/agent/trace-preview" in paths
    assert "/api/v5/agent/reply-modes" in paths
    assert "/api/v5/agent/traces" in paths
    assert "/api/v5/agent/settings" in paths
    assert "/api/v5/capability-registry" in paths
    assert "/api/v5/tools/{tool_name}" in paths
    assert "/api/v5/resources/sync-status" in paths
    assert "/api/v5/resources/sync-runs" in paths
    assert "/api/v5/resources/monitoring" in paths
    assert "/api/v5/resources/sync-policy" in paths
    assert "/api/v5/resources/sync-preview" in paths
    assert "/api/v5/resources/sync" in paths
    assert "/api/v5/resources/{resource_id}/clear-access-block" in paths
    assert "/api/v5/resources/{resource_id}/retry-access-block" in paths
    assert "/api/v5/resources/{resource_id}/access-decision" in paths
    assert "/api/v5/intelligence/risk-noise/close-finance-documents" in paths
    assert "/api/v5/intelligence/noise/close-low-signal-notices" in paths
    assert "/api/v5/intelligence/business-items/enrich-open" in paths
    assert "/api/v5/administration/company-settings" in paths
    assert "/api/v5/administration/departments" in paths
    assert "/api/v5/administration/teams" in paths
    assert "/api/v5/administration/users" in paths
    assert "/api/v5/administration/roles" in paths
    assert "/api/v5/administration/permissions" in paths
    assert "/api/v5/administration/resource-permissions" in paths
    assert "/api/v5/administration/access-preview" in paths
    assert "/api/v5/workspace/events" in paths


def test_agent_reply_modes_endpoint_exposes_runtime_boundary() -> None:
    result = agent_reply_modes()

    assert [item["mode_id"] for item in result["items"]] == ["fast", "normal", "thinking"]
    assert result["runtime_boundary"]["final_answer_owner"] == "agent_runtime"
    assert result["runtime_boundary"]["tool_returns_structured_result"] is True


def test_bot_user_access_payload_exposes_employee_agent_profile() -> None:
    item = SimpleNamespace(
        id=uuid4(),
        company_id=uuid4(),
        open_id="ou_member",
        display_name="员工A",
        role="member",
        access_scope="personal",
        is_active=True,
        settings={
            "source": "default_employee_agent",
            "permission_domains": [],
            "allowed_resources": [],
            "agent_profile": {
                "status": "active",
                "scope": "personal",
                "entrypoint": "feishu_bot",
                "activated_at": "2026-06-14T10:00:00+00:00",
                "first_chat_id": "oc_1",
                "first_message_id": "om_1",
            },
        },
    )

    payload = bot_user_access_payload(item)

    assert payload["source"] == "default_employee_agent"
    assert payload["agent_status"] == "active"
    assert payload["agent_scope"] == "personal"
    assert payload["agent_entrypoint"] == "feishu_bot"
    assert payload["agent_activated_at"] == "2026-06-14T10:00:00+00:00"
    assert payload["agent_first_chat_id"] == "oc_1"
    assert payload["agent_first_message_id"] == "om_1"
    assert payload["agent_activation_source"] == "default_employee_agent"
    assert payload["agent_requires_feishu_app_config"] is False
    assert payload["agent_launch_summary"] == "飞书消息入口按当前 AppConfig 状态运行"
    assert payload["agent_title"] == "员工A 的大飞哥"
    assert payload["agent_contract"] == "每个用户一个 Agent；Tool 全局共享；数据按身份边界收紧"
    assert payload["global_shared_tool_count"] == 9
    assert payload["global_shared_tool_summary"] == "9 个共享业务 Tool"
    assert payload["agent_can_call_all_business_tools"] is True
    assert payload["tool_access_policy"] == "all_business_tools_shared"
    assert payload["tool_sharing_model"] == "shared_business_tools_per_employee_agent"
    assert payload["global_shared_tools"] == [
        "ApprovalTool",
        "KnowledgeTool",
        "BitableTool",
        "ChatTool",
        "CalendarTool",
        "MeetingTool",
        "ReportTool",
        "AutomationTool",
        "PeopleTool",
    ]
    assert [item["business_tool"] for item in payload["global_shared_tool_statuses"]] == payload["global_shared_tools"]
    assert all(item["agent_can_call"] is True for item in payload["global_shared_tool_statuses"])
    assert all(item["data_boundary"] == "identity_scoped_tighten_only" for item in payload["global_shared_tool_statuses"])
    contract = payload["identity_permission_contract"]
    assert contract["agent_model"] == "per_user_personal_agent"
    assert contract["tool_model"] == "global_shared_business_tools"
    assert contract["shared_business_tools"] == payload["global_shared_tools"]
    assert contract["tool_data_boundary_rule"] == {
        "tools": "global_shared_capabilities",
        "data": "bounded_by_app_identity_and_user_identity",
        "digital_advisor_can_only_tighten": True,
    }
    assert contract["enterprise_resource_boundary"]["resource_owner"] == "feishu_custom_app_da_fei_ge"
    assert contract["enterprise_resource_boundary"]["app_identity_permissions_required"] is True
    assert contract["enterprise_resource_boundary"]["company_scope_required"] is True
    assert contract["enterprise_resource_boundary"]["role_scope_required"] is True
    assert contract["user_resource_boundary"]["authorization_policy"] == "resource_owner_authorized_only"
    assert contract["user_resource_boundary"]["owner_authorized_only"] is True
    assert contract["permission_enforcement"]["digital_advisor_can_only_tighten"] is True
    assert contract["permission_enforcement"]["can_escalate_original_permissions"] is False
    assert (
        payload["entrypoint_chain"]
        == "飞书消息入口 -> Message Gateway -> Agent Runtime -> Tool Router -> Tool -> 数据源/执行源 -> Agent Runtime -> 回复用户"
    )
    assert payload["runtime_contract"]["tool_decides_data_or_execution_source"] is True
    assert payload["runtime_contract"]["tool_returns_structured_result"] is True
    assert payload["runtime_contract"]["final_answer_owner"] == "agent_runtime"
    assert payload["runtime_contract"]["agent_runtime_direct_cli_or_api_access"] is False
    assert "Fast（无企业数据）" in payload["reply_mode_summary"]
    assert [item["mode_id"] for item in payload["reply_modes"]] == ["fast", "normal", "thinking"]
    assert payload["enterprise_identity_boundary"] == "App Identity + Company Scope + Role Scope：member/personal"
    assert payload["enterprise_identity_constraints"] == ["app_identity", "company_scope", "role_scope"]
    assert payload["enterprise_resource_boundary"]["resource_owner"] == "feishu_custom_app_da_fei_ge"
    assert payload["enterprise_resource_boundary"]["company_scope"] == str(item.company_id)
    assert payload["enterprise_resource_boundary"]["role_scope"] == {
        "role": "member",
        "access_scope": "personal",
        "domains": [],
    }
    assert payload["enterprise_resource_boundary"]["can_exceed_feishu_app_permissions"] is False
    assert payload["user_identity_boundary"] == "User Identity：未授权前不读取个人资源"
    assert payload["user_identity_constraints"] == ["resource_owner_authorization"]
    assert payload["user_identity_supported_resources"] == [
        "personal_feishu",
        "external_mail",
        "personal_dingtalk",
        "personal_wechat",
    ]
    assert payload["user_resource_boundary"]["can_exceed_original_authorization"] is False
    assert payload["data_permission_policy"] == "只能收紧，不能突破 App Identity 或 User Identity"
    assert payload["digital_advisor_permission_policy"] == "tighten_only"
    assert payload["cannot_escalate_original_permissions"] is True
    assert payload["authorized_user_resource_count"] == 0
    assert payload["pending_user_resource_count"] == 1
    assert payload["personal_authorization_next_action"] == "等待本人一次整体授权：用户级能力包"
    assert payload["agent_next_action"] == "首次使用用户级能力时引导本人一次整体授权"
    assert payload["user_identity_summary"] == "未授权用户级能力包；首次使用个人能力时引导本人一次整体授权"
    assert [item["resource_type"] for item in payload["user_identity_authorizations"]] == [
        "user_identity_bundle",
    ]
    assert all(item["authorization_owner"] == "resource_owner" for item in payload["user_identity_authorizations"])
    assert all(item["owner_open_id"] == "ou_member" for item in payload["user_identity_authorizations"])
    assert all(item["can_escalate_original_permissions"] is False for item in payload["user_identity_authorizations"])
    bundle_auth = payload["user_identity_authorizations"][0]
    assert bundle_auth["authorization_model"] == "bundle_authorization"
    assert bundle_auth["covered_resources"] == ["personal_feishu", "external_mail", "personal_dingtalk", "personal_wechat"]
    assert bundle_auth["authorization_actions"] == [
        {
            "label": "授权个人能力包",
            "channel": "feishu_oauth",
            "authorization_flow": "feishu_in_app_oauth",
            "url": f"http://127.0.0.1:8000/api/user-identity/oauth/feishu/start?company_id={item.company_id}&open_id=ou_member",
            "start_endpoint": "/api/user-identity/oauth/feishu/start",
            "callback_endpoint": "/api/feishu/oauth/callback",
            "fallback_debug_flow": "feishu_cli_split_flow",
            "fallback_debug_url": f"http://127.0.0.1:8000/user-auth/feishu-cli?company_id={item.company_id}&open_id=ou_member",
            "owner_open_id": "ou_member",
            "authorization_model": "bundle_authorization",
            "covered_resources": ["personal_feishu", "external_mail", "personal_dingtalk", "personal_wechat"],
            "can_escalate_original_permissions": False,
            "employee_reachable": False,
            "local_only": True,
            "api_base_url_status": {
                "api_base_url": "http://127.0.0.1:8000",
                "api_base_url_scheme": "http",
                "api_base_url_host": "127.0.0.1",
                "employee_reachable": False,
                "local_only": True,
                "production_requirement": "Set API_BASE_URL to a public HTTPS origin before employee rollout.",
            },
        }
    ]


def test_bot_user_access_payload_exposes_user_identity_authorizations() -> None:
    item = SimpleNamespace(
        id=uuid4(),
        company_id=uuid4(),
        open_id="ou_member",
        display_name="员工A",
        role="member",
        access_scope="personal",
        is_active=True,
        settings={
            "user_identity_authorizations": {
                "external_mail": {"status": "authorized", "owner_open_id": "ou_member"},
            },
        },
    )

    payload = bot_user_access_payload(item)

    assert payload["user_identity_summary"].startswith("已授权：用户级能力包")
    bundle = next(item for item in payload["user_identity_authorizations"] if item["resource_type"] == "user_identity_bundle")
    assert bundle["status"] == "authorized"
    assert bundle["owner_open_id"] == "ou_member"
    assert bundle["needs_authorization"] is False
    assert bundle["authorization_policy"] == "owner_granted_tighten_only"
    assert bundle["authorization_model"] == "bundle_authorization"
    assert bundle["covered_resources"] == ["personal_feishu", "external_mail", "personal_dingtalk", "personal_wechat"]
    assert payload["authorized_user_resource_count"] == 1
    assert payload["user_identity_boundary"] == "User Identity：本人已授权 用户级能力包"


def test_bot_user_access_payload_exposes_latest_tool_execution_evidence() -> None:
    created_at = datetime(2026, 6, 14, 10, 30, tzinfo=UTC)
    item = SimpleNamespace(
        id=uuid4(),
        company_id=uuid4(),
        open_id="ou_member",
        display_name="员工A",
        role="member",
        access_scope="personal",
        is_active=True,
        settings={},
    )

    payload = bot_user_access_payload(
        item,
        latest_gateway_message={
            "message_id": "om_1",
            "chat_id": "oc_1",
            "status": "handled",
            "handled": True,
            "used_agent_runtime": True,
            "final_answer_owner": "agent_runtime",
            "route_path": "calendar_qa",
            "route_label": "日程问答",
            "reply_mode_id": "normal",
            "reply_mode_label": "normal（实时企业数据）",
            "reply_mode_data_requirement": "MCP/CLI / 单 Tool 实时企业数据",
            "thinking_notice_sent": False,
            "authorization_card_sent": True,
            "user_identity_authorization_required": True,
            "user_identity_authorization_actions": [
                {
                    "resource_type": "user_identity_bundle",
                    "label": "授权个人能力包",
                    "url": "http://127.0.0.1:8000/api/user-identity/oauth/feishu/start?company_id=x&open_id=ou_member",
                }
            ],
            "required_user_identity_resources": ["user_identity_bundle"],
            "authorization_owner_open_id": "ou_member",
            "authorization_action_count": 1,
            "agent_runtime_step_count": 3,
            "agent_runtime_tool_steps": [
                {
                    "name": "calendar_qa",
                    "business_tool": "CalendarTool",
                    "enterprise_identity_boundary": "App Identity + Company Scope + Role Scope",
                    "user_identity_boundary": "User Identity + Resource Owner Authorization",
                    "cannot_escalate_original_permissions": True,
                }
            ],
            "gateway_chain": [
                "feishu_message_entrypoint",
                "message_gateway",
                "agent_runtime",
                "tool_router",
                "tool",
                "data_or_execution_source",
                "tool_structured_result",
                "agent_runtime_final_answer",
                "message_gateway_reply",
            ],
            "created_at": created_at.isoformat(),
        },
        latest_tool_execution={
            "tool_name": "calendar_qa",
            "business_tool": "CalendarTool",
            "status": "success",
            "provider": "feishu_mcp",
            "execution_source": "MCP -> CLI -> Feishu",
            "data_source": None,
            "tool_returns_structured_result": True,
            "final_answer_owner": "agent_runtime",
            "data_permission_model": "identity_scoped_tighten_only",
            "enterprise_identity_boundary": "App Identity + Company Scope + Role Scope",
            "user_identity_boundary": "User Identity + Resource Owner Authorization",
            "cannot_escalate_original_permissions": True,
            "cli_profile": "company-gaustek",
            "created_at": created_at.isoformat(),
        },
    )

    assert payload["latest_gateway_message_summary"] == "handled · 日程问答 · normal（实时企业数据）"
    assert payload["latest_gateway_status"] == "handled"
    assert payload["latest_gateway_route_path"] == "calendar_qa"
    assert payload["latest_gateway_reply_mode_label"] == "normal（实时企业数据）"
    assert payload["latest_gateway_used_agent_runtime"] is True
    assert payload["latest_gateway_final_answer_owner"] == "agent_runtime"
    assert payload["latest_gateway_tool_summary"] == "CalendarTool"
    assert payload["latest_gateway_authorization_summary"] == "user_identity_bundle · owner=ou_member · 1 个授权入口"
    assert payload["latest_gateway_thinking_notice_sent"] is False
    assert payload["latest_gateway_authorization_card_sent"] is True
    assert payload["latest_gateway_authorization_required"] is True
    assert payload["latest_gateway_required_user_identity_resources"] == ["user_identity_bundle"]
    assert payload["latest_gateway_authorization_owner_open_id"] == "ou_member"
    assert payload["latest_gateway_authorization_action_count"] == 1
    assert payload["latest_gateway_created_at"] == created_at.isoformat()
    assert payload["latest_tool_execution_summary"] == "CalendarTool · success · MCP -> CLI -> Feishu"
    assert payload["latest_tool_business_tool"] == "CalendarTool"
    assert payload["latest_tool_status"] == "success"
    assert payload["latest_tool_source"] == "MCP -> CLI -> Feishu"
    assert payload["latest_tool_final_answer_owner"] == "agent_runtime"
    assert payload["latest_tool_data_permission_model"] == "identity_scoped_tighten_only"
    assert payload["latest_tool_enterprise_identity_boundary"] == "App Identity + Company Scope + Role Scope"
    assert payload["latest_tool_user_identity_boundary"] == "User Identity + Resource Owner Authorization"
    assert payload["latest_tool_cannot_escalate_original_permissions"] is True
    assert payload["latest_tool_cli_profile"] == "company-gaustek"
    assert payload["latest_tool_created_at"] == created_at.isoformat()


def test_recent_gateway_messages_by_actor_returns_employee_agent_gateway_evidence() -> None:
    company_id = uuid4()
    latest = SimpleNamespace(
        company_id=company_id,
        actor="ou_member",
        target_id="om_latest",
        action="gateway.feishu.message",
        target_type="gateway_message",
        payload={
            "message_id": "om_latest",
            "chat_id": "oc_1",
            "chat_type": "p2p",
            "status": "handled",
            "handled": True,
            "used_agent_runtime": True,
            "final_answer_owner": "agent_runtime",
            "route_path": "company_qa",
            "route_label": "公司级问答",
            "reply_mode_id": "thinking",
            "reply_mode_label": "Thinking（需要分析数据）",
            "reply_mode_data_requirement": "WorkEvent / Knowledge / Memory / 多 Tool 分析",
            "thinking_notice_sent": True,
            "user_identity_card_sent": False,
            "user_identity_required": True,
            "user_identity_action_summaries": [{"resource_type": "user_identity_bundle", "label": "授权个人能力包"}],
            "user_identity_required_resources": ["user_identity_bundle"],
            "user_identity_owner_open_id": "ou_member",
            "user_identity_action_count": 1,
            "agent_runtime_step_count": 3,
            "agent_runtime_tool_steps": [{"name": "company_qa", "business_tool": "ReportTool"}],
            "gateway_chain": ["feishu_message_entrypoint", "message_gateway", "agent_runtime"],
        },
        created_at=datetime(2026, 6, 14, 11, 0, tzinfo=UTC),
    )
    ignored_other_user = SimpleNamespace(
        company_id=company_id,
        actor="ou_other",
        target_id="om_other",
        action="gateway.feishu.message",
        target_type="gateway_message",
        payload={"status": "handled"},
        created_at=datetime(2026, 6, 14, 10, 0, tzinfo=UTC),
    )

    class FakeScalarResult:
        def all(self):
            return [latest, ignored_other_user]

    class FakeDb:
        def scalars(self, query):
            return FakeScalarResult()

    result = recent_gateway_messages_by_actor(FakeDb(), company_id=company_id, open_ids={"ou_member"})

    assert list(result) == ["ou_member"]
    assert result["ou_member"]["message_id"] == "om_latest"
    assert result["ou_member"]["used_agent_runtime"] is True
    assert result["ou_member"]["reply_mode_label"] == "Thinking（需要分析数据）"
    assert result["ou_member"]["required_user_identity_resources"] == ["user_identity_bundle"]
    assert result["ou_member"]["authorization_owner_open_id"] == "ou_member"
    assert result["ou_member"]["authorization_action_count"] == 1
    assert result["ou_member"]["agent_runtime_tool_steps"] == [{"name": "company_qa", "business_tool": "ReportTool"}]


def test_v5_os_overview_exposes_interaction_entrypoints(monkeypatch) -> None:
    class FakeScalarResult:
        def all(self):
            return []

    class FakeDb:
        def scalars(self, query):
            return FakeScalarResult()

    monkeypatch.setattr("app.services.v5_administration.count_company_items", lambda *args, **kwargs: 0)
    monkeypatch.setattr("app.services.v5_administration.count_v5_users", lambda *args, **kwargs: 0)
    monkeypatch.setattr("app.services.v5_administration._active_feishu_app_count", lambda db: 1)
    monkeypatch.setattr("app.services.v5_administration._validated_feishu_app_count", lambda db: 1)
    monkeypatch.setattr("app.services.v5_administration.settings.feishu_bot_ai_mode_enabled", True)
    monkeypatch.setattr("app.services.v5_administration.shutil.which", lambda name: "/usr/local/bin/lark-cli")
    monkeypatch.setattr(
        "app.services.v5_administration.run_feishu_cli_doctor_offline",
        lambda path: _fake_cli_doctor_result(),
    )

    result = v5_os_overview(FakeDb())

    assert result["entrypoints"]["feishu_bot"]["name"] == "大飞哥"
    assert result["entrypoints"]["feishu_bot"]["ai_mode_enabled"] is True
    assert result["entrypoints"]["feishu_bot"]["agent_runtime_fallback"] is True
    assert result["entrypoints"]["feishu_bot"]["identity_boundary"]["agent_model"] == "每个飞书用户一个专属 Agent"
    assert result["entrypoints"]["feishu_bot"]["identity_boundary"]["tool_data_boundary_rule"] == "工具是全局共享的；数据是有边界的"
    assert (
        result["entrypoints"]["feishu_bot"]["identity_boundary"]["enterprise_resource_boundary"]
        == "App Identity + Company Scope + Role Scope"
    )
    assert (
        result["entrypoints"]["feishu_bot"]["identity_boundary"]["enterprise_resource_policy"]
        == "企业级资源继承大飞哥自建应用权限，并按公司范围和角色权限继续收紧"
    )
    assert (
        result["entrypoints"]["feishu_bot"]["identity_boundary"]["user_resource_policy"]
        == "用户级资源只基于资源所有者本人授权；首次使用时按需引导授权"
    )
    assert result["entrypoints"]["admin_console"]["enabled"] is True
    assert result["entrypoints"]["ios_app"] == {"enabled": False, "status": "planned"}
    assert result["status"] == {"database": "ok"}
    assert result["release_readiness"]["status"] == "ready"
    assert result["release_readiness"]["blockers"] == []
    assert {
        item["key"]: item["status"] for item in result["release_readiness"]["checks"]
    }["feishu_boundary"] == "ready"


def test_v5_entrypoint_status_exposes_bot_console_and_ios_boundaries(monkeypatch) -> None:
    company_id = uuid4()
    app_id = uuid4()
    event_id = uuid4()
    resource_id = uuid4()

    app_config = SimpleNamespace(
        id=app_id,
        company_id=company_id,
        name="固势大飞哥",
        app_id="cli_gaustek",
        is_active=True,
        settings={
            "cli_profile": "v5-local-prod",
            "credential_status": "valid",
            "credential_validated_at": "2026-06-14T08:00:00+00:00",
        },
        created_at=datetime(2026, 6, 14, 8, 0, tzinfo=UTC),
    )
    event = SimpleNamespace(
        id=event_id,
        company_id=company_id,
        resource_id=resource_id,
        event_type="im.message.receive_v1",
        thread_id="oc_chat",
        title="业务群",
        occurred_at=datetime(2026, 6, 14, 8, 1, tzinfo=UTC),
        vector_status="skipped",
    )
    gateway_log = SimpleNamespace(
        id=uuid4(),
        company_id=company_id,
        actor="ou_owner",
        target_id="om_1",
        created_at=datetime(2026, 6, 14, 8, 2, tzinfo=UTC),
        payload={
            "message_id": "om_1",
            "chat_id": "oc_chat",
            "status": "handled",
            "handled": True,
            "used_agent_runtime": True,
            "final_answer_owner": "agent_runtime",
            "route_path": "bitable_qa",
            "route_label": "多维表格问答",
            "reply_mode_id": "thinking",
            "reply_mode_label": "Thinking（需要分析数据）",
            "reply_mode_data_requirement": "WorkEvent / Knowledge / Memory / 多 Tool 分析",
            "thinking_notice_sent": True,
            "authorization_card_sent": False,
            "agent_runtime_step_count": 3,
            "agent_runtime_tool_steps": [
                {
                    "name": "bitable_qa",
                    "status": "success",
                    "provider": "local",
                    "business_tool": "BitableTool",
                    "data_source": "PostgreSQL",
                    "execution_source": None,
                    "final_answer_owner": "agent_runtime",
                }
            ],
            "gateway_chain": [
                "feishu_message_entrypoint",
                "message_gateway",
                "agent_runtime",
                "tool_router",
                "tool",
                "data_or_execution_source",
                "tool_structured_result",
                "agent_runtime_final_answer",
                "message_gateway_reply",
            ],
        },
    )
    gateway_error_log = SimpleNamespace(
        id=uuid4(),
        company_id=company_id,
        actor="ou_owner",
        target_id="om_2",
        created_at=datetime(2026, 6, 14, 8, 3, tzinfo=UTC),
        payload={
            "message_id": "om_2",
            "chat_id": "oc_chat",
            "status": "handled",
            "handled": True,
            "used_agent_runtime": True,
            "final_answer_owner": "agent_runtime",
            "route_path": "feishu_approval_task_query",
            "route_label": "审批实时待办",
            "reply_mode_id": "normal",
            "reply_mode_label": "normal（实时企业数据）",
            "agent_runtime_step_count": 3,
            "agent_runtime_tool_steps": [
                {
                    "name": "feishu_approval_task_query",
                    "status": "error",
                    "business_tool": "ApprovalTool",
                    "data_source": None,
                    "execution_source": "MCP -> CLI -> Feishu",
                    "final_answer_owner": "agent_runtime",
                    "error": "lark-cli approval task query failed",
                }
            ],
            "gateway_chain": [
                "feishu_message_entrypoint",
                "message_gateway",
                "agent_runtime",
                "tool_router",
                "tool",
                "data_or_execution_source",
                "tool_structured_result",
                "agent_runtime_final_answer",
                "message_gateway_reply",
            ],
        },
    )

    class FakeScalarResult:
        def __init__(self, items):
            self.items = items

        def all(self):
            return self.items

    class FakeDb:
        def scalar(self, query):
            return 1

        def scalars(self, query):
            return FakeScalarResult([app_config, event] if False else [])

    gateway_logs = [gateway_log, gateway_error_log]
    scalars = [[app_config], [app_config], [event], gateway_logs, gateway_logs]

    class SequencedDb(FakeDb):
        def scalars(self, query):
            return FakeScalarResult(scalars.pop(0))

    monkeypatch.setattr("app.services.v5_administration.settings.feishu_bot_ai_mode_enabled", True)
    monkeypatch.setattr("app.services.v5_administration.shutil.which", lambda name: "/usr/local/bin/lark-cli")
    monkeypatch.setattr(
        "app.services.v5_administration.run_feishu_cli_doctor_offline",
        lambda path, profile=None: _fake_cli_doctor_result(
            output=(
                '{"ok": true, "checks": ['
                '{"name": "app_resolved", "status": "pass", "message": "app: cli_gaustek (feishu)"},'
                '{"name": "bot_identity", "status": "pass", "message": "Bot identity: ready"},'
                '{"name": "user_identity", "status": "pass", "message": "User identity: needs refresh"},'
                '{"name": "identity_ready", "status": "pass", "message": "at least one identity is available"}'
                "]}"
            )
        ),
    )

    result = v5_entrypoint_status(SequencedDb(), company_id=company_id)
    entrypoints = {item["key"]: item for item in result["entrypoints"]}

    assert (
        result["boundary"]["realtime_operation"]
        == "Message Gateway -> Agent Runtime -> Tool Router -> Tool -> 数据源/执行源 -> Agent Runtime -> Message Gateway"
    )
    assert result["boundary"]["data_or_execution_sources"] == [
        "MCP -> CLI -> Feishu",
        "API -> Feishu",
        "PostgreSQL",
        "Vector DB",
        "External Search",
    ]
    assert "所有 Tool 返回结构化结果" in result["boundary"]["tool_contract"]
    assert result["boundary"]["identity_boundary"]["tool_model"] == "9 个业务 Tool 全局共享，Agent 可以访问并调用所有 Tool"
    assert (
        result["boundary"]["identity_boundary"]["digital_advisor_permission_policy"]
        == "只能收紧权限，不能突破飞书 App 或用户原始授权范围"
    )
    assert "Agent Runtime -> CLI" in result["boundary"]["forbidden"]
    assert entrypoints["feishu_bot"]["status"] == "ready"
    assert entrypoints["feishu_bot"]["validated_app_count"] == 1
    assert entrypoints["feishu_bot"]["feishu_app_bootstrap_hint"]["status"] == "ready"
    assert entrypoints["feishu_bot"]["apps"][0]["cli_profile"] == "v5-local-prod"
    assert entrypoints["feishu_bot"]["apps"][0]["credential_status"] == "valid"
    assert entrypoints["feishu_bot"]["recent_events"][0]["event_type"] == "im.message.receive_v1"
    assert entrypoints["feishu_bot"]["gateway_runtime"] == {
        "recent_gateway_messages": 2,
        "agent_runtime_messages": 2,
        "tool_error_count": 1,
        "workflow_error_count": 0,
        "latest_error": {
            "created_at": "2026-06-14T08:03:00+00:00",
            "message_id": "om_2",
            "chat_id": "oc_chat",
            "route_path": "feishu_approval_task_query",
            "route_label": "审批实时待办",
            "reply_mode_label": "normal（实时企业数据）",
            "tool_summary": "ApprovalTool（error · MCP -> CLI -> Feishu）",
            "workflow_summary": "",
            "error": "lark-cli approval task query failed",
            "final_answer_owner": "agent_runtime",
        },
    }
    gateway_message = entrypoints["feishu_bot"]["recent_gateway_messages"][0]
    assert gateway_message["message_id"] == "om_1"
    assert gateway_message["route_path"] == "bitable_qa"
    assert gateway_message["route_label"] == "多维表格问答"
    assert gateway_message["used_agent_runtime"] is True
    assert gateway_message["final_answer_owner"] == "agent_runtime"
    assert gateway_message["tool_summary"] == "BitableTool（success · PostgreSQL）"
    assert gateway_message["agent_runtime_tool_steps"][0]["data_source"] == "PostgreSQL"
    assert gateway_message["gateway_chain"][-1] == "message_gateway_reply"
    assert entrypoints["feishu_bot"]["cli_readiness"]["status"] == "ready"
    assert entrypoints["feishu_bot"]["cli_readiness"]["identity_status"]["bot_identity"] == {
        "status": "pass",
        "message": "Bot identity: ready",
    }
    assert entrypoints["feishu_bot"]["cli_readiness"]["identity_status"]["app_resolved"] == {
        "status": "pass",
        "message": "app: cli_gaustek (feishu)",
    }
    assert entrypoints["feishu_bot"]["cli_readiness"]["identity_status"]["user_identity"] == {
        "status": "pass",
        "message": "User identity: needs refresh",
    }
    assert entrypoints["feishu_bot"]["identity_boundary"]["user_resource_boundary"] == (
        "User Identity + Resource Owner Authorization"
    )
    assert entrypoints["admin_console"]["url"] == "/console"
    assert entrypoints["ios_app"]["status"] == "planned"


def test_v5_os_overview_keeps_entrypoints_when_database_is_unavailable(monkeypatch) -> None:
    class FakeDb:
        def scalars(self, query):
            raise OperationalError("select companies", {}, Exception("db down"))

    monkeypatch.setattr("app.services.v5_administration.settings.feishu_bot_ai_mode_enabled", True)
    monkeypatch.setattr("app.services.v5_administration.shutil.which", lambda name: "/usr/local/bin/lark-cli")
    monkeypatch.setattr(
        "app.services.v5_administration.run_feishu_cli_doctor_offline",
        lambda path: _fake_cli_doctor_result(),
    )

    result = v5_os_overview(FakeDb())

    assert result["status"]["database"] == "unavailable"
    assert "db down" in result["status"]["error"]
    assert result["total_counts"]["companies"] == 0
    assert result["companies"] == []
    assert result["entrypoints"]["feishu_bot"]["ai_mode_enabled"] is True
    assert result["release_readiness"]["status"] == "degraded"
    assert result["release_readiness"]["blockers"] == ["database_unavailable"]
    assert {
        item["key"]: item["status"] for item in result["release_readiness"]["checks"]
    }["database"] == "blocked"


def test_v5_os_overview_blocks_release_when_feishu_cli_is_missing(monkeypatch) -> None:
    class FakeScalarResult:
        def all(self):
            return []

    class FakeDb:
        def scalars(self, query):
            return FakeScalarResult()

    monkeypatch.setattr("app.services.v5_administration.count_company_items", lambda *args, **kwargs: 0)
    monkeypatch.setattr("app.services.v5_administration.count_v5_users", lambda *args, **kwargs: 0)
    monkeypatch.setattr("app.services.v5_administration._active_feishu_app_count", lambda db: 1)
    monkeypatch.setattr("app.services.v5_administration._validated_feishu_app_count", lambda db: 1)
    monkeypatch.setattr("app.services.v5_administration.settings.feishu_bot_ai_mode_enabled", True)
    monkeypatch.setattr("app.services.v5_administration.shutil.which", lambda name: None)

    result = v5_os_overview(FakeDb())
    checks = {item["key"]: item for item in result["release_readiness"]["checks"]}

    assert result["release_readiness"]["status"] == "degraded"
    assert result["release_readiness"]["blockers"] == ["feishu_cli_missing"]
    assert checks["feishu_cli"]["status"] == "blocked"
    assert "lark-cli" in checks["feishu_cli"]["detail"]


def test_v5_os_overview_blocks_release_when_feishu_app_config_is_missing(monkeypatch) -> None:
    class FakeScalarResult:
        def all(self):
            return []

    class FakeDb:
        def scalars(self, query):
            return FakeScalarResult()

    monkeypatch.setattr("app.services.v5_administration.count_company_items", lambda *args, **kwargs: 0)
    monkeypatch.setattr("app.services.v5_administration.count_v5_users", lambda *args, **kwargs: 0)
    monkeypatch.setattr("app.services.v5_administration._active_feishu_app_count", lambda db: 0)
    monkeypatch.setattr("app.services.v5_administration._validated_feishu_app_count", lambda db: 0)
    monkeypatch.setattr("app.services.v5_administration.settings.feishu_bot_ai_mode_enabled", True)
    monkeypatch.setattr("app.services.v5_administration.shutil.which", lambda name: "/usr/local/bin/lark-cli")
    monkeypatch.setattr(
        "app.services.v5_administration.run_feishu_cli_doctor_offline",
        lambda path, **kwargs: _fake_cli_doctor_result(
            output=(
                '{"ok": true, "checks": ['
                '{"name": "app_resolved", "status": "pass", "message": "app: cli_gaustek (feishu)"},'
                '{"name": "bot_identity", "status": "pass", "message": "Bot identity: ready"},'
                '{"name": "identity_ready", "status": "pass", "message": "at least one identity is available"}'
                "]}"
            )
        ),
    )

    result = v5_os_overview(FakeDb())
    checks = {item["key"]: item for item in result["release_readiness"]["checks"]}

    assert result["release_readiness"]["status"] == "degraded"
    assert result["release_readiness"]["blockers"] == ["feishu_app_config_missing"]
    assert checks["feishu_app_config"]["status"] == "blocked"
    assert "大飞哥无法接收事件" in checks["feishu_app_config"]["detail"]
    assert "App Secret" in checks["feishu_app_config"]["required_action"]
    assert checks["feishu_app_config"]["bootstrap_hint"]["app_id"] == "cli_gaustek"
    assert checks["feishu_app_config"]["bootstrap_hint"]["cli_profile"] == "v5-local-prod"
    assert checks["feishu_app_config"]["bootstrap_hint"]["secret_required"] is True
    assert checks["feishu_app_config"]["bootstrap_hint"]["tenant_token_validation_required"] is True
    assert checks["feishu_app_config"]["identity_boundary"] == "App Identity + Company Scope + Role Scope"
    assert checks["feishu_app_config"]["sync_boundary"] == "Sync Engine -> API Client -> Feishu -> PostgreSQL"


def test_v5_os_overview_blocks_release_when_feishu_app_credentials_are_unvalidated(monkeypatch) -> None:
    class FakeScalarResult:
        def all(self):
            return []

    class FakeDb:
        def scalars(self, query):
            return FakeScalarResult()

    monkeypatch.setattr("app.services.v5_administration.count_company_items", lambda *args, **kwargs: 0)
    monkeypatch.setattr("app.services.v5_administration.count_v5_users", lambda *args, **kwargs: 0)
    monkeypatch.setattr("app.services.v5_administration._active_feishu_app_count", lambda db: 1)
    monkeypatch.setattr("app.services.v5_administration._validated_feishu_app_count", lambda db: 0)
    monkeypatch.setattr("app.services.v5_administration.settings.feishu_bot_ai_mode_enabled", True)
    monkeypatch.setattr("app.services.v5_administration.shutil.which", lambda name: "/usr/local/bin/lark-cli")
    monkeypatch.setattr(
        "app.services.v5_administration.run_feishu_cli_doctor_offline",
        lambda path, **kwargs: _fake_cli_doctor_result(
            output=(
                '{"ok": true, "checks": ['
                '{"name": "app_resolved", "status": "pass", "message": "app: cli_gaustek (feishu)"},'
                '{"name": "bot_identity", "status": "pass", "message": "Bot identity: ready"},'
                '{"name": "identity_ready", "status": "pass", "message": "at least one identity is available"}'
                "]}"
            )
        ),
    )

    result = v5_os_overview(FakeDb())
    checks = {item["key"]: item for item in result["release_readiness"]["checks"]}

    assert result["release_readiness"]["status"] == "degraded"
    assert result["release_readiness"]["blockers"] == ["feishu_app_credentials_unvalidated"]
    assert checks["feishu_app_config"]["status"] == "blocked"
    assert "尚未通过 tenant token 校验" in checks["feishu_app_config"]["detail"]
    assert "tenant-access-token" in checks["feishu_app_config"]["required_action"]
    assert checks["feishu_app_config"]["bootstrap_hint"]["status"] == "credentials_unvalidated"
    assert checks["feishu_app_config"]["bootstrap_hint"]["app_id"] == "cli_gaustek"
    assert checks["feishu_app_config"]["bootstrap_hint"]["secret_required"] is True


def test_v5_os_overview_blocks_release_when_feishu_cli_identity_is_unavailable(monkeypatch) -> None:
    class FakeScalarResult:
        def all(self):
            return []

    class FakeDb:
        def scalars(self, query):
            return FakeScalarResult()

    doctor_output = (
        '{"ok": false, "checks": ['
        '{"name": "identity_ready", "status": "fail", "message": "no usable bot or user identity is available"}'
        "]}"
    )
    monkeypatch.setattr("app.services.v5_administration.count_company_items", lambda *args, **kwargs: 0)
    monkeypatch.setattr("app.services.v5_administration.count_v5_users", lambda *args, **kwargs: 0)
    monkeypatch.setattr("app.services.v5_administration._active_feishu_app_count", lambda db: 1)
    monkeypatch.setattr("app.services.v5_administration._validated_feishu_app_count", lambda db: 1)
    monkeypatch.setattr("app.services.v5_administration.settings.feishu_bot_ai_mode_enabled", True)
    monkeypatch.setattr("app.services.v5_administration.shutil.which", lambda name: "/usr/local/bin/lark-cli")
    monkeypatch.setattr(
        "app.services.v5_administration.run_feishu_cli_doctor_offline",
        lambda path: _fake_cli_doctor_result(returncode=1, output=doctor_output),
    )

    result = v5_os_overview(FakeDb())
    checks = {item["key"]: item for item in result["release_readiness"]["checks"]}

    assert result["release_readiness"]["status"] == "degraded"
    assert result["release_readiness"]["blockers"] == ["feishu_cli_identity_unavailable"]
    assert checks["feishu_cli"]["status"] == "blocked"
    assert "identity_ready=fail" in checks["feishu_cli"]["detail"]


def test_v5_os_overview_blocks_release_when_company_cli_profile_is_unavailable(monkeypatch) -> None:
    class FakeScalarResult:
        def __init__(self, items):
            self.items = items

        def all(self):
            return self.items

    class FakeDb:
        def __init__(self):
            self.calls = 0

        def scalars(self, query):
            self.calls += 1
            if self.calls == 1:
                return FakeScalarResult([])
            return FakeScalarResult(
                [
                    SimpleNamespace(
                        name="固势大飞哥",
                        settings={"cli_profile": "company-gaustek"},
                    )
                ]
            )

    doctor_calls = []

    def fake_doctor(path, *, profile=None):
        doctor_calls.append(profile)
        if profile == "company-gaustek":
            return _fake_cli_doctor_result(
                returncode=1,
                output='{"ok": false, "checks": [{"name": "identity_ready", "status": "fail", "message": "profile missing"}]}',
            )
        return _fake_cli_doctor_result()

    monkeypatch.setattr("app.services.v5_administration.count_company_items", lambda *args, **kwargs: 0)
    monkeypatch.setattr("app.services.v5_administration.count_v5_users", lambda *args, **kwargs: 0)
    monkeypatch.setattr("app.services.v5_administration._active_feishu_app_count", lambda db: 1)
    monkeypatch.setattr("app.services.v5_administration._validated_feishu_app_count", lambda db: 1)
    monkeypatch.setattr("app.services.v5_administration.settings.feishu_bot_ai_mode_enabled", True)
    monkeypatch.setattr("app.services.v5_administration.shutil.which", lambda name: "/usr/local/bin/lark-cli")
    monkeypatch.setattr("app.services.v5_administration.run_feishu_cli_doctor_offline", fake_doctor)

    result = v5_os_overview(FakeDb())
    checks = {item["key"]: item for item in result["release_readiness"]["checks"]}

    assert doctor_calls == [None, "company-gaustek"]
    assert result["release_readiness"]["status"] == "degraded"
    assert result["release_readiness"]["blockers"] == ["feishu_cli_profile_unavailable"]
    assert checks["feishu_cli"]["status"] == "blocked"
    assert "固势大飞哥:company-gaustek" in checks["feishu_cli"]["detail"]


def test_v5_os_overview_blocks_release_when_feishu_bot_ai_fallback_is_disabled(monkeypatch) -> None:
    class FakeScalarResult:
        def all(self):
            return []

    class FakeDb:
        def scalars(self, query):
            return FakeScalarResult()

    monkeypatch.setattr("app.services.v5_administration.count_company_items", lambda *args, **kwargs: 0)
    monkeypatch.setattr("app.services.v5_administration.count_v5_users", lambda *args, **kwargs: 0)
    monkeypatch.setattr("app.services.v5_administration._active_feishu_app_count", lambda db: 1)
    monkeypatch.setattr("app.services.v5_administration._validated_feishu_app_count", lambda db: 1)
    monkeypatch.setattr("app.services.v5_administration.settings.feishu_bot_ai_mode_enabled", False)
    monkeypatch.setattr("app.services.v5_administration.shutil.which", lambda name: "/usr/local/bin/lark-cli")
    monkeypatch.setattr(
        "app.services.v5_administration.run_feishu_cli_doctor_offline",
        lambda path: _fake_cli_doctor_result(),
    )

    result = v5_os_overview(FakeDb())
    checks = {item["key"]: item for item in result["release_readiness"]["checks"]}

    assert result["release_readiness"]["status"] == "degraded"
    assert result["release_readiness"]["blockers"] == ["feishu_bot_ai_disabled"]
    assert checks["feishu_bot"]["status"] == "attention"
    assert checks["feishu_bot"]["detail"] == "AI 兜底未开启"


def test_list_tool_executions_returns_filtered_audit_payloads() -> None:
    company_id = uuid4()
    success = SimpleNamespace(
        id=uuid4(),
        company_id=company_id,
        target_id="feishu_task_update",
        actor="ou_owner",
        action="tool.task.update",
        payload={
            "provider": "feishu_mcp",
            "business_tool": "AutomationTool",
            "status": "success",
            "error": None,
            "duration_ms": 12,
            "required_permissions": ["task:write"],
            "supports_write": True,
            "data_source": None,
            "execution_source": "MCP -> CLI -> Feishu",
            "source_chain": ["MCP", "CLI", "Feishu"],
            "source_kind": "execution_source",
            "tool_decides_data_or_execution_source": True,
            "tool_returns_structured_result": True,
            "final_answer_owner": "agent_runtime",
            "data_permission_model": "identity_scoped_tighten_only",
            "data_boundary_policy": "tool_global_data_identity_bounded",
            "company_scope": str(company_id),
            "role_scope": {"role": "owner", "access_scope": "company", "domains": [], "company_data_allowed": True},
            "enterprise_identity": "app_identity",
            "enterprise_identity_boundary": "App Identity + Company Scope + Role Scope",
            "enterprise_identity_constraints": ["app_identity", "company_scope", "role_scope"],
            "enterprise_company_scope": str(company_id),
            "enterprise_role_scope": {"role": "owner", "access_scope": "company", "domains": [], "company_data_allowed": True},
            "can_exceed_feishu_app_permissions": False,
            "data_boundary_enforcement": {
                "tool_is_global_shared": True,
                "enterprise_resources": ["app_identity", "company_scope", "role_scope"],
                "user_resources": ["resource_owner_authorization"],
                "digital_advisor_can_only_tighten": True,
                "can_escalate_original_permissions": False,
            },
            "digital_advisor_permission_policy": "tighten_only",
            "cannot_escalate_original_permissions": True,
            "execution_chain": ["agent_runtime", "tool_router", "tool", "mcp", "cli", "feishu"],
            "mcp_provider": "feishu_mcp",
            "preferred_execution_engine": "lark_cli",
            "realtime_policy": "tool_mcp_cli_only",
            "api_role": "sync_engine_only",
            "agent_runtime_direct_access": False,
            "sync_engine_mcp_access_allowed": False,
            "cli_profile": "company-gaustek",
            "cli_profile_source": "feishu_app_config",
            "write_target": {
                "operation": "task.update",
                "identifiers": {"task_guid": "task_1"},
                "update_fields": ["summary"],
                "param_keys": ["summary", "task_guid", "update_fields"],
            },
            "chat_id": "oc_1",
        },
        created_at=datetime(2026, 6, 12, 8, 0, tzinfo=UTC),
    )
    denied = SimpleNamespace(
        id=uuid4(),
        company_id=company_id,
        target_id="mail_qa",
        actor="ou_member",
        action="tool.mail_qa.read",
        payload={"provider": "local", "status": "denied"},
        created_at=datetime(2026, 6, 12, 8, 1, tzinfo=UTC),
    )

    class FakeScalarResult:
        def all(self):
            return [success, denied]

    class FakeDb:
        def scalars(self, query):
            return FakeScalarResult()

    result = list_tool_executions(company_id=company_id, status="success", db=FakeDb())

    assert result["counts"] == {"success": 1}
    assert result["items"] == [
        {
            "id": str(success.id),
            "company_id": str(company_id),
            "tool_name": "feishu_task_update",
            "actor": "ou_owner",
            "action": "tool.task.update",
            "provider": "feishu_mcp",
            "business_tool": "AutomationTool",
            "status": "success",
            "error": None,
            "duration_ms": 12,
            "required_permissions": ["task:write"],
            "supports_write": True,
            "data_source": None,
            "execution_source": "MCP -> CLI -> Feishu",
            "source_chain": ["MCP", "CLI", "Feishu"],
            "source_kind": "execution_source",
            "tool_decides_data_or_execution_source": True,
            "tool_returns_structured_result": True,
            "final_answer_owner": "agent_runtime",
            "data_permission_model": "identity_scoped_tighten_only",
            "data_boundary_policy": "tool_global_data_identity_bounded",
            "company_scope": str(company_id),
            "role_scope": {"role": "owner", "access_scope": "company", "domains": [], "company_data_allowed": True},
            "enterprise_identity": "app_identity",
            "enterprise_identity_boundary": "App Identity + Company Scope + Role Scope",
            "enterprise_identity_constraints": ["app_identity", "company_scope", "role_scope"],
            "enterprise_company_scope": str(company_id),
            "enterprise_role_scope": {"role": "owner", "access_scope": "company", "domains": [], "company_data_allowed": True},
            "can_exceed_feishu_app_permissions": False,
            "user_identity": None,
            "user_identity_boundary": None,
            "user_identity_owner_open_id": None,
            "can_exceed_user_original_authorization": None,
            "data_boundary_enforcement": {
                "tool_is_global_shared": True,
                "enterprise_resources": ["app_identity", "company_scope", "role_scope"],
                "user_resources": ["resource_owner_authorization"],
                "digital_advisor_can_only_tighten": True,
                "can_escalate_original_permissions": False,
            },
            "digital_advisor_permission_policy": "tighten_only",
            "cannot_escalate_original_permissions": True,
            "execution_chain": ["agent_runtime", "tool_router", "tool", "mcp", "cli", "feishu"],
            "mcp_provider": "feishu_mcp",
            "preferred_execution_engine": "lark_cli",
            "realtime_policy": "tool_mcp_cli_only",
            "api_role": "sync_engine_only",
            "agent_runtime_direct_access": False,
            "sync_engine_mcp_access_allowed": False,
            "cli_profile": "company-gaustek",
            "cli_profile_source": "feishu_app_config",
            "write_mode": None,
            "dry_run": None,
            "confirmed": None,
            "has_confirmation_token": None,
            "write_target": {
                "operation": "task.update",
                "identifiers": {"task_guid": "task_1"},
                "update_fields": ["summary"],
                "param_keys": ["summary", "task_guid", "update_fields"],
            },
            "write_target_summary": "task.update / task_guid=task_1 / update_fields=summary",
            "chat_id": "oc_1",
            "created_at": "2026-06-12T08:00:00+00:00",
        }
    ]


def test_execute_tool_routes_admin_dry_run_through_tool_router(monkeypatch) -> None:
    company_id = uuid4()
    captured = {}

    def fake_execute_agent_tool(context, request):
        captured["context"] = context
        captured["request"] = request
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.FEISHU_MCP,
            status=ToolExecutionStatus.SUCCESS,
            answer="Dry-run only. confirmation_token: token_1",
            metadata={
                "write_mode": "dry_run",
                "dry_run": True,
                "confirmed": False,
                "has_confirmation_token": False,
                "write_target": {
                    "operation": "bitable_record.update",
                    "identifiers": {"app_token": "app_1", "table_id": "tbl_1", "record_id": "rec_1"},
                    "field_keys": ["状态"],
                    "param_keys": ["app_token", "fields", "record_id", "table_id"],
                },
            },
        )

    monkeypatch.setattr("app.services.v5_tool_admin.execute_agent_tool", fake_execute_agent_tool)

    result = execute_tool(
        "feishu_bitable_record_update",
        ToolExecutionRequest(
            question="更新客户状态",
            params={
                "dry_run": True,
                "app_token": "app_1",
                "table_id": "tbl_1",
                "record_id": "rec_1",
                "fields": {"状态": "跟进中"},
                "confirmation_token": "must-not-leak",
            },
            actor_open_id="ou_admin",
        ),
        company_id=company_id,
        db=object(),
    )

    assert captured["context"].company_id == company_id
    assert captured["context"].actor.role == "admin"
    assert captured["context"].actor.open_id == "ou_admin"
    assert captured["request"].tool_name == "feishu_bitable_record_update"
    assert captured["request"].params["confirmation_token"] == "must-not-leak"
    assert result["status"] == "success"
    assert result["metadata"]["write_target_summary"] == (
        "bitable_record.update / app_token=app_1 / record_id=rec_1 / table_id=tbl_1 / fields=状态"
    )
    assert "confirmation_token" not in result["metadata"]["write_target"]["param_keys"]


def test_execute_tool_returns_not_found_for_unknown_tool(monkeypatch) -> None:
    def fake_execute_agent_tool(context, request):
        raise ValueError("Unknown agent tool: missing_tool")

    monkeypatch.setattr("app.services.v5_tool_admin.execute_agent_tool", fake_execute_agent_tool)

    try:
        execute_tool(
            "missing_tool",
            ToolExecutionRequest(),
            company_id=uuid4(),
            db=object(),
        )
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404
        assert "Unknown agent tool" in str(getattr(exc, "detail", ""))
    else:
        raise AssertionError("expected unknown tool to raise HTTP 404")


def test_execute_tool_real_dry_run_writes_tool_audit_without_token() -> None:
    company_id = uuid4()

    class FakeDb:
        def __init__(self):
            self.added = []
            self.commits = 0

        def scalar(self, query):
            return None

        def add(self, item):
            self.added.append(item)

        def commit(self):
            self.commits += 1

    db = FakeDb()

    result = execute_tool(
        "feishu_task_create",
        ToolExecutionRequest(
            question="创建任务 dry-run",
            params={"dry_run": True, "summary": "跟进客户", "confirmation_token": "must-not-leak"},
            actor_open_id="ou_admin",
        ),
        company_id=company_id,
        db=db,
    )

    assert result["status"] == "success"
    assert result["provider"] == "feishu_mcp"
    assert "confirmation_token:" in result["answer"]
    assert result["metadata"]["write_mode"] == "dry_run"
    assert result["metadata"]["dry_run"] is True
    assert result["metadata"]["has_confirmation_token"] is True
    assert result["metadata"]["write_target"]["operation"] == "task.create"
    assert result["metadata"]["write_target"]["title"] == "跟进客户"
    assert result["metadata"]["write_target_summary"] == "task.create / title=跟进客户"
    assert result["metadata"]["preferred_execution_engine"] == "lark_cli"
    assert result["metadata"]["realtime_policy"] == "tool_mcp_cli_only"
    assert result["metadata"]["realtime_bridge"] == "feishu_mcp"
    assert result["metadata"]["mcp_provider"] == "feishu_mcp"
    assert result["metadata"]["execution_chain"] == [
        "agent_runtime",
        "tool_router",
        "tool",
        "mcp",
        "cli",
        "feishu",
    ]
    assert result["metadata"]["agent_runtime_direct_access"] is False
    assert result["metadata"]["sync_engine_direct_api_allowed"] is False
    assert result["metadata"]["sync_engine_mcp_access_allowed"] is False
    assert "confirmation_token" not in result["metadata"]["write_target"]["param_keys"]
    assert len(db.added) == 1
    assert db.commits == 1
    assert db.added[0].action == "tool.task.create"
    assert db.added[0].target_id == "feishu_task_create"
    assert db.added[0].payload["write_target"]["operation"] == "task.create"
    assert db.added[0].payload["write_target"]["title"] == "跟进客户"
    assert db.added[0].payload["mcp_provider"] == "feishu_mcp"
    assert db.added[0].payload["execution_chain"] == [
        "agent_runtime",
        "tool_router",
        "tool",
        "mcp",
        "cli",
        "feishu",
    ]
    assert "confirmation_token" not in db.added[0].payload["write_target"]["param_keys"]


def test_system_logs_overview_groups_audit_payloads() -> None:
    company_id = uuid4()
    items = [
        SimpleNamespace(
            id=uuid4(),
            company_id=company_id,
            action="tool.task_qa.read",
            target_type="tool",
            target_id="task_qa",
            actor="ou_owner",
            payload={"provider": "feishu_mcp", "status": "success"},
            created_at=datetime(2026, 6, 12, 8, 0, tzinfo=UTC),
        ),
        SimpleNamespace(
            id=uuid4(),
            company_id=company_id,
            action="tool.mail_qa.read",
            target_type="tool",
            target_id="mail_qa",
            actor="ou_member",
            payload={"provider": "local", "status": "denied"},
            created_at=datetime(2026, 6, 12, 8, 1, tzinfo=UTC),
        ),
        SimpleNamespace(
            id=uuid4(),
            company_id=company_id,
            action="feishu.messages.sync",
            target_type="feishu_chat",
            target_id="oc_1",
            actor="system",
            payload={"status": "failed", "error_message": "timeout"},
            created_at=datetime(2026, 6, 12, 8, 2, tzinfo=UTC),
        ),
        SimpleNamespace(
            id=uuid4(),
            company_id=company_id,
            action="resource.sync",
            target_type="resource",
            target_id="res_1",
            actor="system",
            payload={"status": "partial"},
            created_at=datetime(2026, 6, 12, 8, 3, tzinfo=UTC),
        ),
        SimpleNamespace(
            id=uuid4(),
            company_id=company_id,
            action="agent.trace.preview",
            target_type="agent_trace",
            target_id="company_qa",
            actor="owner",
            payload={"status": "success", "route_path": "company_qa"},
            created_at=datetime(2026, 6, 12, 8, 4, tzinfo=UTC),
        ),
        SimpleNamespace(
            id=uuid4(),
            company_id=company_id,
            action="gateway.feishu.message",
            target_type="gateway_message",
            target_id="om_1",
            actor="ou_1",
            payload={
                "status": "handled",
                "handled": True,
                "used_agent_runtime": True,
                "final_answer_owner": "agent_runtime",
                "route_path": "agent_runtime",
                "route_label": "Agent Runtime",
                "gateway_chain": ["message_gateway", "agent_runtime"],
            "agent_runtime_trace": {
                "route_path": "company_qa",
                "route_label": "公司级问答",
                "reply_mode": {
                    "mode_id": "thinking",
                    "label": "Thinking（需要分析数据）",
                    "data_requirement": "WorkEvent / Knowledge / Memory / 多 Tool 分析",
                    "enterprise_data_required": True,
                    "pre_reply_required": True,
                    "tool_strategy": "work_event_knowledge_memory_multi_tool",
                },
            },
                "agent_runtime_step_count": 3,
                "agent_runtime_tool_steps": [{"name": "company_qa", "provider": "local", "data_source": "PostgreSQL"}],
                "agent_runtime_workflow_steps": [
                    {
                        "name": "approval_execute_approve",
                        "status": "success",
                        "workflow": "approval_execute",
                        "route_path": "feishu_approval_task_approve",
                        "route_label": "审批通过",
                        "reply_mode_id": "normal",
                        "final_answer_owner": "agent_runtime",
                    }
                ],
            },
            created_at=datetime(2026, 6, 12, 8, 5, tzinfo=UTC),
        ),
    ]

    class FakeScalarResult:
        def all(self):
            return items

    class FakeDb:
        def scalars(self, query):
            return FakeScalarResult()

    result = system_logs_overview(company_id=company_id, db=FakeDb())

    assert result["counts"] == {
        "total": 6,
        "errors": 1,
        "warnings": 2,
        "tools": 2,
        "agent": 1,
        "feishu": 1,
        "sync": 1,
        "approval": 0,
        "gateway": 1,
        "agent_runtime_gateway": 1,
        "report": 0,
        "workspace": 0,
        "administration": 0,
        "system": 0,
    }
    assert result["category_counts"] == {"tool": 2, "feishu": 1, "sync": 1, "agent": 1, "gateway": 1}
    assert result["severity_counts"] == {"info": 3, "warning": 2, "error": 1}
    gateway_item = next(item for item in result["items"] if item["category"] == "gateway")
    assert gateway_item["used_agent_runtime"] is True
    assert gateway_item["final_answer_owner"] == "agent_runtime"
    assert gateway_item["route_path"] == "agent_runtime"
    assert gateway_item["route_label"] == "Agent Runtime"
    assert gateway_item["gateway_chain"] == ["message_gateway", "agent_runtime"]
    assert gateway_item["agent_runtime_trace"] == {
        "route_path": "company_qa",
        "route_label": "公司级问答",
        "reply_mode": {
            "mode_id": "thinking",
            "label": "Thinking（需要分析数据）",
            "data_requirement": "WorkEvent / Knowledge / Memory / 多 Tool 分析",
            "enterprise_data_required": True,
            "pre_reply_required": True,
            "tool_strategy": "work_event_knowledge_memory_multi_tool",
        },
    }
    assert gateway_item["reply_mode_id"] == "thinking"
    assert gateway_item["reply_mode_label"] == "Thinking（需要分析数据）"
    assert gateway_item["reply_mode_data_requirement"] == "WorkEvent / Knowledge / Memory / 多 Tool 分析"
    assert gateway_item["reply_mode_enterprise_data_required"] is True
    assert gateway_item["reply_mode_pre_reply_required"] is True
    assert gateway_item["reply_mode_tool_strategy"] == "work_event_knowledge_memory_multi_tool"
    assert gateway_item["agent_runtime_step_count"] == 3
    assert gateway_item["agent_runtime_tool_steps"] == [{"name": "company_qa", "provider": "local", "data_source": "PostgreSQL"}]
    assert gateway_item["agent_runtime_workflow_steps"] == [
        {
            "name": "approval_execute_approve",
            "status": "success",
            "workflow": "approval_execute",
            "route_path": "feishu_approval_task_approve",
            "route_label": "审批通过",
            "reply_mode_id": "normal",
            "final_answer_owner": "agent_runtime",
        }
    ]
    assert "agent_runtime=true" in gateway_item["summary"]
    assert "agent_steps=3" in gateway_item["summary"]
    assert result["latest_errors"][0]["action"] == "feishu.messages.sync"
    assert result["latest_errors"][0]["error"] == "timeout"


def test_system_log_marks_unhandled_gateway_card_action_as_warning() -> None:
    item = SimpleNamespace(
        id=uuid4(),
        company_id=uuid4(),
        action="gateway.feishu.card_action",
        target_type="gateway_message",
        target_id="om_card",
        actor="ou_1",
        payload={"status": "ignored", "reason": "unhandled_card_action", "handled": False},
        created_at=datetime(2026, 6, 12, 8, 6, tzinfo=UTC),
    )

    payload = system_log_item_payload(item)

    assert payload["category"] == "gateway"
    assert payload["severity"] == "warning"
    assert payload["status"] == "ignored"
    assert payload["reason"] == "unhandled_card_action"


def test_system_log_extracts_employee_agent_identity_from_gateway_trace() -> None:
    company_id = uuid4()
    agent_identity = {
        "agent_type": "employee_personal_agent",
        "agent_id": f"{company_id}:ou_1",
        "agent_owner_open_id": "ou_1",
        "agent_owner_display_name": "王工",
        "shared_business_tool_count": 9,
        "data_permission_model": "identity_scoped_tighten_only",
    }
    item = SimpleNamespace(
        id=uuid4(),
        company_id=company_id,
        action="gateway.feishu.message",
        target_type="gateway_message",
        target_id="om_1",
        actor="ou_1",
        payload={
            "status": "success",
            "agent_runtime_trace": {"agent_identity": agent_identity},
        },
        created_at=datetime(2026, 6, 12, 8, 7, tzinfo=UTC),
    )

    payload = system_log_item_payload(item)

    assert payload["agent_identity"] == agent_identity
    assert payload["agent_id"] == f"{company_id}:ou_1"
    assert payload["agent_type"] == "employee_personal_agent"
    assert payload["agent_owner_open_id"] == "ou_1"
    assert payload["agent_owner_display_name"] == "王工"
    assert payload["shared_business_tool_count"] == 9
    assert payload["data_permission_model"] == "identity_scoped_tighten_only"


def test_list_system_logs_filters_by_employee_agent_identity() -> None:
    company_id = uuid4()
    items = [
        SimpleNamespace(
            id=uuid4(),
            company_id=company_id,
            action="gateway.feishu.message",
            target_type="gateway_message",
            target_id="om_1",
            actor="ou_1",
            payload={
                "status": "success",
                "agent_runtime_trace": {
                    "agent_identity": {
                        "agent_type": "employee_personal_agent",
                        "agent_id": f"{company_id}:ou_1",
                        "agent_owner_open_id": "ou_1",
                        "agent_owner_display_name": "王工",
                        "shared_business_tool_count": 9,
                        "data_permission_model": "identity_scoped_tighten_only",
                    }
                },
            },
            created_at=datetime(2026, 6, 12, 8, 7, tzinfo=UTC),
        ),
        SimpleNamespace(
            id=uuid4(),
            company_id=company_id,
            action="gateway.feishu.message",
            target_type="gateway_message",
            target_id="om_2",
            actor="ou_2",
            payload={
                "status": "success",
                "agent_runtime_trace": {
                    "agent_identity": {
                        "agent_type": "employee_personal_agent",
                        "agent_id": f"{company_id}:ou_2",
                        "agent_owner_open_id": "ou_2",
                    }
                },
            },
            created_at=datetime(2026, 6, 12, 8, 8, tzinfo=UTC),
        ),
    ]

    class FakeScalarResult:
        def all(self):
            return items

    class FakeDb:
        def scalars(self, query):
            return FakeScalarResult()

    result = list_system_logs(company_id=company_id, agent_owner_open_id="ou_1", db=FakeDb())

    assert result["counts"]["total"] == 1
    assert result["items"][0]["agent_id"] == f"{company_id}:ou_1"
    assert result["items"][0]["agent_owner_display_name"] == "王工"
    assert result["items"][0]["shared_business_tool_count"] == 9


def test_list_system_logs_filters_by_category_severity_and_status() -> None:
    company_id = uuid4()
    items = [
        SimpleNamespace(
            id=uuid4(),
            company_id=company_id,
            action="tool.task_qa.read",
            target_type="tool",
            target_id="task_qa",
            actor="ou_owner",
            payload={"status": "success"},
            created_at=datetime(2026, 6, 12, 8, 0, tzinfo=UTC),
        ),
        SimpleNamespace(
            id=uuid4(),
            company_id=company_id,
            action="gateway.feishu.message",
            target_type="gateway_message",
            target_id="om_1",
            actor="ou_member",
            payload={"status": "ignored", "reason": "not_addressed_to_bot"},
            created_at=datetime(2026, 6, 12, 8, 1, tzinfo=UTC),
        ),
        SimpleNamespace(
            id=uuid4(),
            company_id=company_id,
            action="feishu.messages.sync",
            target_type="feishu_chat",
            target_id="oc_1",
            actor="system",
            payload={"status": "failed", "error_message": "timeout"},
            created_at=datetime(2026, 6, 12, 8, 2, tzinfo=UTC),
        ),
    ]

    class FakeScalarResult:
        def all(self):
            return items

    class FakeDb:
        def scalars(self, query):
            return FakeScalarResult()

    result = list_system_logs(company_id=company_id, category="feishu", severity="error", status="failed", db=FakeDb())

    assert result["counts"] == {
        "total": 1,
        "errors": 1,
        "warnings": 0,
        "tools": 0,
        "agent": 0,
        "feishu": 1,
        "sync": 0,
        "approval": 0,
        "gateway": 0,
        "agent_runtime_gateway": 0,
        "report": 0,
        "workspace": 0,
        "administration": 0,
        "system": 0,
    }
    assert result["items"][0]["action"] == "feishu.messages.sync"
    assert result["items"][0]["severity"] == "error"
    assert result["items"][0]["error"] == "timeout"


def test_list_system_logs_filters_by_gateway_reason() -> None:
    company_id = uuid4()
    items = [
        SimpleNamespace(
            id=uuid4(),
            company_id=company_id,
            action="gateway.feishu.card_action",
            target_type="gateway_message",
            target_id="om_card",
            actor="ou_member",
            payload={"status": "ignored", "reason": "unhandled_card_action"},
            created_at=datetime(2026, 6, 12, 8, 1, tzinfo=UTC),
        ),
        SimpleNamespace(
            id=uuid4(),
            company_id=company_id,
            action="gateway.feishu.message",
            target_type="gateway_message",
            target_id="om_1",
            actor="ou_member",
            payload={"status": "ignored", "reason": "not_addressed_to_bot"},
            created_at=datetime(2026, 6, 12, 8, 2, tzinfo=UTC),
        ),
    ]

    class FakeScalarResult:
        def all(self):
            return items

    class FakeDb:
        def scalars(self, query):
            return FakeScalarResult()

    result = list_system_logs(company_id=company_id, category="gateway", reason="unhandled_card_action", db=FakeDb())

    assert result["counts"]["total"] == 1
    assert result["items"][0]["target_id"] == "om_card"
    assert result["items"][0]["reason"] == "unhandled_card_action"


def test_list_system_logs_filters_gateway_agent_runtime_route() -> None:
    company_id = uuid4()
    items = [
        SimpleNamespace(
            id=uuid4(),
            company_id=company_id,
            action="gateway.feishu.message",
            target_type="gateway_message",
            target_id="om_agent",
            actor="ou_owner",
            payload={
                "status": "handled",
                "used_agent_runtime": True,
                "final_answer_owner": "agent_runtime",
                "route_path": "company_qa",
            },
            created_at=datetime(2026, 6, 12, 8, 1, tzinfo=UTC),
        ),
        SimpleNamespace(
            id=uuid4(),
            company_id=company_id,
            action="gateway.feishu.message",
            target_type="gateway_message",
            target_id="om_fixed",
            actor="ou_owner",
            payload={
                "status": "handled",
                "used_agent_runtime": False,
                "final_answer_owner": "legacy_fixed_reply",
                "route_path": "帮助",
            },
            created_at=datetime(2026, 6, 12, 8, 2, tzinfo=UTC),
        ),
    ]

    class FakeScalarResult:
        def all(self):
            return items

    class FakeDb:
        def scalars(self, query):
            return FakeScalarResult()

    result = list_system_logs(
        company_id=company_id,
        category="gateway",
        used_agent_runtime=True,
        final_answer_owner="agent_runtime",
        route_path="company_qa",
        db=FakeDb(),
    )

    assert result["counts"]["total"] == 1
    assert result["items"][0]["target_id"] == "om_agent"
    assert result["items"][0]["used_agent_runtime"] is True
    assert result["items"][0]["final_answer_owner"] == "agent_runtime"
    assert result["items"][0]["route_path"] == "company_qa"


def test_list_system_logs_route_passes_agent_runtime_filters(monkeypatch) -> None:
    captured = {}

    def fake_list_system_log_items(db, **kwargs):
        captured["db"] = db
        captured["kwargs"] = kwargs
        return {"items": [], "counts": {"total": 0}}

    monkeypatch.setattr(
        "app.api.routes.v5_system_log_list_routes.list_system_log_items",
        fake_list_system_log_items,
    )

    db = object()
    result = list_system_logs(
        company_id=None,
        category=" gateway ",
        severity=None,
        status=" handled ",
        reason=None,
        used_agent_runtime=True,
        final_answer_owner=" agent_runtime ",
        route_path=" company_qa ",
        db=db,
    )

    assert result == {"items": [], "counts": {"total": 0}}
    assert captured["db"] is db
    assert captured["kwargs"]["category"] == "gateway"
    assert captured["kwargs"]["status"] == "handled"
    assert captured["kwargs"]["used_agent_runtime"] is True
    assert captured["kwargs"]["final_answer_owner"] == "agent_runtime"
    assert captured["kwargs"]["route_path"] == "company_qa"


def test_list_system_logs_filters_approval_action_category() -> None:
    company_id = uuid4()
    approval_audit = SimpleNamespace(
        id=uuid4(),
        company_id=company_id,
        action="approval.task.approve",
        target_type="approval_task",
        target_id="task_1",
        actor="ou_owner",
        payload={"status": "success", "confirmed": True, "confirmation_token_checked": True},
        created_at=datetime(2026, 6, 12, 8, 0, tzinfo=UTC),
    )

    class FakeScalarResult:
        def all(self):
            return [approval_audit]

    class FakeDb:
        def scalars(self, query):
            return FakeScalarResult()

    result = list_system_logs(company_id=company_id, category="approval", db=FakeDb())

    assert result["counts"]["total"] == 1
    assert result["counts"]["approval"] == 1
    assert result["category_counts"] == {"approval": 1}
    assert result["items"][0]["category"] == "approval"
    assert result["items"][0]["action"] == "approval.task.approve"
    assert result["items"][0]["confirmed"] is True
    assert result["items"][0]["confirmation_token_checked"] is True
    assert "confirmation_token_checked=true" in result["items"][0]["summary"]
    assert "confirmed=true" in result["items"][0]["summary"]


def test_list_system_logs_exposes_approval_confirmation_mismatch_without_token() -> None:
    company_id = uuid4()
    approval_audit = SimpleNamespace(
        id=uuid4(),
        company_id=company_id,
        action="approval.task.reject",
        target_type="approval_task",
        target_id="task_1",
        actor="ou_owner",
        payload={
            "status": "denied",
            "confirmed": False,
            "confirmation_token_checked": False,
            "expected_action": "approve",
            "confirmation_token": "must-not-leak",
            "error": "confirmation_token_mismatch",
        },
        created_at=datetime(2026, 6, 12, 8, 0, tzinfo=UTC),
    )
    success_audit = SimpleNamespace(
        id=uuid4(),
        company_id=company_id,
        action="approval.task.reject",
        target_type="approval_task",
        target_id="task_2",
        actor="ou_owner",
        payload={
            "status": "success",
            "confirmed": True,
            "confirmation_token_checked": True,
        },
        created_at=datetime(2026, 6, 12, 8, 1, tzinfo=UTC),
    )

    class FakeScalarResult:
        def all(self):
            return [approval_audit, success_audit]

    class FakeDb:
        def scalars(self, query):
            return FakeScalarResult()

    result = list_system_logs(
        company_id=company_id,
        category="approval",
        confirmed=False,
        confirmation_token_checked=False,
        db=FakeDb(),
    )

    item = result["items"][0]
    assert result["counts"]["total"] == 1
    assert item["severity"] == "warning"
    assert item["confirmed"] is False
    assert item["confirmation_token_checked"] is False
    assert item["expected_action"] == "approve"
    assert "confirmation_token_mismatch" in item["summary"]
    assert "must-not-leak" not in str(item)


def test_list_system_logs_exposes_write_target_summary_without_token() -> None:
    company_id = uuid4()
    tool_audit = SimpleNamespace(
        id=uuid4(),
        company_id=company_id,
        action="tool.bitable.record.update",
        target_type="tool",
        target_id="feishu_bitable_record_update",
        actor="ou_owner",
        payload={
            "status": "success",
            "provider": "feishu_mcp",
            "preferred_execution_engine": "lark_cli",
            "realtime_policy": "tool_mcp_cli_only",
            "realtime_bridge": "feishu_mcp",
            "api_role": "sync_engine_only",
            "execution_chain": ["agent_runtime", "tool_router", "tool", "mcp", "cli", "feishu"],
            "agent_runtime_direct_access": False,
            "sync_engine_direct_api_allowed": False,
            "sync_engine_mcp_access_allowed": False,
            "confirmed": True,
            "has_confirmation_token": True,
            "confirmation_token": "must-not-leak",
            "write_target": {
                "operation": "bitable_record.update",
                "identifiers": {"app_token": "app_1", "table_id": "tbl_1", "record_id": "rec_1"},
                "field_keys": ["客户名称", "状态"],
            },
        },
        created_at=datetime(2026, 6, 12, 8, 0, tzinfo=UTC),
    )

    class FakeScalarResult:
        def all(self):
            return [tool_audit]

    class FakeDb:
        def scalars(self, query):
            return FakeScalarResult()

    result = list_system_logs(company_id=company_id, category="tool", db=FakeDb())

    item = result["items"][0]
    assert item["write_target_summary"] == (
        "bitable_record.update / app_token=app_1 / record_id=rec_1 / table_id=tbl_1 / fields=客户名称,状态"
    )
    assert item["preferred_execution_engine"] == "lark_cli"
    assert item["realtime_policy"] == "tool_mcp_cli_only"
    assert item["realtime_bridge"] == "feishu_mcp"
    assert item["execution_chain"] == ["agent_runtime", "tool_router", "tool", "mcp", "cli", "feishu"]
    assert item["agent_runtime_direct_access"] is False
    assert item["sync_engine_direct_api_allowed"] is False
    assert item["sync_engine_mcp_access_allowed"] is False
    assert "write_target=bitable_record.update" in item["summary"]
    assert "must-not-leak" not in str(item)


def test_agent_trace_preview_returns_trace_and_writes_audit(monkeypatch) -> None:
    company_id = uuid4()
    captured = {}
    runtime_result = AgentRuntimeResult(
        answer="范围：指定公司｜公司级问答\n公司摘要",
        trace=AgentRuntimeTrace(
            semantic_intent="intent_rules",
            route_path="company_qa",
            route_scope="company",
            route_reason="permission_scope",
            execution_category="query",
            execution_category_source="answer_semantics_hint",
            scope_label="指定公司",
            route_label="公司级问答",
            agent_identity={
                "agent_type": "employee_personal_agent",
                "agent_id": f"{company_id}:ou_owner",
                "agent_owner_open_id": "ou_owner",
                "agent_owner_display_name": "Joon",
                "shared_business_tool_count": 9,
                "data_permission_model": "identity_scoped_tighten_only",
            },
            actor_context={
                "role": "owner",
                "access_scope": "company",
                "data_access_scope": "company",
                "company_data_allowed": True,
                "cross_user_data_allowed": True,
                "final_answer_owner": "agent_runtime",
                },
                reply_mode={"mode_id": "thinking", "label": "Thinking（需要分析数据）"},
                thinking_preview=None,
                steps=(
                AgentExecutionStep(kind="semantic", name="intent_rules", status="success", metadata={}),
                AgentExecutionStep(kind="route", name="company_qa", status="success", metadata={"scope": "company"}),
            ),
        ),
    )

    def fake_answer_with_trace(*args, **kwargs):
        captured["kwargs"] = kwargs
        return runtime_result

    monkeypatch.setattr("app.services.v5_agent_admin.answer_agent_message_with_trace", fake_answer_with_trace)

    class FakeDb:
        def __init__(self):
            self.added = []
            self.committed = False
            self.setting = SimpleNamespace(
                settings={
                    "agent": {
                        "planner_enabled": True,
                        "max_planner_steps": 2,
                        "allow_write_tools": False,
                        "require_write_confirmation": True,
                    }
                },
                updated_at=None,
            )

        def scalar(self, query):
            return self.setting

        def add(self, item):
            self.added.append(item)

        def commit(self):
            self.committed = True

    db = FakeDb()

    result = agent_trace_preview(
        AgentTracePreviewRequest(
            company_id=company_id,
            question="固势今天有什么风险",
            actor_open_id="ou_owner",
            chat_id="oc_owner",
        ),
        db=db,
    )

    assert captured["kwargs"]["company_id"] == company_id
    assert captured["kwargs"]["normalized_command"] == "固势今天有什么风险"
    assert captured["kwargs"]["actor"].role == "owner"
    assert captured["kwargs"]["actor"].open_id == "ou_owner"
    assert captured["kwargs"]["planner_enabled"] is True
    assert captured["kwargs"]["max_planner_steps"] == 2
    assert captured["kwargs"]["allow_write_tools"] is False
    assert captured["kwargs"]["require_write_confirmation"] is True
    assert result["company_id"] == str(company_id)
    assert result["answer"].startswith("范围：指定公司")
    assert result["trace"]["route_path"] == "company_qa"
    assert result["trace"]["reply_mode"]["mode_id"] == "thinking"
    assert result["trace"]["steps"][0]["kind"] == "semantic"
    assert db.committed is True
    assert len(db.added) == 1
    assert db.added[0].action == "agent.trace.preview"
    assert db.added[0].target_type == "agent_trace"
    assert db.added[0].target_id == "company_qa"
    assert db.added[0].payload["route_path"] == "company_qa"
    assert db.added[0].payload["agent_identity"]["agent_type"] == "employee_personal_agent"
    assert db.added[0].payload["agent_id"].endswith(":ou_owner")
    assert db.added[0].payload["agent_owner_open_id"] == "ou_owner"
    assert db.added[0].payload["shared_business_tool_count"] == 9
    assert db.added[0].payload["actor_context"]["data_access_scope"] == "company"
    assert db.added[0].payload["data_access_scope"] == "company"
    assert db.added[0].payload["company_data_allowed"] is True
    assert db.added[0].payload["cross_user_data_allowed"] is True
    assert db.added[0].payload["reply_mode"]["mode_id"] == "thinking"
    assert db.added[0].payload["planner_enabled"] is True
    assert db.added[0].payload["max_planner_steps"] == 2
    assert db.added[0].payload["allow_write_tools"] is False
    assert db.added[0].payload["require_write_confirmation"] is True
    assert db.added[0].payload["supports_write"] is False
    assert db.added[0].payload["requires_dry_run"] is False
    assert db.added[0].payload["confirmed_execution_requires"] == []


def test_list_agent_traces_returns_audit_summaries() -> None:
    company_id = uuid4()
    audit = SimpleNamespace(
        id=uuid4(),
        company_id=company_id,
        actor="ou_owner",
        target_id="company_qa",
        payload={
            "status": "success",
            "semantic_intent": "intent_rules",
            "route_path": "company_qa",
            "route_scope": "company",
            "route_reason": "permission_scope",
            "agent_identity": {
                "agent_type": "employee_personal_agent",
                "agent_id": f"{company_id}:ou_owner",
                "agent_owner_open_id": "ou_owner",
                "agent_owner_display_name": "Joon",
                "shared_business_tool_count": 9,
                "data_permission_model": "identity_scoped_tighten_only",
            },
            "agent_id": f"{company_id}:ou_owner",
            "agent_type": "employee_personal_agent",
            "agent_owner_open_id": "ou_owner",
            "agent_owner_display_name": "Joon",
            "shared_business_tool_count": 9,
            "actor_context": {
                "role": "owner",
                "access_scope": "company",
                "data_access_scope": "company",
                "company_data_allowed": True,
                "cross_user_data_allowed": True,
                "final_answer_owner": "agent_runtime",
            },
            "data_access_scope": "company",
            "personal_owner_open_id": None,
            "company_data_allowed": True,
            "cross_user_data_allowed": True,
            "reply_mode": {
                "mode_id": "thinking",
                "label": "Thinking（需要分析数据）",
                "data_requirement": "WorkEvent / Knowledge / Memory / 多 Tool 分析",
                "enterprise_data_required": True,
                "pre_reply_required": True,
                "tool_strategy": "work_event_knowledge_memory_multi_tool",
            },
            "step_count": 3,
            "supports_write": True,
            "requires_dry_run": True,
            "write_policy": "confirmation_required",
            "confirmed_execution_requires": ["dry_run=true", "confirmed=true", "confirmation_token"],
            "chat_id": "oc_owner",
        },
        created_at=datetime(2026, 6, 12, 9, 0, tzinfo=UTC),
    )

    class FakeScalarResult:
        def all(self):
            return [audit]

    class FakeDb:
        def scalars(self, query):
            return FakeScalarResult()

    result = list_agent_traces(company_id=company_id, db=FakeDb())

    assert result["counts"] == {"company_qa": 1}
    assert result["items"] == [
        {
            "id": str(audit.id),
            "company_id": str(company_id),
            "actor": "ou_owner",
            "route_path": "company_qa",
            "route_scope": "company",
            "route_reason": "permission_scope",
            "agent_identity": {
                "agent_type": "employee_personal_agent",
                "agent_id": f"{company_id}:ou_owner",
                "agent_owner_open_id": "ou_owner",
                "agent_owner_display_name": "Joon",
                "shared_business_tool_count": 9,
                "data_permission_model": "identity_scoped_tighten_only",
            },
            "agent_id": f"{company_id}:ou_owner",
            "agent_type": "employee_personal_agent",
            "agent_owner_open_id": "ou_owner",
            "agent_owner_display_name": "Joon",
            "shared_business_tool_count": 9,
            "actor_context": {
                "role": "owner",
                "access_scope": "company",
                "data_access_scope": "company",
                "company_data_allowed": True,
                "cross_user_data_allowed": True,
                "final_answer_owner": "agent_runtime",
            },
            "data_access_scope": "company",
            "personal_owner_open_id": None,
            "company_data_allowed": True,
            "cross_user_data_allowed": True,
            "semantic_intent": "intent_rules",
            "reply_mode": {
                "mode_id": "thinking",
                "label": "Thinking（需要分析数据）",
                "data_requirement": "WorkEvent / Knowledge / Memory / 多 Tool 分析",
                "enterprise_data_required": True,
                "pre_reply_required": True,
                "tool_strategy": "work_event_knowledge_memory_multi_tool",
            },
            "reply_mode_id": "thinking",
            "reply_mode_label": "Thinking（需要分析数据）",
            "reply_mode_data_requirement": "WorkEvent / Knowledge / Memory / 多 Tool 分析",
            "reply_mode_enterprise_data_required": True,
            "reply_mode_pre_reply_required": True,
            "reply_mode_tool_strategy": "work_event_knowledge_memory_multi_tool",
            "step_count": 3,
            "supports_write": True,
            "requires_dry_run": True,
            "write_policy": "confirmation_required",
            "confirmed_execution_requires": ["dry_run=true", "confirmed=true", "confirmation_token"],
            "chat_id": "oc_owner",
            "status": "success",
            "created_at": "2026-06-12T09:00:00+00:00",
        }
    ]


def test_list_agent_traces_filters_by_employee_agent_identity() -> None:
    company_id = uuid4()
    audits = [
        SimpleNamespace(
            id=uuid4(),
            company_id=company_id,
            actor="ou_1",
            target_id="personal_tasks",
            payload={
                "status": "success",
                "route_path": "personal_tasks",
                "agent_identity": {
                    "agent_type": "employee_personal_agent",
                    "agent_id": f"{company_id}:ou_1",
                    "agent_owner_open_id": "ou_1",
                    "agent_owner_display_name": "王工",
                    "shared_business_tool_count": 9,
                },
                "agent_id": f"{company_id}:ou_1",
                "agent_type": "employee_personal_agent",
                "agent_owner_open_id": "ou_1",
                "agent_owner_display_name": "王工",
                "shared_business_tool_count": 9,
            },
            created_at=datetime(2026, 6, 12, 9, 0, tzinfo=UTC),
        ),
        SimpleNamespace(
            id=uuid4(),
            company_id=company_id,
            actor="ou_2",
            target_id="company_qa",
            payload={
                "status": "success",
                "route_path": "company_qa",
                "agent_identity": {
                    "agent_type": "employee_personal_agent",
                    "agent_id": f"{company_id}:ou_2",
                    "agent_owner_open_id": "ou_2",
                },
                "agent_id": f"{company_id}:ou_2",
                "agent_type": "employee_personal_agent",
                "agent_owner_open_id": "ou_2",
            },
            created_at=datetime(2026, 6, 12, 9, 1, tzinfo=UTC),
        ),
    ]

    class FakeScalarResult:
        def all(self):
            return audits

    class FakeDb:
        def scalars(self, query):
            return FakeScalarResult()

    result = list_agent_traces(company_id=company_id, agent_owner_open_id="ou_1", db=FakeDb())

    assert result["counts"] == {"personal_tasks": 1}
    assert len(result["items"]) == 1
    assert result["items"][0]["agent_id"] == f"{company_id}:ou_1"
    assert result["items"][0]["agent_owner_display_name"] == "王工"
    assert result["items"][0]["shared_business_tool_count"] == 9


def test_agent_settings_returns_defaults_from_company_setting() -> None:
    company_id = uuid4()
    setting = SimpleNamespace(settings={"agent": {"default_model": "deepseek-reasoner", "max_tool_calls": 99}}, updated_at=None)

    class FakeDb:
        def scalar(self, query):
            return setting

    result = agent_settings(company_id=company_id, db=FakeDb())

    assert result["company_id"] == str(company_id)
    assert result["settings"]["default_model"] == "deepseek-reasoner"
    assert result["settings"]["max_tool_calls"] == 20
    assert result["settings"]["require_write_confirmation"] is True


def test_save_agent_settings_updates_json_and_writes_audit() -> None:
    company_id = uuid4()
    setting = SimpleNamespace(settings={"source": "test", "agent": {"default_model": "qwen-local"}}, updated_at=None)

    class FakeDb:
        def __init__(self):
            self.added = []
            self.committed = False

        def scalar(self, query):
            return setting

        def add(self, item):
            self.added.append(item)

        def commit(self):
            self.committed = True

    db = FakeDb()
    result = save_agent_settings(
        AgentSettingsUpdate(
            default_model="deepseek-reasoner",
            planner_enabled=True,
            max_tool_calls=8,
            allow_write_tools=False,
            require_write_confirmation=False,
        ),
        company_id=company_id,
        db=db,
    )

    assert db.committed is True
    assert setting.settings["agent"]["default_model"] == "deepseek-reasoner"
    assert setting.settings["agent"]["planner_enabled"] is True
    assert setting.settings["agent"]["max_tool_calls"] == 8
    assert setting.settings["agent"]["allow_write_tools"] is False
    assert setting.settings["agent"]["require_write_confirmation"] is True
    assert result["settings"] == setting.settings["agent"]
    assert len(db.added) == 1
    assert db.added[0].action == "agent.settings.update"
    assert db.added[0].target_type == "agent_settings"
    assert db.added[0].payload["updated_fields"] == [
        "allow_write_tools",
        "default_model",
        "max_tool_calls",
        "planner_enabled",
        "require_write_confirmation",
    ]


def test_update_tool_config_rejects_incompatible_provider_with_bad_request() -> None:
    class FakeDb:
        def scalar(self, query):
            return None

    try:
        update_tool_config(
            "general_chat",
            ToolConfigUpdate(provider=ToolProvider.FEISHU_MCP),
            company_id=uuid4(),
            db=FakeDb(),
        )
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400
        assert "explicit tool binding" in str(getattr(exc, "detail", ""))
    else:
        raise AssertionError("expected incompatible provider to raise HTTP 400")


def test_update_tool_config_returns_not_found_for_unknown_tool() -> None:
    class FakeDb:
        def scalar(self, query):
            return None

    try:
        update_tool_config(
            "missing_tool",
            ToolConfigUpdate(provider=ToolProvider.LOCAL),
            company_id=uuid4(),
            db=FakeDb(),
        )
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404
        assert "Unknown tool" in str(getattr(exc, "detail", ""))
    else:
        raise AssertionError("expected unknown tool to raise HTTP 404")


def test_update_tool_config_returns_complete_tool_payload() -> None:
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

    db = FakeDb()

    result = update_tool_config(
        "task_qa",
        ToolConfigUpdate(provider=ToolProvider.FEISHU_MCP, config_json={"mode": "lark_cli_first"}),
        company_id=uuid4(),
        db=db,
    )

    assert db.committed is True
    assert result["tool_name"] == "task_qa"
    assert result["provider"] == "feishu_mcp"
    assert result["compatible_providers"] == ["local", "feishu_mcp"]
    assert result["config_json"] == {"mode": "lark_cli_first"}


def test_batch_update_tool_configs_previews_write_tool_policy_without_mutation() -> None:
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

    db = FakeDb()

    result = batch_update_tool_configs(
        ToolBatchUpdateRequest(all_tools=True, supports_write=True, enabled=False, dry_run=True),
        company_id=uuid4(),
        db=db,
    )

    assert result["dry_run"] is True
    assert result["matched_count"] > 10
    assert result["updated_count"] == 0
    assert result["failed_count"] == 0
    assert {item["status"] for item in result["items"]} == {"dry_run"}
    assert all(item["enabled"] is False for item in result["items"])
    assert db.added == []
    assert db.committed is False


def test_batch_update_tool_configs_applies_compatible_tools_and_reports_failures() -> None:
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

    db = FakeDb()

    result = batch_update_tool_configs(
        ToolBatchUpdateRequest(
            tool_names=["task_qa", "general_chat", "missing_tool"],
            provider=ToolProvider.FEISHU_MCP,
            config_json={"mode": "lark_cli_first"},
            dry_run=False,
        ),
        company_id=uuid4(),
        db=db,
    )

    by_name = {item["tool_name"]: item for item in result["items"]}
    assert result["dry_run"] is False
    assert result["matched_count"] == 3
    assert result["updated_count"] == 1
    assert result["failed_count"] == 2
    assert by_name["task_qa"]["status"] == "updated"
    assert by_name["task_qa"]["provider"] == "feishu_mcp"
    assert by_name["general_chat"]["status"] == "failed"
    assert "requires explicit tool binding" in by_name["general_chat"]["error"]
    assert by_name["missing_tool"]["status"] == "failed"
    assert db.committed is True
    assert any(getattr(item, "tool_name", None) == "task_qa" for item in db.added)
    assert any(getattr(item, "action", None) == "tool.config.batch_update" for item in db.added)


def test_mail_account_registers_v5_resource_source() -> None:
    class FakeDb:
        def __init__(self):
            self.added = []

        def scalar(self, query):
            return None

        def add(self, item):
            self.added.append(item)

    account = SimpleNamespace(
        id=uuid4(),
        company_id=uuid4(),
        provider="imap",
        account_type="external_mail",
        display_name="老板邮箱",
        external_account_id=None,
        email_address="boss@example.com",
        settings={},
        is_active=True,
    )

    resource = upsert_mail_account_resource(FakeDb(), account, folder_id="INBOX")

    assert resource.platform == "mail"
    assert resource.resource_type == "mail_folder"
    assert resource.resource_id == "boss@example.com"
    assert resource.resource_sub_id == "INBOX"
    assert resource.permission_level == "owner"
    assert resource.data_classification == "personal"
    assert resource.business_domain == "communications"
    assert resource.sources[0].source_type == "external_mail_account"
    assert resource.sources[0].source_account_id == str(account.id)
    assert resource.config_json["account_type"] == "external_mail"
    assert resource.sources[0].settings["account_type"] == "external_mail"


def test_mail_ingest_writes_source_and_visibility_metadata(monkeypatch) -> None:
    class FakeDb:
        def scalar(self, query):
            return None

        def add(self, item):
            pass

        def flush(self):
            pass

    captured = {}
    event_id = uuid4()

    def fake_upsert_work_event(db, data):
        captured["data"] = data
        return SimpleNamespace(id=event_id)

    monkeypatch.setattr("app.services.integrations.mail.upsert_work_event", fake_upsert_work_event)
    account = SimpleNamespace(
        id=uuid4(),
        company_id=uuid4(),
        provider="imap",
        display_name="老板邮箱",
        external_account_id=None,
        email_address="boss@example.com",
        settings={},
        is_active=True,
    )
    raw_message = (
        b"From: a@example.com\r\n"
        b"To: boss@example.com\r\n"
        b"Subject: Weekly update\r\n"
        b"Message-ID: <mail-1@example.com>\r\n"
        b"Date: Fri, 12 Jun 2026 08:00:00 +0800\r\n"
        b"\r\n"
        b"hello"
    )

    event = ingest_email_message(FakeDb(), account, raw_message, labels=["INBOX"])

    assert event.id == event_id
    assert captured["data"].source_type == "external_mail_account"
    assert captured["data"].source_account_id == str(account.id)
    assert captured["data"].visibility_scope == "owner"
    assert captured["data"].data_classification == "personal"
    assert captured["data"].business_domain == "general"
    assert captured["data"].resource_id is not None


def test_mail_ingest_marks_strong_personal_mail_as_personal(monkeypatch) -> None:
    class FakeDb:
        def scalar(self, query):
            return None

        def add(self, item):
            pass

        def flush(self):
            pass

    captured = {}

    def fake_upsert_work_event(db, data):
        captured["data"] = data
        return SimpleNamespace(id=uuid4())

    monkeypatch.setattr("app.services.integrations.mail.upsert_work_event", fake_upsert_work_event)
    account = SimpleNamespace(
        id=uuid4(),
        company_id=uuid4(),
        provider="imap",
        display_name="老板邮箱",
        external_account_id=None,
        email_address="boss@example.com",
        settings={},
        is_active=True,
    )
    raw_message = (
        b"From: private@example.com\r\n"
        b"To: boss@example.com\r\n"
        b"Subject: private family plan\r\n"
        b"Message-ID: <mail-2@example.com>\r\n"
        b"Date: Fri, 12 Jun 2026 08:00:00 +0800\r\n"
        b"\r\n"
        b"family personal note"
    )

    ingest_email_message(FakeDb(), account, raw_message, labels=["INBOX"])

    assert captured["data"].data_classification == "personal"
    assert captured["data"].business_domain == "personal"
    assert captured["data"].visibility_scope == "owner"


def test_access_control_resource_policies_cover_chat_mail_bitable_and_doc() -> None:
    company_id = uuid4()
    owner = AccessPrincipal(company_id=company_id, role="owner", domains=("all",))
    employee = AccessPrincipal(company_id=company_id, role="employee", current_chat_id="oc_current", domains=("chat",))
    sales = AccessPrincipal(company_id=company_id, role="sales_manager", domains=("sales", "bitable"))
    calendar_actor = AccessPrincipal(company_id=company_id, role="employee", domains=("calendar",))

    chat = SimpleNamespace(
        id=uuid4(),
        company_id=company_id,
        resource_type="chat",
        resource_id="oc_current",
        data_classification="company",
        business_domain="communications",
        permission_level="team",
    )
    mail = SimpleNamespace(
        id=uuid4(),
        company_id=company_id,
        resource_type="mail_folder",
        resource_id="boss@example.com",
        data_classification="personal",
        business_domain="communications",
        permission_level="owner",
        config_json={},
    )
    bitable = SimpleNamespace(
        id=uuid4(),
        company_id=company_id,
        resource_type="bitable_table",
        resource_id="bascn_x",
        data_classification="company",
        business_domain="bitable",
        permission_level="department",
    )
    doc = SimpleNamespace(
        id=uuid4(),
        company_id=company_id,
        resource_type="drive_file",
        resource_id="doccn_x",
        data_classification="company",
        business_domain="knowledge",
        permission_level="department",
    )
    calendar = SimpleNamespace(
        id=uuid4(),
        company_id=company_id,
        resource_type="capability",
        resource_id="feishu:calendar",
        data_classification="company",
        business_domain=None,
        permission_level="department",
    )
    meeting = SimpleNamespace(
        id=uuid4(),
        company_id=company_id,
        resource_type="capability",
        resource_id="feishu:meetings",
        data_classification="company",
        business_domain=None,
        permission_level="department",
    )

    assert can_access_resource(chat, employee) is True
    assert can_access_resource(mail, employee) is False
    assert can_access_resource(mail, owner) is True
    assert can_access_resource(bitable, sales) is True
    assert resource_access_policy(bitable)["field_policy"]["mode"] == "mask_sensitive_fields_before_llm"
    assert resource_access_policy(doc)["record_policy"]["mode"] == "document_acl"
    assert resource_access_policy(calendar)["business_domain"] == "calendar"
    assert resource_access_policy(meeting)["business_domain"] == "meeting"
    assert can_access_resource(calendar, calendar_actor) is True
    assert can_access_resource(meeting, calendar_actor) is False


def test_v5_administration_defaults_cover_core_scope_and_modules() -> None:
    roles = {item["name"]: item for item in DEFAULT_V5_ROLES}
    permission_codes = {item["code"] for item in DEFAULT_V5_PERMISSIONS}

    assert roles["owner"]["scope_type"] == "all_companies"
    assert roles["owner"]["permissions"]["can_manage"] is True
    assert {"finance_manager", "sales_manager", "rd_manager", "project_manager"} <= set(roles)
    assert {
        "workspace.read",
        "resources.manage",
        "knowledge.read",
        "administration.manage",
        "reports.read",
        "feishu_resources.manage",
    } <= permission_codes

def test_v5_sync_strategy_separates_realtime_index_knowledge_memory_and_vectors() -> None:
    strategy = sync_strategy_overview()
    layers = {item["key"]: item for item in strategy["layers"]}
    authorization = {item["resource_type"]: item for item in strategy["authorization_model"]}
    query_policies = {item["key"]: item for item in strategy["query_policies"]}

    assert strategy["data_source"]["platform"] == "feishu"
    assert "外部邮箱" in strategy["data_source"]["principle"]
    assert any("外部邮箱" in rule for rule in strategy["rules"])
    assert {item["key"] for item in strategy["data_sources"]} >= {
        "feishu_app_identity",
        "feishu_user_identity",
        "external_mail_account",
        "personal_dingtalk_account",
    }
    assert "external_web" not in {item["key"] for item in strategy["data_sources"]}
    assert {item["key"] for item in strategy["data_types"]} >= {
        "operational_data",
        "memory_data",
        "knowledge_data",
    }
    assert [item["key"] for item in strategy["data_types"]] == [
        "operational_data",
        "knowledge_data",
        "memory_data",
    ]
    knowledge_type = next(item for item in strategy["data_types"] if item["key"] == "knowledge_data")
    assert knowledge_type["storage"] == "document_store_vector_db"
    assert knowledge_type["external_storage"] == "web"
    assert knowledge_type["storage_layers"] == {"internal": "document_store_vector_db", "external": "web"}
    assert knowledge_type["levels"] == {
        "l1_hot": "sync_to_local_rag",
        "l2_cold": "register_only_lark_cli_realtime",
        "l3_external": "external_search_realtime_no_local_store",
    }
    operational_type = next(item for item in strategy["data_types"] if item["key"] == "operational_data")
    assert operational_type["realtime_query"] == "lark_cli_first"
    assert operational_type["persistence"] == "important_business_data_to_work_events"
    assert query_policies["operational_realtime_cli_first"]["primary_query_path"] == "lark_cli"
    assert query_policies["operational_realtime_cli_first"]["local_fallback"] == "work_events"
    assert query_policies["knowledge_l2_cold"]["primary_query_path"] == "lark_cli"
    assert query_policies["knowledge_l2_cold"]["persistence_rule"] == "register_only_no_full_sync"
    assert query_policies["knowledge_l3_external"]["primary_query_path"] == "external_search"
    assert query_policies["knowledge_l3_external"]["persistence_rule"] == "no_local_store"
    assert {
        "realtime_work_events",
        "master_data_index",
        "knowledge_hot",
        "knowledge_cold",
        "knowledge_external_web",
        "long_term_memory",
        "qdrant_vectors",
    } <= set(layers)
    assert layers["knowledge_external_web"]["sync_action"] == "external_realtime_reference"
    assert data_layer_for_resource("approval_code") == "realtime_work_events"
    assert data_layer_for_resource("bitable_table") == "master_data_index"
    assert data_layer_for_resource("drive_file") == "knowledge_cold"
    assert data_layer_for_resource("web_page") == "knowledge_external_web"
    assert data_layer_for_resource("memory_fact") == "long_term_memory"
    assert sync_action_for_resource("approval_code")["action"] == "realtime_event"
    assert sync_action_for_resource("approval_code")["query_path"] == "lark_cli_first_then_work_event_cache"
    assert sync_action_for_resource("drive_file")["action"] == "document_index_only"
    assert sync_action_for_resource("drive_file")["query_path"] == "lark_cli_realtime"
    assert sync_action_for_resource("web_page")["action"] == "external_realtime_reference"
    assert sync_action_for_resource("web_page")["vectorize"] == "none_or_summary_after_capture"
    assert sync_action_for_resource("web_page")["query_path"] == "external_search_realtime"
    assert sync_action_for_resource("bitable_table")["action"] == "master_data_index"
    assert sync_action_for_resource("bitable_table")["query_path"] == "lark_cli_first_then_index"
    assert sync_action_for_resource("memory_fact")["action"] == "memory_extract_later"
    assert sync_action_for_resource("memory_fact")["query_path"] == "memory_facts"
    assert sync_action_for_resource("unknown")["action"] == "manual_review_required"
    assert authorization["chat"]["access_mode"] == "app_identity_with_auto_discovered_chat"
    assert authorization["calendar"]["access_mode"] == "app_identity_or_user_authorization_with_auto_discovery"
    assert authorization["bitable"]["required_identifiers"] == []
    assert "mailbox" not in DEFAULT_REALTIME_RESOURCE_TYPES
    assert data_layer_for_resource("mail_folder") == "realtime_work_events"


def test_v5_sync_strategy_exposes_feishu_authorization_requirements() -> None:
    calendar = capability_requirement_for_resource("capability", "feishu:calendar")
    bitable = sync_action_for_resource("bitable_table")
    doc = sync_action_for_resource("drive_file")
    unknown = capability_requirement_for_resource("legacy_custom_type")

    assert calendar["resource_type"] == "calendar"
    assert calendar["access_mode"] == "app_identity_or_user_authorization_with_auto_discovery"
    assert calendar["required_identifiers"] == []
    assert bitable["authorization"]["required_identifiers"] == []
    assert doc["authorization"]["access_mode"] == "app_identity_with_auto_discovered_document"
    assert unknown["access_mode"] == "manual_review_required"


def test_v5_resource_sync_status_prioritizes_actionable_items() -> None:
    assert _status_order("failed") < _status_order("partial")
    assert _status_order("partial") < _status_order("running")
    assert _status_order("running") < _status_order("never_synced")
    assert _status_order("never_synced") < _status_order("stale")
    assert _status_order("stale") < _status_order("healthy")
    assert _status_order("unsupported") < _status_order("healthy")


def test_v5_resource_sync_status_uses_latest_resource_sync_run() -> None:
    class ResourceStub:
        def __init__(self):
            self.id = uuid4()
            self.company_id = uuid4()
            self.platform = "feishu"
            self.resource_type = "approval_code"
            self.resource_id = "approval_x"
            self.resource_sub_id = None
            self.resource_name = "付款审批"
            self.sync_mode = "auto"
            self.permission_level = "company"
            self.enabled = True
            self.last_sync_at = None
            self.app_config_id = None
            self.config_json = {}

    resource = ResourceStub()
    run = ResourceSyncRun(
        company_id=resource.company_id,
        resource_id=resource.id,
        sync_action="realtime_event",
        status="failed",
        started_at=datetime.now(UTC),
        error_message="Feishu API error",
    )

    payload = _resource_sync_payload(
        resource,
        now=run.started_at,
        event_counts={},
        policy={},
        latest_run=run,
    )

    assert payload["status"] == "failed"
    assert payload["query_path"] == "lark_cli_first_then_work_event_cache"
    assert payload["latest_run"]["status"] == "failed"
    assert payload["latest_run"]["error_message"] == "Feishu API error"
    assert payload["next_action"] == "最近一次同步失败，请查看错误信息并重新同步。"


def test_v5_resource_sync_status_exposes_latest_run_rag_indexing() -> None:
    class ResourceStub:
        def __init__(self):
            self.id = uuid4()
            self.company_id = uuid4()
            self.platform = "feishu"
            self.resource_type = "drive_file"
            self.resource_id = "doccn_hot"
            self.resource_sub_id = None
            self.resource_name = "高频制度文档"
            self.sync_mode = "manual"
            self.permission_level = "company"
            self.enabled = True
            self.last_sync_at = datetime.now(UTC)
            self.app_config_id = uuid4()
            self.config_json = {"document_type": "docx"}

    resource = ResourceStub()
    run = ResourceSyncRun(
        company_id=resource.company_id,
        resource_id=resource.id,
        sync_action="knowledge_vectorize",
        status="success",
        started_at=datetime.now(UTC),
        summary={
            "document_store": {
                "store": "work_events",
                "work_event_ids": ["event_1"],
                "chunk_count": 3,
                "vector_db": "qdrant_vectors",
                "rag_index": "pending",
                "vector_status": "pending",
            },
            "rag_indexing": {
                "document_store": "work_events",
                "chunking": "pending",
                "vector_db": "qdrant_vectors",
                "rag_index": "pending",
            }
        },
    )

    payload = _resource_sync_payload(
        resource,
        now=run.started_at,
        event_counts={},
        policy={"large_document_mode": "knowledge_vectorize"},
        latest_run=run,
    )

    assert payload["latest_run"]["rag_indexing"]["document_store"] == "work_events"
    assert payload["latest_run"]["rag_indexing_summary"] == (
        "store=work_events / chunk=pending / vector=qdrant_vectors / rag=pending"
    )
    assert payload["latest_run"]["document_store"]["store"] == "work_events"
    assert payload["latest_run"]["document_store_summary"] == "store=work_events / chunks=3 / events=1"


def test_v5_resource_sync_run_payload_exposes_rag_indexing_summary() -> None:
    run = ResourceSyncRun(
        company_id=uuid4(),
        resource_id=uuid4(),
        sync_action="knowledge_vectorize",
        status="success",
        started_at=datetime.now(UTC),
        summary={
            "document_store": {
                "store": "work_events",
                "work_event_ids": ["event_1", "event_2"],
                "chunk_count": 4,
                "vector_db": "qdrant_vectors",
                "rag_index": "pending",
                "vector_status": "pending",
            },
            "rag_indexing": {
                "document_store": "work_events",
                "chunking": "pending",
                "vector_db": "qdrant_vectors",
                "rag_index": "pending",
            }
        },
    )

    payload = resource_sync_run_payload(run)

    assert payload["rag_indexing"]["document_store"] == "work_events"
    assert payload["rag_indexing"]["vector_db"] == "qdrant_vectors"
    assert payload["rag_indexing_summary"] == "store=work_events / chunk=pending / vector=qdrant_vectors / rag=pending"
    assert payload["document_store"]["store"] == "work_events"
    assert payload["document_store_summary"] == "store=work_events / chunks=4 / events=2"


def test_workspace_event_payload_exposes_data_layer_and_sync_action() -> None:
    event = SimpleNamespace(
        id=uuid4(),
        company_id=uuid4(),
        resource_id=uuid4(),
        source="feishu",
        event_type="feishu.bitable.master_data_index",
        external_id="bitable:app:table:record",
        thread_id=None,
        title="多维表格主数据索引",
        business_domain="operations",
        occurred_at=datetime.now(UTC),
        importance_score=0.0,
        sensitivity="normal",
        vector_status="pending",
        payload={
            "data_layer": "master_data_index",
            "sync_action": "master_data_index",
            "query_path": "bitable_api",
        },
    )

    payload = workspace_event_payload(event)

    assert payload["business_domain"] == "operations"
    assert payload["data_layer"] == "master_data_index"
    assert payload["sync_action"] == "master_data_index"
    assert payload["query_path"] == "bitable_api"


def test_risk_noise_closes_normal_salary_document_risks() -> None:
    class ScalarResult:
        def __init__(self, items):
            self.items = items

        def all(self):
            return self.items

    class FakeDb:
        def __init__(self, items):
            self.items = items

        def scalars(self, query):
            return ScalarResult(self.items)

    noise = ExtractedItem(
        company_id=uuid4(),
        item_type="risk",
        title="2026年3月固势工资明细汇总表",
        description="附件为工资总表，请核对。",
        status="open",
        payload={},
    )
    real_risk = ExtractedItem(
        company_id=uuid4(),
        item_type="risk",
        title="工资明细存在差异和问题",
        description="需要确认风险。",
        status="open",
        payload={},
    )

    result = close_finance_document_noise_risks(FakeDb([noise, real_risk]))

    assert result == {"checked": 2, "closed": 1}
    assert noise.status == "closed"
    assert noise.payload["noise_filter"] == "finance_document_notice"
    assert real_risk.status == "open"


def test_low_signal_noise_closes_admin_notice_risks_and_offer_decisions() -> None:
    class ScalarResult:
        def __init__(self, items):
            self.items = items

        def all(self):
            return self.items

    class FakeDb:
        def __init__(self, items):
            self.items = items

        def scalars(self, query):
            return ScalarResult(self.items)

    admin_notice = ExtractedItem(
        company_id=uuid4(),
        item_type="risk",
        title="关于邮件系统迁移后统一邮件签名规范的提醒",
        description="如有问题请联系市场部。如果错误地收到了此邮件，请自行删除。",
        status="open",
        payload={},
    )
    offer_notice = ExtractedItem(
        company_id=uuid4(),
        item_type="decision",
        title="录用通知书-固势科技-王悦",
        description="请确认邮件成功传达。",
        status="open",
        payload={},
    )
    welcome_task = ExtractedItem(
        company_id=uuid4(),
        item_type="task",
        title="欢迎使用飞书邮箱！",
        description="请查看使用指南并稍后处理。",
        status="open",
        payload={},
    )
    real_risk = ExtractedItem(
        company_id=uuid4(),
        item_type="risk",
        title="客户项目延期风险",
        description="需要确认影响。",
        status="open",
        payload={},
    )
    heading_noise = ExtractedItem(
        company_id=uuid4(),
        item_type="risk",
        title="### 风险与阻塞",
        description="### 风险与阻塞",
        status="open",
        payload={},
    )

    result = close_low_signal_extraction_noise(FakeDb([admin_notice, offer_notice, welcome_task, real_risk, heading_noise]))

    assert result == {"checked": 5, "closed": 4}
    assert admin_notice.status == "closed"
    assert offer_notice.status == "closed"
    assert welcome_task.status == "closed"
    assert heading_noise.status == "closed"
    assert real_risk.status == "open"


def test_v5_owner_actions_group_business_next_steps() -> None:
    actions = owner_actions_from_resource_items(
        [
            {
                "id": "resource-chat",
                "resource_name": "销售群",
                "resource_type": "chat",
                "status": "access_blocked",
                "config_json": {"governance": {"status": "bot_not_in_chat"}},
                "next_action": "请先在飞书中把机器人加入对应群，或停用该资源。",
            },
            {
                "id": "resource-calendar",
                "resource_name": "日历日程",
                "resource_type": "calendar",
                "status": "access_blocked",
                "config_json": {},
                "identifier_issue": {"code": "requires_user_authorization"},
                "next_action": "请先完成飞书用户授权；系统会继续自动发现可同步资源。",
            },
        ]
    )

    assert [item["action_code"] for item in actions] == ["invite_bot_to_chats", "complete_user_authorization"]
    assert actions[0]["count"] == 1
    assert actions[0]["title"] == "确认关键群是否接入数字助理"
    assert actions[0]["owner_next_step"] == "先按建议等级确认是否为经营关键群；确认后再请业务负责人邀请机器人入群。"
    assert "日报、风险和问答" in actions[0]["business_impact"]
    assert actions[0]["business_domain"] == "销售/客户"
    assert actions[0]["responsible_role"] == "销售负责人"
    assert actions[0]["resources"][0]["responsible_role"] == "销售负责人"
    assert actions[0]["access_recommendation_summary"] == "建议接入 1 个"
    assert actions[0]["recommended_notify_targets"] == ["销售负责人"]
    assert actions[0]["resources"][0]["access_recommendation"]["label"] == "建议接入"
    assert actions[0]["affected_resources"] == "销售群"
    assert "涉及：销售群。" in actions[0]["owner_next_step_detail"]


def test_v5_owner_actions_do_not_recommend_low_value_chat_notifications() -> None:
    actions = owner_actions_from_resource_items(
        [
            {
                "id": "chat-1",
                "resource_name": "茶水闲聊群",
                "resource_type": "chat",
                "status": "access_blocked",
                "config_json": {"governance": {"status": "bot_not_in_chat"}},
                "next_action": "请先在飞书中把机器人加入对应群，或停用该资源。",
            },
            {
                "id": "chat-2",
                "resource_name": "研发测试质量群",
                "resource_type": "chat",
                "status": "access_blocked",
                "config_json": {"governance": {"status": "bot_not_in_chat"}},
                "next_action": "请先在飞书中把机器人加入对应群，或停用该资源。",
            },
        ]
    )

    chat_action = actions[0]
    recommendations = {
        item["resource_name"]: item["access_recommendation"]
        for item in chat_action["resources"]
    }

    assert chat_action["access_recommendation_summary"] == "不建议接入 1 个 / 建议接入 1 个"
    assert recommendations["茶水闲聊群"]["recommended_notify_target"] == "不通知"
    assert recommendations["茶水闲聊群"]["label"] == "不建议接入"
    assert recommendations["研发测试质量群"]["recommended_notify_target"] == "研发负责人"
    assert recommendations["研发测试质量群"]["label"] == "建议接入"


def test_v5_owner_actions_respect_manual_access_decisions() -> None:
    ignored = {
        "id": "chat-ignored",
        "resource_name": "销售临时群",
        "resource_type": "chat",
        "status": "access_blocked",
        "config_json": {"governance": {"status": "bot_not_in_chat", "access_decision": "do_not_connect"}},
    }
    business = {
        "id": "chat-business",
        "resource_name": "普通群",
        "resource_type": "chat",
        "status": "access_blocked",
        "config_json": {"governance": {"status": "bot_not_in_chat", "access_decision": "business_group"}},
    }
    confirmed = {
        "id": "chat-confirmed",
        "resource_name": "项目群",
        "resource_type": "chat",
        "status": "access_blocked",
        "config_json": {"governance": {"status": "bot_not_in_chat", "access_decision": "owner_confirmed"}},
    }

    actions = owner_actions_from_resource_items([ignored, business, confirmed])
    recommendations = {
        item["resource_id"]: item["access_recommendation"]
        for item in actions[0]["resources"]
    }

    assert actions[0]["count"] == 2
    assert "chat-ignored" not in recommendations
    assert recommendations["chat-business"]["reason"] == "已标记为业务群"
    assert recommendations["chat-business"]["label"] == "建议接入"
    assert recommendations["chat-confirmed"]["label"] == "已确认接入"


def test_v5_resource_access_decision_updates_governance_config() -> None:
    resource = type("ResourceStub", (), {"config_json": {"governance": {"status": "bot_not_in_chat"}}})()

    governance = set_resource_access_decision(resource, decision="business_group", note="老板确认")

    assert governance["status"] == "bot_not_in_chat"
    assert governance["access_decision"] == "business_group"
    assert governance["access_decision_label"] == "业务群"
    assert governance["access_decision_note"] == "老板确认"
    assert resource.config_json["governance"]["access_decision"] == "business_group"

    reset = set_resource_access_decision(resource, decision="reset")

    assert reset == {"status": "bot_not_in_chat"}
    assert "access_decision" not in resource.config_json["governance"]


def test_v5_resource_access_block_governance_stops_auto_sync() -> None:
    class ResourceStub:
        def __init__(self):
            self.id = uuid4()
            self.company_id = uuid4()
            self.platform = "feishu"
            self.resource_type = "chat"
            self.resource_id = "oc_x"
            self.resource_sub_id = None
            self.resource_name = "销售群"
            self.sync_mode = "realtime"
            self.permission_level = "team"
            self.enabled = True
            self.last_sync_at = None
            self.app_config_id = None
            self.config_json = {"governance": {"status": "bot_not_in_chat"}}

    resource = ResourceStub()
    decision = sync_decision_for_resource(resource)
    payload = _resource_sync_payload(
        resource,
        now=datetime.now(UTC),
        event_counts={},
        policy={},
    )

    assert decision["status"] == "access_blocked"
    assert decision["auto_sync_allowed"] is False
    assert payload["status"] == "access_blocked"
    assert payload["next_action"] == "请先在飞书中把机器人加入对应群，或停用该资源。"


def test_v5_sync_decision_requires_complete_feishu_identifiers() -> None:
    class ResourceStub:
        def __init__(self, resource_type, resource_id=None, resource_sub_id=None):
            self.resource_type = resource_type
            self.resource_id = resource_id
            self.resource_sub_id = resource_sub_id
            self.platform = "feishu"
            self.enabled = True
            self.config_json = {}

    missing_bitable = sync_decision_for_resource(ResourceStub("bitable_table", "bascn_x"))
    generic_calendar = sync_decision_for_resource(ResourceStub("capability", "feishu:calendar"))

    assert missing_bitable["status"] == "manual_review_required"
    assert missing_bitable["identifier_issue"]["code"] == "missing_required_identifier"
    assert "auto_discovered_bitable" in missing_bitable["reason"]
    assert missing_bitable["query_path"] == "lark_cli_first_then_index"
    assert missing_bitable["auto_sync_allowed"] is False
    assert generic_calendar["status"] == "access_blocked"
    assert generic_calendar["identifier_issue"]["code"] == "requires_user_authorization"
    assert generic_calendar["next_action"] == "请先完成飞书用户授权；系统会继续自动发现可同步资源。"


def test_v5_batch_sync_defaults_to_actionable_resource_statuses() -> None:
    request = ResourceBatchSyncRequest(company_id=uuid4())

    assert request.statuses == ["never_synced", "stale"]
    assert request.resource_type is None
    assert request.limit_resources == 20


def test_v5_company_auto_sync_selector_filters_by_policy() -> None:
    company_id = uuid4()

    class ResourceStub:
        def __init__(self, resource_type, resource_id, resource_sub_id=None):
            self.id = uuid4()
            self.company_id = company_id
            self.platform = "feishu"
            self.resource_type = resource_type
            self.resource_id = resource_id
            self.resource_sub_id = resource_sub_id
            self.resource_name = resource_id
            self.sync_mode = "manual"
            self.permission_level = "company"
            self.enabled = True
            self.last_sync_at = None
            self.created_at = None
            self.app_config_id = uuid4()
            self.config_json = {}

    class ScalarResult:
        def all(self):
            return [
                ResourceStub("approval_code", "approval_x"),
                ResourceStub("drive_file", "doccn_x"),
                ResourceStub("unknown", "unknown_x"),
            ]

    class FakeDb:
        def scalars(self, query):
            return ScalarResult()

    policy = {
        "resource_types": ["approval"],
        "statuses": ["never_synced"],
        "limit_resources": 10,
        "large_document_mode": "index_only",
        "bitable_mode": "master_data_index",
    }

    resources = select_company_auto_sync_resources(FakeDb(), company_id=company_id, policy=policy)

    assert len(resources) == 1
    assert resources[0].resource_type == "approval_code"


def test_v5_company_auto_sync_orchestrates_resource_runs(monkeypatch) -> None:
    company_id = uuid4()
    resource_id = uuid4()
    run_id = uuid4()

    resource = type("ResourceStub", (), {"id": resource_id})()
    run = type("SyncRunStub", (), {"id": run_id})()

    class FakeDb:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

    def fake_select_resources(db, *, company_id, policy):
        return [resource]

    def fake_start_sync_run(*args, **kwargs):
        assert kwargs["company_id"] == company_id
        assert kwargs["provider"] == "v5_resource"
        assert kwargs["sync_type"] == "auto"
        return run

    def fake_finish_sync_run(sync_run, **kwargs):
        assert sync_run is run
        assert kwargs["status"] == "success"
        assert kwargs["saved_count"] == 2
        return sync_run

    async def fake_sync_resource(db, **kwargs):
        assert kwargs["resource_id"] == resource_id
        assert kwargs["limit"] == 20
        assert kwargs["bitable_mode"] == "master_data_index"
        return {"resource_id": str(resource_id), "saved_count": 2}

    monkeypatch.setattr("app.services.v5_auto_sync.select_company_auto_sync_resources", fake_select_resources)
    monkeypatch.setattr("app.services.v5_auto_sync.start_sync_run", fake_start_sync_run)
    monkeypatch.setattr("app.services.v5_auto_sync.finish_sync_run", fake_finish_sync_run)

    policy = {
        "event_limit": 20,
        "max_pages": 2,
        "extract_items": True,
        "large_document_mode": "index_only",
        "bitable_mode": "master_data_index",
        "memory_mode": "stable_facts_only",
        "vector_mode": "summaries_and_hot_knowledge",
    }

    import asyncio

    db = FakeDb()
    result = asyncio.run(
        sync_company_auto_resources(db, company_id=company_id, policy=policy, sync_resource=fake_sync_resource)
    )

    assert result["sync_run_id"] == str(run_id)
    assert result["selected_resource_ids"] == [str(resource_id)]
    assert result["saved_count"] == 2
    assert result["errors"] == []
    assert db.commits == 2
    assert db.rollbacks == 0


def test_v5_company_auto_sync_due_compares_interval_seconds() -> None:
    company_id = uuid4()
    policy = {"interval_seconds": 900}

    class FakeDb:
        def __init__(self, latest):
            self.latest = latest

        def scalar(self, query):
            return self.latest

    assert company_auto_sync_due(FakeDb(None), company_id=company_id, policy=policy) is True

    recent = SimpleNamespace(started_at=datetime.now(UTC) - timedelta(seconds=60))
    assert company_auto_sync_due(FakeDb(recent), company_id=company_id, policy=policy) is False

    stale = SimpleNamespace(started_at=datetime.now(UTC) - timedelta(seconds=901))
    assert company_auto_sync_due(FakeDb(stale), company_id=company_id, policy=policy) is True


def test_v5_resource_sync_policy_normalizes_console_settings() -> None:
    policy = normalize_v5_resource_sync_policy(
        {
            "enabled": True,
            "interval_seconds": 10,
            "limit_resources": 999,
            "event_limit": 0,
            "max_pages": 99,
            "resource_types": "approval,mailbox,approval",
            "statuses": ["never_synced", "stale", "stale"],
        }
    )

    assert policy["enabled"] is True
    assert policy["interval_seconds"] == 60
    assert policy["limit_resources"] == 100
    assert policy["event_limit"] == 1
    assert policy["max_pages"] == 20
    assert policy["resource_types"] == ["approval", "mailbox"]
    assert policy["statuses"] == ["never_synced", "stale"]
    assert policy["large_document_mode"] == "index_only"
    assert policy["bitable_mode"] == "master_data_index"
    assert policy["memory_mode"] == "stable_facts_only"


def test_v5_enabled_sync_policies_fall_back_per_unconfigured_company(monkeypatch) -> None:
    configured_company_id = uuid4()
    fallback_company_id = uuid4()

    class ScalarResult:
        def __init__(self, items):
            self.items = items

        def all(self):
            return self.items

    class FakeDb:
        def __init__(self):
            self.calls = 0

        def scalars(self, query):
            self.calls += 1
            if self.calls == 1:
                return ScalarResult(
                    [
                        type(
                            "CompanySettingStub",
                            (),
                            {
                                "company_id": configured_company_id,
                                "settings": {"v5_resource_sync_policy": {"enabled": False}},
                            },
                        )()
                    ]
                )
            return ScalarResult([configured_company_id, fallback_company_id])

    monkeypatch.setattr(
        "app.services.v5_sync_policy.default_v5_resource_sync_policy",
        lambda: {
            "enabled": True,
            "interval_seconds": 900,
            "limit_resources": 10,
            "event_limit": 20,
            "max_pages": 2,
            "resource_types": ["approval"],
            "statuses": ["never_synced"],
            "extract_items": True,
            "large_document_mode": "index_only",
            "bitable_mode": "master_data_index",
            "memory_mode": "stable_facts_only",
            "vector_mode": "summaries_and_hot_knowledge",
            "source": "settings_default",
        },
    )

    policies = enabled_v5_resource_sync_policies(FakeDb())

    assert [(company_id, policy["source"]) for company_id, policy in policies] == [
        (fallback_company_id, "settings_default")
    ]


def test_v5_sync_decision_prevents_large_data_over_sync() -> None:
    class ResourceStub:
        def __init__(self, resource_type, *, enabled=True):
            self.resource_type = resource_type
            self.resource_id = "doccn_x"
            self.platform = "feishu"
            self.enabled = enabled

    default_doc = sync_decision_for_resource(ResourceStub("drive_file"))
    hot_doc = sync_decision_for_resource(
        ResourceStub("drive_file"),
        {"large_document_mode": "knowledge_vectorize"},
    )
    hot_wiki_space = sync_decision_for_resource(
        ResourceStub("wiki_space"),
        {"large_document_mode": "knowledge_vectorize"},
    )
    excluded_chat = sync_decision_for_resource(
        ResourceStub("chat"),
        {"resource_types": ["approval"]},
    )
    redacted_approval = ResourceStub("approval_code")
    redacted_approval.resource_id = "AE296D4C-57FA-4A96-9B[PHONE_REDACTED]A36"
    redacted_decision = sync_decision_for_resource(redacted_approval)

    assert default_doc["sync_action"] == "document_index_only"
    assert default_doc["query_path"] == "lark_cli_realtime"
    assert default_doc["executable"] is True
    assert hot_doc["sync_action"] == "knowledge_vectorize"
    assert hot_doc["query_path"] == "local_rag"
    assert hot_doc["executable"] is True
    assert hot_doc["status"] == "ready"
    assert hot_doc["next_action"] == "同步全文到本地知识索引，并进入向量化队列。"
    assert hot_wiki_space["sync_action"] == "document_index_only"
    assert hot_wiki_space["query_path"] == "lark_cli_realtime"
    assert hot_wiki_space["vectorize"] == "outline_and_summary_only"
    assert hot_wiki_space["executable"] is True
    assert "具体 Wiki 文档 token" in hot_wiki_space["reason"]
    assert excluded_chat["status"] == "policy_excluded"
    assert excluded_chat["auto_sync_allowed"] is False
    assert redacted_decision["status"] == "manual_review_required"
    assert redacted_decision["executable"] is False
    assert redacted_decision["has_redacted_identifier"] is True


def test_resource_sync_run_service_records_required_v5_fields() -> None:
    class FakeDb:
        def __init__(self):
            self.added = None
            self.flushed = False

        def add(self, item):
            self.added = item

        def flush(self):
            self.flushed = True

    company_id = uuid4()
    resource_id = uuid4()
    db = FakeDb()

    run = start_resource_sync_run(
        db,
        company_id=company_id,
        resource_id=resource_id,
        sync_action="realtime_event",
    )
    finish_resource_sync_run(
        run,
        status="success",
        items_seen=3,
        items_indexed=2,
        items_skipped=1,
    )

    assert isinstance(run, ResourceSyncRun)
    assert db.added is run
    assert db.flushed is True
    assert run.company_id == company_id
    assert run.resource_id == resource_id
    assert run.sync_action == "realtime_event"
    assert run.status == "success"
    assert run.finished_at is not None
    assert run.items_seen == 3
    assert run.items_indexed == 2
    assert run.items_skipped == 1


def test_v5_resource_type_normalization_keeps_legacy_discovery_useful() -> None:
    assert normalize_v5_resource_type("mail_folder") == "mailbox"
    assert normalize_v5_resource_type("approval_code") == "approval"
    assert normalize_v5_resource_type("drive_file") == "doc"
    assert normalize_v5_resource_type("wiki_space") == "wiki"
    assert normalize_v5_resource_type("bitable_table") == "bitable"
    assert normalize_v5_resource_type("web_page") == "web"
    assert normalize_v5_resource_type("capability", "feishu:calendar") == "calendar"
    assert normalize_v5_resource_type("capability", "feishu:meetings") == "meeting"
    assert normalize_v5_resource_type("capability", "feishu:tasks") == "task"


def test_v5_resource_payload_classifies_normalized_capabilities_as_operational_data() -> None:
    resource = SimpleNamespace(
        id=uuid4(),
        company_id=uuid4(),
        platform="feishu",
        resource_type="capability",
        resource_name="飞书通讯录",
        resource_id="feishu:contacts",
        resource_sub_id=None,
        legacy_feishu_resource_id=None,
        sync_mode="auto",
        permission_level="company",
        data_classification="company",
        business_domain="people",
        enabled=True,
        last_sync_at=None,
        app_config_id=None,
        config_json={},
    )

    payload = resource_to_v5_payload(resource)

    assert payload["resource_type"] == "directory"
    assert payload["data_type"] == "operational_data"
    assert payload["storage_layer"] == "postgresql"
    assert payload["data_layer"] == "realtime_work_events"


def test_v5_resource_payload_keeps_external_web_outside_internal_vector_store() -> None:
    resource = SimpleNamespace(
        id=uuid4(),
        company_id=uuid4(),
        platform="web",
        resource_type="web_page",
        resource_name="行业政策",
        resource_id="https://example.com/policy",
        resource_sub_id=None,
        legacy_feishu_resource_id=None,
        sync_mode="manual",
        permission_level="company",
        data_classification="public",
        business_domain="market",
        enabled=True,
        last_sync_at=None,
        app_config_id=None,
        config_json={"source_type": "external_web"},
    )

    payload = resource_to_v5_payload(resource)

    assert payload["resource_type"] == "web"
    assert payload["data_type"] == "knowledge_data"
    assert payload["storage_layer"] == "web"
    assert payload["data_layer"] == "knowledge_external_web"


def test_v5_resource_payload_classifies_memory_as_long_term_memory_data() -> None:
    resource = SimpleNamespace(
        id=uuid4(),
        company_id=uuid4(),
        platform="advisor",
        resource_type="memory_fact",
        resource_name="老板偏好",
        resource_id="memory:owner_preferences",
        resource_sub_id=None,
        legacy_feishu_resource_id=None,
        sync_mode="derived",
        permission_level="personal",
        data_classification="personal",
        business_domain="general",
        enabled=True,
        last_sync_at=None,
        app_config_id=None,
        config_json={},
    )

    payload = resource_to_v5_payload(resource)

    assert payload["resource_type"] == "memory"
    assert payload["data_type"] == "memory_data"
    assert payload["storage_layer"] == "memory_facts"
    assert payload["data_layer"] == "long_term_memory"


def test_v5_legacy_mail_resource_identity_is_canonicalized() -> None:
    legacy = type(
        "LegacyResourceStub",
        (),
        {
            "resource_type": "mail_folder",
            "external_id": "jun.chen@gaustek.com:INBOX",
            "settings": {"user_mailbox_id": "jun.chen@gaustek.com", "folder_id": "INBOX"},
        },
    )()

    assert _legacy_resource_identity(legacy) == ("jun.chen@gaustek.com", "INBOX")


def test_work_event_create_supports_v5_resource_binding() -> None:
    resource_id = uuid4()
    event = WorkEventCreate(
        company_id=uuid4(),
        resource_id=resource_id,
        source="feishu",
        event_type="approval",
        payload={"summary": "redacted"},
        raw_json={"raw": True},
        importance_score=0.7,
    )

    assert event.resource_id == resource_id
    assert event.raw_json == {"raw": True}
    assert event.importance_score == 0.7


def test_v5_resource_sync_args_map_resources_to_workspace_sources() -> None:
    class ResourceStub:
        def __init__(self, resource_type, resource_id, resource_sub_id=None, config_json=None):
            self.resource_type = resource_type
            self.resource_id = resource_id
            self.resource_sub_id = resource_sub_id
            self.config_json = config_json or {}

    assert _sync_args_for_resource(ResourceStub("mail_folder", "jun.chen@gaustek.com", "INBOX")) == {
        "kinds": ["mail"],
        "user_mailbox_id": "jun.chen@gaustek.com",
        "folder_id": "INBOX",
    }
    assert _sync_args_for_resource(ResourceStub("chat", "oc_x")) == {"kinds": ["messages"], "chat_id": "oc_x"}
    assert _sync_args_for_resource(ResourceStub("approval_code", "approval_x")) == {
        "kinds": ["approvals"],
        "approval_code": "approval_x",
    }
    assert _sync_args_for_resource(ResourceStub("capability", "feishu:tasks")) == {"kinds": ["tasks"]}
    assert _sync_args_for_resource(ResourceStub("bitable_table", "bascn_x", "tbl_x")) == {
        "kinds": ["bitable"],
        "app_token": "bascn_x",
        "table_id": "tbl_x",
    }


def test_v5_resource_sync_uses_preferred_resource_source(monkeypatch) -> None:
    resource_id = uuid4()
    legacy_app_config_id = uuid4()
    source_app_config_id = uuid4()
    event_id = uuid4()

    class ResourceStub:
        def __init__(self):
            self.id = resource_id
            self.company_id = uuid4()
            self.platform = "feishu"
            self.app_config_id = legacy_app_config_id
            self.resource_type = "chat"
            self.resource_id = "oc_x"
            self.resource_sub_id = None
            self.resource_name = "销售群"
            self.sync_mode = "realtime_and_history"
            self.permission_level = "team"
            self.enabled = True
            self.last_sync_at = None
            self.config_json = {}

    resource = ResourceStub()
    source = SimpleNamespace(
        id=uuid4(),
        resource_id=resource.id,
        source_type="feishu_app_identity",
        source_account_id=str(source_app_config_id),
        source_account_label="固势自建应用",
        access_level="read",
        can_sync=True,
        priority=100,
        visibility_scope="team",
        allowed_user_ids=[],
        allowed_roles=["owner"],
        allowed_departments=[],
        limitations={},
        last_seen_at=datetime.now(UTC),
    )
    source_app_config = SimpleNamespace(id=source_app_config_id)
    event = SimpleNamespace(
        id=event_id,
        resource_id=None,
        source_type="unknown",
        source_account_id=None,
        visibility_scope="company",
        allowed_user_ids=[],
        allowed_roles=[],
        allowed_departments=[],
    )

    class ScalarResult:
        def all(self):
            return [event]

    class FakeDb:
        committed = False

        def get(self, model, item_id):
            if item_id == resource.id:
                return resource
            if item_id == source_app_config_id:
                return source_app_config
            return None

        def scalars(self, query):
            return ScalarResult()

        def commit(self):
            self.committed = True

    async def fake_sync_feishu_information(db, app_config, **kwargs):
        assert app_config is source_app_config
        assert kwargs["chat_id"] == "oc_x"
        return {"messages": {"available": True, "saved_count": 1, "work_event_ids": [str(event_id)]}}

    sync_run = SimpleNamespace(id=uuid4())

    monkeypatch.setattr("app.services.v5_workspace.preferred_sync_source", lambda db, resource: source)
    monkeypatch.setattr("app.services.v5_workspace.sync_feishu_information", fake_sync_feishu_information)
    monkeypatch.setattr("app.services.v5_workspace.start_resource_sync_run", lambda *args, **kwargs: sync_run)
    monkeypatch.setattr("app.services.v5_workspace.finish_resource_sync_run", lambda run, **kwargs: run)

    import asyncio

    db = FakeDb()
    result = asyncio.run(sync_v5_resource(db, resource_id=resource.id))

    assert result["available"] is True
    assert result["source"]["source_type"] == "feishu_app_identity"
    assert result["source"]["data_source"] == "feishu_enterprise_app"
    assert event.resource_id == resource.id
    assert event.source_type == "feishu_app_identity"
    assert event.source_account_id == str(source_app_config_id)
    assert event.visibility_scope == "team"
    assert event.allowed_roles == ["owner"]
    assert db.committed is True


def test_v5_resource_sync_uses_feishu_user_source(monkeypatch) -> None:
    resource_id = uuid4()
    app_config_id = uuid4()
    account_id = uuid4()
    event_id = uuid4()

    class ResourceStub:
        def __init__(self):
            self.id = resource_id
            self.company_id = uuid4()
            self.platform = "feishu"
            self.app_config_id = app_config_id
            self.resource_type = "bitable_table"
            self.resource_id = "bascn_x"
            self.resource_sub_id = "tbl_x"
            self.resource_name = "CRM+进销存管理系统"
            self.sync_mode = "scheduled"
            self.permission_level = "department"
            self.data_classification = "company"
            self.business_domain = "operations"
            self.enabled = True
            self.last_sync_at = None
            self.config_json = {}

    resource = ResourceStub()
    source = SimpleNamespace(
        id=uuid4(),
        resource_id=resource.id,
        source_type="feishu_user_identity",
        source_account_id=str(account_id),
        source_account_label="陈俊",
        access_level="read",
        can_sync=True,
        priority=80,
        visibility_scope="department",
        allowed_user_ids=[str(account_id)],
        allowed_roles=["owner"],
        allowed_departments=["od_sales"],
        limitations={},
        last_seen_at=datetime.now(UTC),
    )
    app_config = SimpleNamespace(id=app_config_id)
    event = SimpleNamespace(
        id=event_id,
        resource_id=None,
        source_type="unknown",
        source_account_id=None,
        visibility_scope="company",
        data_classification="company",
        business_domain="general",
        allowed_user_ids=[],
        allowed_roles=[],
        allowed_departments=[],
    )

    class ScalarResult:
        def all(self):
            return [event]

    class FakeDb:
        committed = False

        def get(self, model, item_id):
            if item_id == resource.id:
                return resource
            if item_id == app_config_id:
                return app_config
            return None

        def scalars(self, query):
            return ScalarResult()

        def commit(self):
            self.committed = True

    async def fake_user_sync(db, *, app_config, resource, source, limit, max_pages, extract_items):
        assert app_config is not None
        assert resource.resource_sub_id == "tbl_x"
        assert source.source_type == "feishu_user_identity"
        return {"bitable": {"available": True, "saved_count": 1, "work_event_ids": [str(event_id)]}}

    sync_run = SimpleNamespace(id=uuid4())
    monkeypatch.setattr("app.services.v5_workspace.preferred_sync_source", lambda db, resource: source)
    monkeypatch.setattr("app.services.v5_workspace._sync_user_authorized_resource", fake_user_sync)
    monkeypatch.setattr("app.services.v5_workspace.start_resource_sync_run", lambda *args, **kwargs: sync_run)
    monkeypatch.setattr("app.services.v5_workspace.finish_resource_sync_run", lambda run, **kwargs: run)

    import asyncio

    db = FakeDb()
    result = asyncio.run(sync_v5_resource(db, resource_id=resource.id))

    assert result["available"] is True
    assert result["source"]["source_type"] == "feishu_user_identity"
    assert result["source"]["data_source"] == "feishu_personal_user"
    assert event.resource_id == resource.id
    assert event.source_type == "feishu_user_identity"
    assert event.source_account_id == str(account_id)
    assert event.visibility_scope == "department"
    assert event.data_classification == "company"
    assert event.business_domain == "operations"
    assert event.allowed_user_ids == [str(account_id)]
    assert event.allowed_roles == ["owner"]
    assert event.allowed_departments == ["od_sales"]
    assert db.committed is True


def test_v5_doc_resource_sync_indexes_metadata_without_fetching_full_content(monkeypatch) -> None:
    class FakeDb:
        def __init__(self, resource, app_config, event=None):
            self.resource = resource
            self.app_config = app_config
            self.event = event
            self.committed = False

        def get(self, model, item_id):
            if item_id == self.resource.id:
                return self.resource
            if item_id == self.app_config.id:
                return self.app_config
            return None

        def scalars(self, query):
            event = self.event

            class ScalarResult:
                def all(self):
                    return [event] if event is not None else []

            return ScalarResult()

        def commit(self):
            self.committed = True

    class ResourceStub:
        def __init__(self):
            self.id = uuid4()
            self.company_id = uuid4()
            self.platform = "feishu"
            self.app_config_id = uuid4()
            self.resource_type = "drive_file"
            self.resource_id = "doccn_x"
            self.resource_sub_id = None
            self.resource_name = "大型技术手册"
            self.permission_level = "company"
            self.config_json = {"document_type": "docx"}
            self.last_sync_at = None

    resource = ResourceStub()
    app_config = type("AppConfig", (), {"id": resource.app_config_id})()
    created_event = type("Event", (), {"id": uuid4(), "resource_id": None, "vector_status": "pending"})()
    db = FakeDb(resource, app_config, created_event)
    called = {"feishu": False}

    async def fake_sync_feishu_information(*args, **kwargs):
        called["feishu"] = True
        return {}

    def fake_upsert_work_event(db_arg, data):
        assert data.event_type == "feishu.resource.index"
        assert data.resource_id == resource.id
        assert data.payload["data_layer"] == "knowledge_cold"
        assert data.payload["query_path"] == "lark_cli_realtime"
        assert "查询路径：lark_cli_realtime" in data.content_text
        return created_event

    run_id = uuid4()
    sync_run = type("SyncRun", (), {"id": run_id})()

    def fake_start_resource_sync_run(*args, **kwargs):
        assert kwargs["company_id"] == resource.company_id
        assert kwargs["resource_id"] == resource.id
        assert kwargs["sync_action"] == "document_index_only"
        return sync_run

    def fake_finish_resource_sync_run(run, **kwargs):
        assert run is sync_run
        assert kwargs["status"] == "success"
        assert kwargs["items_seen"] == 1
        assert kwargs["items_indexed"] == 1
        assert kwargs["summary"]["query_path"] == "lark_cli_realtime"
        assert "rag_indexing" not in kwargs["summary"]
        return run

    monkeypatch.setattr("app.services.v5_workspace.sync_feishu_information", fake_sync_feishu_information)
    monkeypatch.setattr("app.services.v5_workspace.upsert_work_event", fake_upsert_work_event)
    monkeypatch.setattr("app.services.v5_workspace.start_resource_sync_run", fake_start_resource_sync_run)
    monkeypatch.setattr("app.services.v5_workspace.finish_resource_sync_run", fake_finish_resource_sync_run)
    monkeypatch.setattr("app.services.v5_workspace.preferred_sync_source", lambda db, resource: None)

    import asyncio

    result = asyncio.run(sync_v5_resource(db, resource_id=resource.id))

    assert called["feishu"] is False
    assert db.committed is True
    assert result["data_layer"] == "knowledge_cold"
    assert result["resource_sync_run_id"] == str(run_id)
    assert result["sync_action"] == "document_index_only"
    assert result["query_path"] == "lark_cli_realtime"
    assert "rag_indexing" not in result
    assert result["saved_count"] == 1
    assert result["work_event_ids"] == [str(created_event.id)]
    assert created_event.vector_status == "skipped"


def test_v5_external_web_resource_sync_never_enters_internal_or_feishu_sync(monkeypatch) -> None:
    class ResourceStub:
        def __init__(self):
            self.id = uuid4()
            self.company_id = uuid4()
            self.platform = "web"
            self.app_config_id = None
            self.resource_type = "web_page"
            self.resource_id = "https://example.com/policy"
            self.resource_sub_id = None
            self.resource_name = "外部政策资料"
            self.permission_level = "company"
            self.config_json = {"source_type": "external_web"}
            self.last_sync_at = None
            self.enabled = True

    class FakeDb:
        def __init__(self, resource):
            self.resource = resource
            self.committed = False

        def get(self, model, item_id):
            if item_id == self.resource.id:
                return self.resource
            raise AssertionError("external web sync must not load Feishu app config")

        def commit(self):
            self.committed = True

    resource = ResourceStub()
    db = FakeDb(resource)
    run_id = uuid4()
    sync_run = type("SyncRun", (), {"id": run_id})()

    def fake_start_resource_sync_run(*args, **kwargs):
        assert kwargs["company_id"] == resource.company_id
        assert kwargs["resource_id"] == resource.id
        assert kwargs["sync_action"] == "external_realtime_reference"
        return sync_run

    def fake_finish_resource_sync_run(run, **kwargs):
        assert run is sync_run
        assert kwargs["status"] == "skipped"
        assert kwargs["items_skipped"] == 1
        assert kwargs["summary"]["data_layer"] == "knowledge_external_web"
        assert kwargs["summary"]["query_path"] == "external_search_realtime"
        assert "rag_indexing" not in kwargs["summary"]
        return run

    monkeypatch.setattr("app.services.v5_workspace.start_resource_sync_run", fake_start_resource_sync_run)
    monkeypatch.setattr("app.services.v5_workspace.finish_resource_sync_run", fake_finish_resource_sync_run)
    monkeypatch.setattr(
        "app.services.v5_workspace.sync_feishu_information",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("external web must not call Feishu sync")),
    )
    monkeypatch.setattr(
        "app.services.v5_workspace.upsert_work_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("external web must not write internal WorkEvent")),
    )

    import asyncio

    result = asyncio.run(sync_v5_resource(db, resource_id=resource.id))

    assert db.committed is True
    assert result["available"] is False
    assert result["resource_sync_run_id"] == str(run_id)
    assert result["resource_type"] == "web"
    assert result["data_layer"] == "knowledge_external_web"
    assert result["sync_action"] == "external_realtime_reference"
    assert result["query_path"] == "external_search_realtime"
    assert result["decision_status"] == "unsupported"
    assert result["saved_count"] == 0


def test_v5_wiki_space_sync_indexes_metadata_even_when_vectorize_requested(monkeypatch) -> None:
    class FakeDb:
        def __init__(self, resource, app_config, event=None):
            self.resource = resource
            self.app_config = app_config
            self.event = event
            self.committed = False

        def get(self, model, item_id):
            if item_id == self.resource.id:
                return self.resource
            if item_id == self.app_config.id:
                return self.app_config
            return None

        def scalars(self, query):
            event = self.event

            class ScalarResult:
                def all(self):
                    return [event] if event is not None else []

            return ScalarResult()

        def commit(self):
            self.committed = True

    class ResourceStub:
        def __init__(self):
            self.id = uuid4()
            self.company_id = uuid4()
            self.platform = "feishu"
            self.app_config_id = uuid4()
            self.resource_type = "wiki_space"
            self.resource_id = "spc_x"
            self.resource_sub_id = None
            self.resource_name = "公司知识库"
            self.permission_level = "company"
            self.config_json = {}
            self.last_sync_at = None

    resource = ResourceStub()
    app_config = type("AppConfig", (), {"id": resource.app_config_id})()
    created_event = type("Event", (), {"id": uuid4(), "resource_id": None, "vector_status": "pending"})()
    db = FakeDb(resource, app_config, created_event)
    called = {"feishu": False}

    async def fake_sync_feishu_information(*args, **kwargs):
        called["feishu"] = True
        return {}

    def fake_upsert_work_event(db_arg, data):
        assert data.event_type == "feishu.resource.index"
        assert data.resource_id == resource.id
        assert data.payload["resource_type"] == "wiki"
        assert data.payload["data_layer"] == "knowledge_cold"
        assert data.payload["sync_action"] == "document_index_only"
        assert data.payload["query_path"] == "lark_cli_realtime"
        assert "查询路径：lark_cli_realtime" in data.content_text
        return created_event

    monkeypatch.setattr("app.services.v5_workspace.sync_feishu_information", fake_sync_feishu_information)
    monkeypatch.setattr("app.services.v5_workspace.upsert_work_event", fake_upsert_work_event)
    monkeypatch.setattr(
        "app.services.v5_workspace.start_resource_sync_run",
        lambda *args, **kwargs: type("SyncRun", (), {"id": uuid4()})(),
    )
    def fake_finish_resource_sync_run(*args, **kwargs):
        assert "rag_indexing" not in kwargs["summary"]

    monkeypatch.setattr("app.services.v5_workspace.finish_resource_sync_run", fake_finish_resource_sync_run)
    monkeypatch.setattr("app.services.v5_workspace.preferred_sync_source", lambda db, resource: None)

    import asyncio

    result = asyncio.run(
        sync_v5_resource(db, resource_id=resource.id, large_document_mode="knowledge_vectorize")
    )

    assert called["feishu"] is False
    assert db.committed is True
    assert result["resource_type"] == "wiki"
    assert result["data_layer"] == "knowledge_cold"
    assert result["sync_action"] == "document_index_only"
    assert result["query_path"] == "lark_cli_realtime"
    assert "rag_indexing" not in result
    assert result["saved_count"] == 1
    assert result["work_event_ids"] == [str(created_event.id)]
    assert created_event.vector_status == "skipped"


def test_v5_hot_doc_resource_sync_fetches_content_for_local_rag(monkeypatch) -> None:
    class FakeDb:
        def __init__(self, resource, app_config):
            self.resource = resource
            self.app_config = app_config
            self.committed = False

        def get(self, model, item_id):
            if item_id == self.resource.id:
                return self.resource
            if item_id == self.app_config.id:
                return self.app_config
            return None

        def scalars(self, query):
            class ScalarResult:
                def all(self):
                    return []

            return ScalarResult()

        def commit(self):
            self.committed = True

    class ResourceStub:
        def __init__(self):
            self.id = uuid4()
            self.company_id = uuid4()
            self.platform = "feishu"
            self.app_config_id = uuid4()
            self.resource_type = "drive_file"
            self.resource_id = "doccn_hot"
            self.resource_sub_id = None
            self.resource_name = "高频制度文档"
            self.permission_level = "company"
            self.config_json = {"document_type": "docx"}
            self.last_sync_at = None
            self.enabled = True

    resource = ResourceStub()
    app_config = type("AppConfig", (), {"id": resource.app_config_id, "company_id": resource.company_id})()
    db = FakeDb(resource, app_config)
    event_id = uuid4()
    called = {}

    async def fake_sync_feishu_information(*args, **kwargs):
        called["kwargs"] = kwargs
        return {
            "docx": {
                "available": True,
                "saved_count": 1,
                "work_event_ids": [str(event_id)],
                "document_store": {
                    "store": "work_events",
                    "work_event_ids": [str(event_id)],
                    "chunk_count": 2,
                    "vector_db": "qdrant_vectors",
                    "rag_index": "pending",
                    "vector_status": "pending",
                },
                "rag_indexing": {
                    "document_store": "work_events",
                    "chunking": "pending",
                    "vector_db": "qdrant_vectors",
                    "rag_index": "pending",
                    "vector_status": "pending",
                    "vectorize": "full_chunk_or_summary_chunk",
                },
            }
        }

    run_id = uuid4()
    sync_run = type("SyncRun", (), {"id": run_id})()
    finished = {}

    def fake_start_resource_sync_run(*args, **kwargs):
        assert kwargs["company_id"] == resource.company_id
        assert kwargs["resource_id"] == resource.id
        assert kwargs["sync_action"] == "knowledge_vectorize"
        assert kwargs["summary"]["data_layer"] == "knowledge_hot"
        assert kwargs["summary"]["query_path"] == "local_rag"
        return sync_run

    def fake_finish_resource_sync_run(run, **kwargs):
        assert run is sync_run
        finished.update(kwargs)
        return run

    monkeypatch.setattr("app.services.v5_workspace.sync_feishu_information", fake_sync_feishu_information)
    monkeypatch.setattr("app.services.v5_workspace.start_resource_sync_run", fake_start_resource_sync_run)
    monkeypatch.setattr("app.services.v5_workspace.finish_resource_sync_run", fake_finish_resource_sync_run)
    monkeypatch.setattr("app.services.v5_workspace.preferred_sync_source", lambda db, resource: None)

    import asyncio

    result = asyncio.run(sync_v5_resource(db, resource_id=resource.id, large_document_mode="knowledge_vectorize"))

    assert called["kwargs"]["kinds"] == ["docx"]
    assert called["kwargs"]["document_id"] == "doccn_hot"
    assert called["kwargs"]["document_type"] == "docx"
    assert called["kwargs"]["extract_items"] is True
    assert finished["status"] == "success"
    assert finished["items_indexed"] == 1
    assert finished["summary"]["data_layer"] == "knowledge_hot"
    assert finished["summary"]["query_path"] == "local_rag"
    assert finished["summary"]["rag_indexing"]["document_store"] == "work_events"
    assert finished["summary"]["rag_indexing"]["vector_db"] == "qdrant_vectors"
    assert finished["summary"]["rag_indexing"]["rag_index"] == "pending"
    assert finished["summary"]["document_store"]["store"] == "work_events"
    assert finished["summary"]["document_store"]["chunk_count"] == 2
    assert finished["summary"]["document_store"]["work_event_ids"] == [str(event_id)]
    assert db.committed is True
    assert resource.last_sync_at is not None
    assert result["available"] is True
    assert result["data_layer"] == "knowledge_hot"
    assert result["sync_action"] == "knowledge_vectorize"
    assert result["query_path"] == "local_rag"
    assert result["rag_indexing"]["document_store"] == "work_events"
    assert result["rag_indexing"]["vector_db"] == "qdrant_vectors"
    assert result["rag_indexing"]["rag_index"] == "pending"
    assert result["document_store"]["store"] == "work_events"
    assert result["document_store"]["chunk_count"] == 2
    assert result["document_store"]["work_event_ids"] == [str(event_id)]
    assert result["saved_count"] == 1
    assert result["work_event_ids"] == [str(event_id)]


def test_v5_hot_wiki_doc_resource_sync_resolves_content_for_local_rag(monkeypatch) -> None:
    class FakeDb:
        def __init__(self, resource, app_config):
            self.resource = resource
            self.app_config = app_config
            self.committed = False

        def get(self, model, item_id):
            if item_id == self.resource.id:
                return self.resource
            if item_id == self.app_config.id:
                return self.app_config
            return None

        def scalars(self, query):
            class ScalarResult:
                def all(self):
                    return []

            return ScalarResult()

        def commit(self):
            self.committed = True

    class ResourceStub:
        def __init__(self):
            self.id = uuid4()
            self.company_id = uuid4()
            self.platform = "feishu"
            self.app_config_id = uuid4()
            self.resource_type = "drive_file"
            self.resource_id = "wikcn_hot"
            self.resource_sub_id = None
            self.resource_name = "高频 Wiki 制度"
            self.permission_level = "company"
            self.config_json = {"document_type": "wiki"}
            self.last_sync_at = None
            self.enabled = True

    resource = ResourceStub()
    app_config = type("AppConfig", (), {"id": resource.app_config_id, "company_id": resource.company_id})()
    db = FakeDb(resource, app_config)
    event_id = uuid4()
    called = {}

    async def fake_sync_feishu_information(*args, **kwargs):
        called["kwargs"] = kwargs
        return {
            "wiki": {
                "available": True,
                "saved_count": 1,
                "work_event_ids": [str(event_id)],
                "rag_indexing": {
                    "document_store": "work_events",
                    "chunking": "queued",
                    "vector_db": "qdrant_vectors",
                    "rag_index": "pending",
                    "vector_status": "pending",
                    "vectorize": "full_chunk_or_summary_chunk",
                },
            }
        }

    sync_run = type("SyncRun", (), {"id": uuid4()})()
    finished = {}

    monkeypatch.setattr("app.services.v5_workspace.sync_feishu_information", fake_sync_feishu_information)
    monkeypatch.setattr("app.services.v5_workspace.start_resource_sync_run", lambda *args, **kwargs: sync_run)
    monkeypatch.setattr("app.services.v5_workspace.finish_resource_sync_run", lambda run, **kwargs: finished.update(kwargs))
    monkeypatch.setattr("app.services.v5_workspace.preferred_sync_source", lambda db, resource: None)

    import asyncio

    result = asyncio.run(sync_v5_resource(db, resource_id=resource.id, large_document_mode="knowledge_vectorize"))

    assert called["kwargs"]["kinds"] == ["wiki"]
    assert called["kwargs"]["document_id"] == "wikcn_hot"
    assert called["kwargs"]["document_type"] == "wiki"
    assert called["kwargs"]["sync_context"]["data_layer"] == "knowledge_hot"
    assert called["kwargs"]["sync_context"]["query_path"] == "local_rag"
    assert finished["status"] == "success"
    assert finished["items_indexed"] == 1
    assert result["available"] is True
    assert result["data_layer"] == "knowledge_hot"
    assert result["sync_action"] == "knowledge_vectorize"
    assert result["query_path"] == "local_rag"
    assert result["saved_count"] == 1
    assert result["work_event_ids"] == [str(event_id)]
    assert db.committed is True


def test_v5_resource_retry_clears_access_block_after_success(monkeypatch) -> None:
    class FakeDb:
        def __init__(self, resource, app_config):
            self.resource = resource
            self.app_config = app_config
            self.committed = False

        def get(self, model, item_id):
            if item_id == self.resource.id:
                return self.resource
            if item_id == self.app_config.id:
                return self.app_config
            return None

        def commit(self):
            self.committed = True

    class ResourceStub:
        def __init__(self):
            self.id = uuid4()
            self.company_id = uuid4()
            self.platform = "feishu"
            self.app_config_id = uuid4()
            self.resource_type = "chat"
            self.resource_id = "oc_x"
            self.resource_sub_id = None
            self.resource_name = "销售群"
            self.config_json = {"governance": {"status": "bot_not_in_chat"}}
            self.last_sync_at = None
            self.enabled = True

    resource = ResourceStub()
    app_config = type("AppConfig", (), {"id": resource.app_config_id})()
    db = FakeDb(resource, app_config)
    called = {"feishu": False}

    async def fake_sync_feishu_information(*args, **kwargs):
        called["feishu"] = True
        assert kwargs["kinds"] == ["messages"]
        assert kwargs["chat_id"] == "oc_x"
        return {"messages": {"available": True, "saved_count": 0}}

    run_id = uuid4()
    sync_run = type("SyncRun", (), {"id": run_id})()

    def fake_start_resource_sync_run(*args, **kwargs):
        assert kwargs["sync_action"] == "realtime_event"
        return sync_run

    def fake_finish_resource_sync_run(run, **kwargs):
        assert run is sync_run
        assert kwargs["status"] == "success"
        assert kwargs["error_message"] is None
        assert kwargs["summary"]["access_block_cleared"] is True
        return run

    monkeypatch.setattr("app.services.v5_workspace.sync_feishu_information", fake_sync_feishu_information)
    monkeypatch.setattr("app.services.v5_workspace.start_resource_sync_run", fake_start_resource_sync_run)
    monkeypatch.setattr("app.services.v5_workspace.finish_resource_sync_run", fake_finish_resource_sync_run)
    monkeypatch.setattr("app.services.v5_workspace.preferred_sync_source", lambda db, resource: None)

    import asyncio

    result = asyncio.run(sync_v5_resource(db, resource_id=resource.id, retry_access_blocked=True))

    assert called["feishu"] is True
    assert db.committed is True
    assert result["available"] is True
    assert result["access_block_cleared"] is True
    assert "governance" not in resource.config_json


def test_work_event_ids_from_resource_sync_result_are_deduped() -> None:
    result = {
        "mail": {"work_event_ids": ["1", "2", "2"], "saved_count": 3},
        "tasks": {"work_event_ids": ["2", "3"], "error": "partial failure"},
        "ignored": "nope",
    }

    assert _work_event_ids_from_result(result) == ["1", "2", "3"]
    assert _items_seen_from_result(result) == 3
    assert _error_message_from_result(result) == "partial failure"


def test_v5_workspace_marks_bot_not_in_chat_governance() -> None:
    resource = type("ResourceStub", (), {"config_json": {}})()
    error = "Feishu code 230002: Bot/User can NOT be out of the chat."

    assert _is_bot_not_in_chat_error(error) is True
    _mark_resource_access_blocked(
        resource,
        status="bot_not_in_chat",
        reason="机器人不在该群，飞书拒绝读取群消息。",
        error_message=error,
    )

    assert resource.config_json["governance"]["status"] == "bot_not_in_chat"
    assert resource.config_json["governance"]["error_message"] == error


def test_v5_workspace_retry_decision_ignores_existing_access_block() -> None:
    resource = type(
        "ResourceStub",
        (),
        {
            "resource_type": "chat",
            "resource_id": "oc_x",
            "resource_sub_id": None,
            "platform": "feishu",
            "enabled": True,
            "config_json": {"governance": {"status": "bot_not_in_chat"}},
        },
    )()

    blocked = sync_decision_for_resource(resource)
    retry_subject = _decision_resource(resource, retry_access_blocked=True)
    retry_decision = sync_decision_for_resource(retry_subject)

    assert blocked["status"] == "access_blocked"
    assert retry_decision["status"] == "ready"
    assert retry_decision["executable"] is True
    assert "governance" in resource.config_json
    assert "governance" not in retry_subject.config_json


def test_v5_workspace_can_clear_access_block() -> None:
    resource = type("ResourceStub", (), {"config_json": {"governance": {"status": "bot_not_in_chat"}}})()

    assert _clear_resource_access_block(resource) is True
    assert resource.config_json == {}
    assert _clear_resource_access_block(resource) is False


def test_v5_workspace_detects_calendar_authorization_error() -> None:
    error = "Feishu code 99992402: field validation failed"

    assert _is_calendar_authorization_error(error) is True
