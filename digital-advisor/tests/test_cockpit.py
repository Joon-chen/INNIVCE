from pathlib import Path
from datetime import UTC, datetime
from uuid import uuid4

from app.main import app
from app.models.entities import ExtractedItem, WorkEvent
from app.services.cockpit import build_scope
from app.services.cockpit.modules.communications import _communication_attention_level, _communication_item
from app.services.cockpit.modules.communications import _communication_next_actions
from app.services.cockpit.modules.communications import _dedupe_communication_items
from app.services.cockpit.modules.communications import _is_low_signal_event as _is_low_signal_communication_event
from app.services.cockpit.modules.decisions import _decision_attention_level, _decision_item, _decision_next_actions
from app.services.cockpit.modules.decisions import _dedupe_decision_items
from app.services.cockpit.modules.meetings import _dedupe_meeting_items, _meeting_attention_level, _meeting_item
from app.services.cockpit.modules.meetings import _meeting_next_actions
from app.services.cockpit.modules.meetings import _is_low_signal_event as _is_low_signal_meeting_event
from app.services.cockpit.modules.approvals import _approval_bucket, _approval_item, _dedupe_approval_items
from app.services.cockpit.modules.approvals import _approval_status
from app.services.cockpit.modules.projects import _dedupe_project_items, _is_low_signal_event
from app.services.cockpit.modules.projects import _project_attention_level, _project_item, _project_next_actions
from app.services.cockpit.modules.resources import _resource_health_payload, _resource_health_status, _resource_next_actions
from app.services.cockpit.modules.tasks import _dedupe_task_items, _task_attention_level, _task_item, _task_next_actions
from app.services.cockpit.modules.today import _build_today_focus_items, _today_next_actions, _today_summary
from app.services.cockpit.modules.risks import _is_open_business_risk, _risk_summary
from app.services.cockpit.registry import DEFAULT_MODULE_ORDER, MODULE_BUILDERS
from app.services.tools.providers.feishu_api import FEISHU_API_CAPABILITIES, FeishuApiRisk
from app.services.tools.router import TOOL_REGISTRY


def test_cockpit_modules_are_registered() -> None:
    assert DEFAULT_MODULE_ORDER == (
        "today-focus",
        "risks",
        "tasks",
        "approvals",
        "projects",
        "decisions",
        "communications",
        "meetings",
        "resources",
        "reports",
    )
    assert set(DEFAULT_MODULE_ORDER) == set(MODULE_BUILDERS)


def test_event_modules_are_split_by_domain() -> None:
    module_dir = Path("app/services/cockpit/modules")
    assert (module_dir / "approvals.py").exists()
    assert (module_dir / "projects.py").exists()
    assert (module_dir / "communications.py").exists()
    assert (module_dir / "meetings.py").exists()
    assert not (module_dir / "events.py").exists()


def test_cockpit_scope_supports_single_all_and_multi_company() -> None:
    company_id = uuid4()
    other_company_id = uuid4()

    single = build_scope(company_id=company_id)
    all_companies = build_scope(all_companies=True)
    multi = build_scope(company_ids=[company_id, other_company_id])

    assert single.effective_company_ids == (company_id,)
    assert all_companies.all_companies is True
    assert multi.effective_company_ids == (company_id, other_company_id)


def test_cockpit_layer_has_no_feishu_or_llm_dependencies() -> None:
    offenders: list[str] = []
    forbidden = [
        "app.services.feishu",
        "app.services.feishu.client",
        "app.services.ai",
        "httpx",
        "lark_oapi",
        ".add(",
        ".commit(",
        ".delete(",
    ]
    for path in Path("app/services/cockpit").rglob("*.py"):
        text = path.read_text()
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path}:{marker}")

    assert offenders == []


def test_console_v5_api_paths_are_registered() -> None:
    console_paths = {
        "/api/v5/agent/settings",
        "/api/v5/agent/trace-preview",
        "/api/v5/agent/traces",
        "/api/v5/administration/company-settings",
        "/api/v5/administration/departments",
        "/api/v5/administration/permissions",
        "/api/v5/administration/resource-permissions",
        "/api/v5/administration/roles",
        "/api/v5/administration/teams",
        "/api/v5/administration/users",
        "/api/v5/bootstrap/foundation",
        "/api/v5/os/overview",
        "/api/v5/resources",
        "/api/v5/resources/{resource_id}/access-decision",
        "/api/v5/resources/{resource_id}/clear-access-block",
        "/api/v5/resources/{resource_id}/retry-access-block",
        "/api/v5/resources/{resource_id}/sync",
        "/api/v5/resources/company-overview",
        "/api/v5/resources/monitoring",
        "/api/v5/resources/sync",
        "/api/v5/resources/sync-policy",
        "/api/v5/resources/sync-preview",
        "/api/v5/resources/sync-runs",
        "/api/v5/resources/sync-status",
        "/api/v5/resources/sync-strategy",
        "/api/v5/system/logs",
        "/api/v5/tools",
        "/api/v5/tools/{tool_name}",
        "/api/v5/tools/{tool_name}/execute",
        "/api/v5/tools/batch",
        "/api/v5/tools/executions",
    }
    registered_paths = {route.path for route in app.routes}

    assert sorted(console_paths - registered_paths) == []


def test_cockpit_resources_use_resource_sync_runs_not_legacy_sync_runs() -> None:
    resources_module = Path("app/services/cockpit/modules/resources.py").read_text()
    orchestrator = Path("app/services/cockpit/orchestrator.py").read_text()
    today_module = Path("app/services/cockpit/modules/today.py").read_text()

    assert "ResourceSyncRun" in resources_module
    assert "ResourceSyncRun" in orchestrator
    assert "ResourceSyncRun" not in today_module
    assert "from app.models.entities import Resource, ResourceSyncRun" in resources_module
    assert "from app.models.entities import Resource, SyncRun" not in resources_module
    assert "from app.models.entities import ExtractedItem, WorkEvent" in today_module


def test_cockpit_language_is_owner_cockpit_not_it_console() -> None:
    today_module = Path("app/services/cockpit/modules/today.py").read_text()
    resources_module = Path("app/services/cockpit/modules/resources.py").read_text()
    risks_module = Path("app/services/cockpit/modules/risks.py").read_text()
    console_app = Path("app/static/console/app.js").read_text()

    assert "最近资源同步" not in today_module
    assert 'name="数据覆盖"' in resources_module
    assert "数据盲区" in risks_module
    assert 'eyebrow: "数据覆盖"' in console_app
    assert "counts.vector_indexed" not in console_app
    assert "counts.vector_pending" not in console_app
    assert "counts.resource_sync_runs" not in console_app
    assert '["今日事件", metric("today-focus", "today_events")]' in console_app
    assert "function riskCardTitle" in console_app
    assert "function resourceCoverageTitle" in console_app
    assert "function renderGovernanceActions" in console_app
    assert "function renderOverviewGovernanceActions" in console_app
    assert "function selectGovernanceAction" in console_app
    assert "function governanceActionGuide" in console_app
    assert "function applyGovernanceActionPreset" in console_app
    assert "function retrySelectedResourceBlock" in console_app
    assert "retry-access-block" in console_app
    assert "resourceGovernanceActionsTable" in console_app
    assert "governanceActionGuide" in console_app
    assert "feishu_doc_fetch" in console_app
    assert "feishu_drive_search" in console_app
    assert "feishu_drive_file_list" in console_app
    assert "feishu_wiki_space_list" in console_app
    assert "feishu_wiki_node_list" in console_app
    assert "feishu_im_chat_search" in console_app
    assert "feishu_im_message_list" in console_app
    assert "feishu_mail_folder_list" in console_app
    assert "feishu_mail_message_get" in console_app
    assert "feishu_vc_meeting_search" in console_app
    assert "feishu_bitable_view_set_filter" in console_app
    assert "feishu_bitable_view_set_group" in console_app
    assert "feishu_bitable_view_set_sort" in console_app
    assert "feishu_bitable_view_get_card" in console_app
    assert "feishu_bitable_view_set_card" in console_app
    assert "feishu_bitable_view_get_timebar" in console_app
    assert "feishu_bitable_view_set_timebar" in console_app
    assert "feishu_bitable_view_get_visible_fields" in console_app
    assert "feishu_bitable_view_set_visible_fields" in console_app
    assert "archive_tasklist: false" in console_app
    assert "feishu_bitable_record_upload_attachment" in console_app
    assert "feishu_bitable_record_remove_attachment" in console_app
    assert "feishu_task_section_create" in console_app
    assert "feishu_task_section_delete" in console_app
    assert "feishu_task_upload_attachment" in console_app
    assert "feishu_calendar_create_event" in console_app
    assert "feishu_task_delete" in console_app
    assert "feishu_tasklist_delete" in console_app
    assert '["document_store_summary", "文档库"]' in console_app
    assert '["rag_indexing_summary", "RAG 入队"]' in console_app
    assert "latest_document_store_summary: item.latest_run?.document_store_summary" in console_app
    assert '["latest_document_store_summary", "最近文档库"]' in console_app
    assert "latest_rag_indexing_summary: item.latest_run?.rag_indexing_summary" in console_app
    assert '["latest_rag_indexing_summary", "最近 RAG"]' in console_app
    assert "个数据盲区" in console_app
    assert "data_blindspots" in console_app
    assert "sync_errors" in console_app
    assert "auto_sync_pending" in console_app
    assert "overviewGovernanceActions" in console_app
    assert "暂无需要你关注的关键群接入建议" in console_app
    assert '["attention_label", "分层"]' in console_app
    assert "must_handle: \"必须处理\"" in console_app
    assert "system_managed: \"系统自动处理\"" in console_app
    assert "pending_for_me: \"待我审批\"" in console_app
    assert "sync_only: \"仅同步记录\"" in console_app
    assert "unknown: \"未知\"" in console_app


def test_console_tool_templates_cover_all_feishu_write_capabilities() -> None:
    console_app = Path("app/static/console/app.js").read_text()
    write_tools = {
        tool_name
        for tool_name, capability in FEISHU_API_CAPABILITIES.items()
        if capability.risk == FeishuApiRisk.WRITE
    }

    missing = sorted(tool_name for tool_name in write_tools if f"{tool_name}:" not in console_app)

    assert missing == []
    console_app = Path("app/static/console/app.js").read_text()

    assert 'business_tools: new Set(items.map((item) => item.business_tool).filter(Boolean)).size' in console_app
    assert '["business_tool", "业务工具"]' in console_app
    assert "business_tool_capabilities: item.business_tool_capabilities || []" in console_app


def test_console_system_logs_can_filter_gateway_reason() -> None:
    console_html = Path("app/static/console/index.html").read_text()
    console_app = Path("app/static/console/app.js").read_text()
    oauth_helper = Path("app/services/feishu_oauth_helpers.py").read_text()

    assert "<title>数字参谋驾驶舱</title>" in console_html
    assert "<strong>老司机</strong>" in console_html
    assert "<span>企业数字参谋</span>" in console_html
    assert "数字顾问驾驶舱" not in console_html
    assert "返回数字参谋后台" in oauth_helper
    assert "数字顾问后台" not in oauth_helper
    assert 'id="systemLogReason"' in console_html
    assert 'id="systemLogUsedAgentRuntime"' in console_html
    assert 'id="systemLogFinalAnswerOwner"' in console_html
    assert 'id="systemLogRoutePath"' in console_html
    assert '["agent_title", "员工 Agent"]' in console_app
    assert '["agent_status", "状态"]' in console_app
    assert '["agent_entrypoint", "入口"]' in console_app
    assert '["agent_activated_at", "激活时间"]' in console_app
    assert '["agent_first_chat_id", "首个会话"]' in console_app
    assert '["global_shared_tool_summary", "共享 Tool"]' in console_app
    assert '["user_identity_summary", "授权状态"]' in console_app
    assert 'params.set("reason", value("systemLogReason"))' in console_app
    assert 'params.set("used_agent_runtime", value("systemLogUsedAgentRuntime"))' in console_app
    assert 'params.set("final_answer_owner", value("systemLogFinalAnswerOwner"))' in console_app
    assert 'params.set("route_path", value("systemLogRoutePath"))' in console_app
    assert '["reason", "原因"]' in console_app
    assert '["used_agent_runtime", "Agent Runtime"]' in console_app
    assert '["final_answer_owner", "最终回复"]' in console_app
    assert '["route_path", "路由"]' in console_app
    assert '["route_label", "能力路径"]' in console_app
    assert '["reply_mode_label", "回复模式"]' in console_app
    assert '["data_access_scope", "数据边界"]' in console_app
    assert '["personal_owner_open_id", "资源所有者"]' in console_app
    assert '["cross_user_data_allowed", "跨人数据"]' in console_app
    assert '["agent_runtime_step_count", "Agent步数"]' in console_app
    assert "rowAction: showSystemLogDetail" in console_app
    assert "function showSystemLogDetail(item)" in console_app
    assert "tool_steps: item.agent_runtime_tool_steps || []" in console_app
    assert "workflow_steps: item.agent_runtime_workflow_steps || []" in console_app
    assert 'data-action="view-gateway-card-issues"' in console_html
    assert 'data-action="view-agent-runtime-gateway"' in console_html
    assert 'id="replyModeBoard"' in console_html
    assert 'id="agentPreviewQuestion"' in console_html
    assert 'id="agentPreviewOpenId"' in console_html
    assert 'id="agentPreviewResult"' in console_html
    assert 'data-action="preview-agent-fast"' in console_html
    assert 'data-action="preview-agent-normal"' in console_html
    assert 'data-action="preview-agent-thinking"' in console_html
    assert 'data-action="preview-agent-reply-mode"' in console_html
    assert "20260615-v5-cli-identity-match" in console_html
    assert "function renderReplyModeBoard" in console_app
    assert '"preview-agent-fast": () => previewAgentReplyModePreset("fast")' in console_app
    assert '"preview-agent-normal": () => previewAgentReplyModePreset("normal")' in console_app
    assert '"preview-agent-thinking": () => previewAgentReplyModePreset("thinking")' in console_app
    assert '"preview-agent-reply-mode": () => previewAgentReplyMode()' in console_app
    assert 'api("/api/v5/agent/trace-preview"' in console_app
    assert "function previewAgentReplyModePreset(mode)" in console_app
    assert "function previewAgentReplyMode()" in console_app
    assert "thinking_preview: trace.thinking_preview" in console_app
    assert '"view-gateway-card-issues": viewGatewayCardIssues' in console_app
    assert '"view-agent-runtime-gateway": viewAgentRuntimeGatewayLogs' in console_app
    assert 'setValue("systemLogStatus", "handled")' in console_app
    assert 'setValue("systemLogAction", "gateway.feishu.message")' in console_app
    assert 'setValue("systemLogReason", "unhandled_card_action")' in console_app
    assert 'setValue("systemLogUsedAgentRuntime", "true")' in console_app
    assert 'setValue("systemLogFinalAnswerOwner", "agent_runtime")' in console_app


def test_console_owner_center_matches_v5_xmind_structure() -> None:
    console_html = Path("app/static/console/index.html").read_text()

    assert "经营智能中心" in console_html
    assert 'class="primary-nav"' in console_html
    assert 'data-view="entrypoints"' in console_html
    assert "Swagger" not in console_html
    assert "运行状态" in console_html
    assert "执行通道" in console_html
    assert "预演" in console_html
    assert "运行记录" in console_html
    assert "员工 Agent 工作台" in console_html
    assert "机器人权限</h2>" not in console_html
    assert 'id="employeeAgentStats"' in console_html
    assert 'id="employeeAgentRuntimeBoard"' in console_html
    assert 'id="employeeAgentToolBoard"' in console_html
    assert 'id="employeeAgentAuthorizationBoard"' in console_html
    assert 'id="employeeAgentEvidenceBoard"' in console_html
    assert 'id="employeeAgentReplyModeBoard"' in console_html
    assert "系统健康" not in console_html
    assert "系统日志概览" not in console_html
    assert "Dry-run" not in console_html
    assert "Open ID" not in console_html
    assert 'class="owner-command-center"' in console_html
    assert 'data-action="validate-feishu-app"' in console_html
    assert "创建或更新并校验" in console_html


def test_console_employee_agent_workbench_surfaces_identity_boundaries() -> None:
    console_html = Path("app/static/console/index.html").read_text()
    console_app = Path("app/static/console/app.js").read_text()
    console_css = Path("app/static/console/styles.css").read_text()

    assert 'renderChips("employeeAgentStats", employeeAgentStats(items))' in console_app
    assert "renderEmployeeAgentRuntimeBoard(items)" in console_app
    assert "renderEmployeeAgentToolBoard(items)" in console_app
    assert "renderEmployeeAgentAuthorizationBoard(items)" in console_app
    assert "renderEmployeeAgentEvidenceBoard(items)" in console_app
    assert 'renderReplyModeBoard("employeeAgentReplyModeBoard", items[0]?.reply_modes' in console_app
    assert "function employeeAgentStats(items)" in console_app
    assert "function renderEmployeeAgentRuntimeBoard(items)" in console_app
    assert "function renderEmployeeAgentToolBoard(items)" in console_app
    assert "function renderEmployeeAgentAuthorizationBoard(items)" in console_app
    assert "function renderEmployeeAgentEvidenceBoard(items)" in console_app
    assert '["agent_title", "员工 Agent"]' in console_app
    assert '["agent_launch_summary", "入口就绪"]' in console_app
    assert '["global_shared_tool_summary", "共享 Tool"]' in console_app
    assert '["reply_mode_summary", "回复模式"]' in console_app
    assert '["enterprise_identity_boundary", "企业边界"]' in console_app
    assert '["user_identity_boundary", "用户边界"]' in console_app
    assert '["latest_tool_execution_summary", "最近 Tool"]' in console_app
    assert '["latest_gateway_message_summary", "最近消息入口"]' in console_app
    assert '["latest_gateway_used_agent_runtime", "Agent Runtime"]' in console_app
    assert '["latest_gateway_final_answer_owner", "消息最终回复"]' in console_app
    assert '["latest_gateway_tool_summary", "消息 Tool"]' in console_app
    assert '["latest_gateway_authorization_summary", "消息授权"]' in console_app
    assert '["latest_tool_enterprise_identity_boundary", "最近企业边界"]' in console_app
    assert '["latest_tool_user_identity_boundary", "最近用户边界"]' in console_app
    assert '["latest_tool_cli_profile", "CLI Profile"]' in console_app
    assert '["latest_tool_final_answer_owner", "最终回复"]' in console_app
    assert '["latest_tool_cannot_escalate_original_permissions", "不提权"]' in console_app
    assert '["pending_user_resource_count", "待授权"]' in console_app
    assert '["agent_next_action", "下一步"]' in console_app
    assert "authorizationActionSummary" in console_app
    assert "Owner 与员工均由资源所有者本人完成统一授权；后台只展示授权状态" in console_app
    assert "applyConsoleUrlParams" in console_app
    assert 'params.get("auth") !== "feishu_cli"' in console_app
    assert "这是本地 CLI 调试入口" in console_app
    assert "员工正式授权请从大飞哥机器人卡片进入飞书 OAuth" in console_app
    assert "/api/user-identity/oauth/feishu/cli/start" in console_app
    assert "exchange-feishu-user-oauth" not in console_app
    assert "feishuOAuthCode" not in console_html
    assert "latestGateway" in console_app
    assert "消息入口证据" in console_app
    assert "入口状态" in console_app
    assert "agent_activation_source" in console_app
    assert "Gateway链路" in console_app
    assert "消息回复模式" in console_app
    assert "消息 Tool步骤" in console_app
    assert "消息授权" in console_app
    assert "资源所有者：" in console_app
    assert "latest?.enterprise_identity_boundary" in console_app
    assert "latest?.user_identity_boundary" in console_app
    assert 'id="dataLayerBoard"' in console_html
    assert 'id="toolFamilyBoard"' in console_html
    assert 'id="entrypointBoard"' in console_html
    assert "知识库模块稍后接入" not in console_html
    assert 'data-action="load-knowledge"' in console_html
    assert 'data-action="discover-knowledge-resources"' in console_html
    assert 'data-action="preview-knowledge-sync"' in console_html
    assert 'id="knowledgeStats"' in console_html
    assert 'id="knowledgePolicyBoard"' in console_html
    assert 'id="knowledgeResourcesTable"' in console_html
    assert 'id="knowledgeEventsTable"' in console_html
    assert 'id="knowledgeSyncPreviewTable"' in console_html
    assert '"load-knowledge": loadKnowledge' in console_app
    assert '"discover-knowledge-resources": discoverKnowledgeResources' in console_app
    assert '"preview-knowledge-sync": previewKnowledgeSync' in console_app
    assert 'safeLoad("/api/v5/resources/sync-strategy")' in console_app
    assert 'safeLoad(`/api/v5/resources/sync-status${companyQuery()}`)' in console_app
    assert 'safeLoad(`/api/v5/workspace/events${companyQuery("limit=80")}`)' in console_app
    assert 'kinds: ["drive", "wiki"]' in console_app
    assert 'api("/api/v5/resources/sync-preview"' in console_app
    assert "function renderKnowledgePolicyBoard" in console_app
    assert "function isKnowledgeResource" in console_app
    assert "function knowledgeResourceLevel" in console_app
    assert "Operational Data" in console_app
    assert "Knowledge Data" in console_app
    assert "Memory" in console_app
    assert "WorkEvent" in console_app
    for tool_name in (
        "ApprovalTool",
        "KnowledgeTool",
        "BitableTool",
        "ChatTool",
        "CalendarTool",
        "MeetingTool",
        "ReportTool",
        "AutomationTool",
        "PeopleTool",
    ):
        assert tool_name in console_app
    assert "大飞哥机器人" in console_app
    assert "管理后台" in console_app
    assert "iOS App" in console_app
    assert 'entrypoints: ["交互入口"' in console_app
    assert '"load-entrypoints": loadEntrypoints' in console_app
    assert '"validate-feishu-app": validateFeishuAppConfig' in console_app
    assert "async function validateFeishuAppConfig" in console_app
    assert "/tenant-access-token" in console_app
    assert "result.feishu_app?.id" in console_app
    assert 'safeLoad(`/api/v5/entrypoints/status${companyQuery()}`)' in console_app
    assert 'id="entrypointStatusPills"' in console_html
    assert 'id="entrypointEventsTable"' in console_html
    assert 'id="entrypointSourceBoard"' in console_html
    assert '["Agent链路", gatewayRuntime.agent_runtime_messages ?? 0]' in console_app
    assert '["Tool异常", gatewayRuntime.tool_error_count ?? 0]' in console_app
    assert '["最近异常", gatewayRuntime.latest_error ? entrypointGatewayErrorSummary(gatewayRuntime.latest_error) : "无"]' in console_app
    assert '["已校验 App", bot.validated_app_count ?? 0]' in console_app
    assert '["credential_status", "凭证状态"]' in console_app
    assert "function entrypointGatewayErrorSummary" in console_app
    assert "function entrypointDetail" in console_app
    assert "CLI 已识别 App ID" in console_app
    assert "仍需 App Secret 入库并校验 tenant token" in console_app
    assert '["Bot身份", identityStatus.bot_identity?.message || "-"]' in console_app
    assert '["User身份", identityStatus.user_identity?.message || "-"]' in console_app
    assert 'userIdentityMessage.includes("needs refresh") || userIdentityMessage.includes("missing")' in console_app
    assert "User token 缺失或需要刷新" in console_app
    assert "数据源/执行源" in console_app
    assert 'data-action="complete-feishu-cli-user-auth"' in console_html
    assert 'data-action="probe-user-identity-tools"' in console_html
    assert 'id="feishuCliDeviceCode"' in console_html
    assert 'id="feishuCliAuthBoard"' in console_html
    assert '"/api/user-identity/oauth/feishu/cli/start"' in console_app
    assert '"/api/user-identity/oauth/feishu/cli/complete"' in console_app
    assert '"probe-user-identity-tools": probeUserIdentityTools' in console_app
    assert "function probeUserIdentityTools" in console_app
    assert "function userIdentityProbeSummary" in console_app
    assert '["feishu_approval_task_query", "有没有需要我处理的审批", "审批实时待办"]' in console_app
    assert '["calendar_qa", "今天我的日程有哪些", "日程问答"]' in console_app
    assert '["personal_tasks", "我的待办事项有哪些", "本人待办"]' in console_app
    assert "function renderFeishuCliAuthBoard" in console_app
    assert "result.identity_open_id" in console_app
    assert "result.identity_match" in console_app
    assert "identityBoundary.agent_model" in console_app
    assert "identityBoundary.tool_model" in console_app
    assert "identityBoundary.enterprise_resource_boundary" in console_app
    assert "identityBoundary.user_resource_boundary" in console_app
    assert "identityBoundary.tool_data_boundary_rule" in console_app
    assert "用户级资源受 User Identity 和资源所有者授权" in console_app
    assert "identityBoundary.digital_advisor_permission_policy" in console_app
    assert "每个飞书用户一个专属 Agent" in console_app
    assert "9 个业务 Tool 全局共享" in console_app
    assert "不能突破飞书 App 或用户原始授权范围" in console_app
    assert "Tool 决定数据源/执行源" in console_app
    assert "Tool 返回结构化结果" in console_app
    assert "Agent Runtime 生成最终回复" in console_app
    assert "所有员工 Agent 可调用" in console_app
    assert "未授权前不读取个人飞书、其他邮箱、个人钉钉或个人微信数据" in console_app
    assert "Fast（无企业数据） / Normal（实时单 Tool） / Thinking（WorkEvent、Knowledge、Memory、多 Tool 分析）" in console_app
    assert "Message Gateway -> Agent Runtime -> Tool Router -> Tool -> 数据源/执行源" in console_app
    assert "独立飞书 App、独立 CLI Profile、统一 V5 数据层" in console_html
    assert ".owner-command-center" in console_css
    assert ".architecture-board" in console_css


def test_console_company_space_reuses_v5_data_apis() -> None:
    console_html = Path("app/static/console/index.html").read_text()
    console_app = Path("app/static/console/app.js").read_text()
    console_css = Path("app/static/console/styles.css").read_text()

    assert 'data-view="company-space"' in console_html
    assert "公司经营空间" in console_html
    assert "进入公司空间" in console_html
    assert '<button class="ghost" data-view="company-space">查看数据覆盖</button>' in console_html
    assert 'id="companySpaceDataLayers"' in console_html
    assert 'id="companySpaceAgentState"' in console_html
    assert 'data-action="discover-company-space-resources"' in console_html
    assert 'id="companySpaceActionResult"' in console_html
    assert "上线编排" in console_html
    assert 'data-action="load-resource-launch-plan"' in console_html
    assert 'data-action="discover-launch-local-resources"' in console_html
    assert 'data-action="preview-launch-sync"' in console_html
    assert 'data-action="sync-launch-pending"' in console_html
    assert 'id="resourceLaunchSteps"' in console_html
    assert 'id="resourceLaunchPreviewTable"' in console_html
    assert 'id="companySpaceEventsTable"' in console_html
    assert 'id="companySpaceResourcesTable"' in console_html
    assert '"company-space": loadCompanySpace' in console_app
    assert '"load-company-space": loadCompanySpace' in console_app
    assert '"load-resource-launch-plan": loadResourceLaunchPlan' in console_app
    assert '"discover-company-space-resources": discoverCompanySpaceResources' in console_app
    assert '"discover-launch-local-resources": discoverLaunchLocalResources' in console_app
    assert '"preview-launch-sync": previewLaunchSync' in console_app
    assert '"sync-launch-pending": syncLaunchPending' in console_app
    assert 'ensureDefaultAppConfigId("companySpaceActionResult")' in console_app
    assert 'ensureDefaultAppConfigId("resourceLaunchResult")' in console_app
    assert 'kinds: ["local"]' in console_app
    assert 'api(`/api/feishu/apps/${appConfigId}/resources/discover`' in console_app
    assert 'api("/api/v5/resources/sync-preview"' in console_app
    assert 'api("/api/v5/resources/sync"' in console_app
    assert 'if (result) showResult("resourceLaunchResult", result)' in console_app
    assert "if (!state.selectedCompanyId && effectiveCompanyId)" in console_app
    assert 'safeLoad(`/api/v5/resources/company-overview${query}`)' in console_app
    assert 'safeLoad(`/api/v5/resources/sync-status${query}`)' in console_app
    assert 'safeLoad(`/api/v5/workspace/events${limitQuery}`)' in console_app
    assert "Agent Runtime 只通过 Tool Router 调用工具" in console_app
    assert "当前公司暂无资源，请先执行资源发现" in console_app
    assert ".company-space-hero" in console_css
    assert ".company-space-grid" in console_css
    assert ".result.compact" in console_css
    assert ".panel-subtitle" in console_css


def test_console_item_type_labels_are_domain_specific() -> None:
    console_app = Path("app/static/console/app.js").read_text()
    console_css = Path("app/static/console/styles.css").read_text()

    assert "botEntrypoint.ai_mode_enabled" in console_app
    assert "大飞哥 AI 兜底已开" in console_app
    assert "大飞哥 AI 兜底未开" in console_app
    assert 'const databaseUnavailable = !os || os?.status?.database === "unavailable"' in console_app
    assert "if (operatingState?.databaseUnavailable)" in console_app
    assert "if (state.databaseUnavailable && state.activeView !== \"overview\")" in console_app
    assert "if (!databaseUnavailable)" in console_app
    assert "if (state.databaseUnavailable) return;" in console_app
    assert "state.databaseUnavailable = databaseUnavailable" in console_app
    assert "function renderCompanyUnavailable()" in console_app
    assert "renderCompanyUnavailable()" in console_app
    assert "select.disabled = false;" in console_app
    assert "select.disabled = true;" in console_app
    assert 'select.innerHTML = `<option value="">数据库未连接</option>`;' in console_app
    assert 'state.databaseUnavailable && !path.startsWith("/api/v5/os/overview")' in console_app
    assert "API load skipped while database is unavailable" in console_app
    assert 'showResult(target, "数据库未连接，操作已暂停。")' in console_app
    assert "数据库未连接，首屏降级可读" in console_app
    assert "const readiness = os?.release_readiness || {}" in console_app
    assert "const cliCheck = readinessChecks.feishu_cli || {}" in console_app
    assert "appCheck.required_action" in console_app
    assert "prefillQuickSetupFromOverview(os)" in console_app
    assert "checks.feishu_cli?.identity_status?.app_resolved?.message" in console_app
    assert "setDefaultInputValue(\"appId\", appId)" in console_app
    assert "setDefaultInputValue(\"appCliProfile\", \"v5-local-prod\")" in console_app
    assert "if (el && !el.value && value) el.value = value" in console_app
    assert "const result = await safeAction(\"quickSetupResult\"" in console_app
    assert "await loadDashboardDefaults()" in console_app
    assert "await loadCurrentView()" in console_app
    assert "上线检查" in console_app
    assert "飞书 CLI" in console_app
    assert "等待 CLI 自检" in console_app
    assert "overflow-wrap: anywhere;" in console_css
    assert "核心入口与职责边界已就绪" in console_app
    assert "降级可读，阻塞项" in console_app
    assert "实时 Agent 操作必须走 Tool Router -> Tool -> MCP -> CLI" in console_app
    assert "Feishu MCP 只负责工具调度，不直接执行动作" in console_app
    assert "MCP是否直接执行动作" in console_app
    assert "project_risk: \"客户/交付风险\"" in console_app
    assert "rd_progress: \"研发测试进展\"" in console_app
    assert "project_record: \"普通项目记录\"" in console_app
    assert "owner_reply: \"需要你回应\"" in console_app
    assert "business_attention: \"需要关注\"" in console_app
    assert "communication_record: \"背景记录\"" in console_app
    assert "meeting_decision: \"需要决策\"" in console_app
    assert "meeting_follow_up: \"会后跟进\"" in console_app
    assert "meeting_record: \"背景日程\"" in console_app
    assert "owner_final_decision: \"需要老板拍板\"" in console_app
    assert "evidence_needed: \"需要补充依据\"" in console_app
    assert "decision_record: \"已沉淀记录\"" in console_app
    assert "auto_sync_healthy: \"自动同步正常\"" in console_app
    assert "auto_sync_pending: \"等待自动同步\"" in console_app
    assert "sync_errors: \"同步异常\"" in console_app
    assert "owner_decision: \"需要你决策\"" in console_app
    assert "assignee_action: \"需要负责人推进\"" in console_app
    assert "system_record: \"系统记录\"" in console_app
    assert '["title", "审批"]' in console_app
    assert '["title", "待办"]' in console_app
    assert '["title", "决策"]' in console_app
    assert '["title", "项目动态"]' in console_app
    assert '["title", "沟通线索"]' in console_app
    assert '["title", "会议日程"]' in console_app
    assert '["subtitle", "影响"]' in console_app
    assert '["owner", "建议责任人"]' in console_app
    assert '["description", "处理建议"]' in console_app


def test_console_today_focus_uses_existing_cockpit_loader() -> None:
    console_app = Path("app/static/console/app.js").read_text()

    assert 'loadCockpitModule("today-focus")' in console_app
    assert "loadModule(" not in console_app


def test_console_defaults_to_all_companies_for_owner_cockpit() -> None:
    console_app = Path("app/static/console/app.js").read_text()

    assert 'state.selectedCompanyId = "";' in console_app
    assert "state.selectedCompanyId = items[0].id" not in console_app


def test_console_feishu_oauth_autofills_default_app_config() -> None:
    console_app = Path("app/static/console/app.js").read_text()

    assert "async function ensureDefaultAppConfigId" in console_app
    assert "async function resolveDefaultAppConfigId" in console_app
    assert "firstAppConfigId(v5Resources?.items || [])" in console_app
    assert 'id="appCliProfile"' in Path("app/static/console/index.html").read_text()
    assert 'settings: { cli_profile: value("appCliProfile") || null }' in console_app
    assert '"open-feishu-user-oauth": openFeishuUserOAuth' in console_app
    assert "const appConfigId = await ensureDefaultAppConfigId();" in console_app
    assert "未找到可用的飞书应用配置" in console_app


def test_console_static_assets_are_versioned() -> None:
    console_html = Path("app/static/console/index.html").read_text()

    assert "/console/assets/app.js?v=" in console_html


def test_feishu_cli_user_auth_page_is_employee_facing() -> None:
    route_text = Path("app/api/routes/console_feishu_cli_routes.py").read_text()
    page_text = Path("app/static/user_auth/feishu_cli.html").read_text()

    assert '@router.get("/user-auth/feishu-cli"' in route_text
    assert "大飞哥个人能力授权" in page_text
    assert "/api/user-identity/oauth/feishu/cli/start" in page_text
    assert "/api/user-identity/oauth/feishu/cli/complete" in page_text
    assert "company_id" in page_text
    assert "open_id" in page_text
    assert "管理后台" not in page_text


def test_console_tools_exposes_batch_policy_controls() -> None:
    console_html = Path("app/static/console/index.html").read_text()
    console_app = Path("app/static/console/app.js").read_text()

    assert 'id="toolBatchScope"' in console_html
    assert 'id="toolBatchEnabled"' in console_html
    assert 'id="toolBatchProvider"' in console_html
    assert 'data-action="preview-tool-batch"' in console_html
    assert 'data-action="apply-tool-batch"' in console_html
    assert '"preview-tool-batch": () => updateToolBatch(true)' in console_app
    assert '"apply-tool-batch": () => updateToolBatch(false)' in console_app
    assert "async function updateToolBatch" in console_app
    assert "/api/v5/tools/batch?company_id=" in console_app


def test_console_tool_execution_logs_surface_runtime_evidence() -> None:
    console_app = Path("app/static/console/app.js").read_text()

    assert '["business_tool", "业务 Tool"]' in console_app
    assert '["execution_source", "执行源"]' in console_app
    assert '["data_source", "数据源"]' in console_app
    assert '["cli_profile", "CLI Profile"]' in console_app
    assert '["cli_profile_source", "Profile 来源"]' in console_app
    assert '["source_chain", "来源链路"]' in console_app
    assert '["execution_chain", "执行链路"]' in console_app
    assert '["tool_returns_structured_result", "结构化结果"]' in console_app
    assert '["final_answer_owner", "最终回复"]' in console_app
    assert '["data_permission_model", "数据权限"]' in console_app
    assert '["enterprise_identity_boundary", "企业边界"]' in console_app
    assert '["company_scope", "公司范围"]' in console_app
    assert '["role_scope", "角色范围"]' in console_app
    assert '["user_identity_boundary", "用户边界"]' in console_app
    assert '["cannot_escalate_original_permissions", "不提权"]' in console_app


def test_console_settings_exposes_governance_action_table() -> None:
    console_html = Path("app/static/console/index.html").read_text()
    console_app = Path("app/static/console/app.js").read_text()

    assert 'id="resourceGovernanceActionsTable"' in console_html
    assert 'id="governanceActionGuide"' in console_html
    assert 'data-action="retry-selected-resource-block"' in console_html
    assert 'data-action="mark-selected-business-group"' in console_html
    assert 'data-action="mark-selected-owner-confirmed"' in console_html
    assert 'data-action="mark-selected-do-not-connect"' in console_html
    assert 'data-action="ignore-selected-access-suggestion"' in console_html
    assert 'data-action="reset-selected-access-decision"' in console_html
    assert "<th>处理动作</th>" in console_app
    assert "<th>接入建议</th>" in console_app
    assert "<th>通知对象</th>" in console_app
    assert "<th>业务影响</th>" in console_app
    assert "<th>下一步</th>" in console_app
    assert 'data-governance-index="${index}"' in console_app
    assert "selectGovernanceAction(rows[Number(button.dataset.governanceIndex)])" in console_app
    assert "重试并恢复" in console_app
    assert "setSelectedResourceAccessDecision" in console_app
    assert "access-decision" in console_app
    assert "只有“建议接入”的业务群才通知负责人" in console_app
    assert "执行自动发现，系统会登记可同步资源" in console_app


def test_console_overview_surfaces_only_high_value_group_access_actions() -> None:
    console_html = Path("app/static/console/index.html").read_text()
    console_app = Path("app/static/console/app.js").read_text()
    console_css = Path("app/static/console/styles.css").read_text()

    assert 'id="overviewGovernanceActions"' in console_html
    assert "overview-action" in console_css
    assert 'action.action_code === "invite_bot_to_chats"' in console_app
    assert '["建议接入", "已确认接入"].includes(label)' in console_app
    assert "selectGovernanceAction(actions[Number(button.dataset.overviewGovernanceIndex)])" in console_app


def test_approval_bucket_separates_personal_pending_from_sync_record() -> None:
    assert _approval_bucket({"task_id": "task_1"}, "pending") == "待我审批"
    assert _approval_bucket({"instance_code": "inst_1"}, "pending") == "仅同步记录"
    assert _approval_bucket({"instance_code": "inst_2"}, "approved") == "已完成"


def test_approval_item_explains_sync_only_pending_instance() -> None:
    event = WorkEvent(
        id=uuid4(),
        company_id=uuid4(),
        source="feishu",
        event_type="feishu.approvals.snapshot",
        title="付款审批 - PAY-001",
        content_text="状态 pending",
        occurred_at=datetime.now(UTC),
        payload={"item": {"status": "pending", "instance_code": "PAY-001", "approval_name": "付款审批"}},
    )

    item = _approval_item(event)

    assert item.attention_label == "仅同步记录"
    assert item.status == "pending"
    assert "未确认是待你本人审批" in str(item.description)


def test_approval_module_uses_strict_approval_event_scope() -> None:
    source = Path("app/services/cockpit/modules/approvals.py").read_text()

    assert "text_match_conditions" not in source
    assert 'WorkEvent.content_text.ilike("%采购%")' not in source
    assert 'WorkEvent.event_type.ilike("%approval%")' in source


def test_approval_status_ignores_nested_status_objects() -> None:
    assert _approval_status({"status": {"is_deleted": False}, "instance_status": "approved"}) == "approved"


def test_approval_module_deduplicates_repeated_sync_snapshots() -> None:
    now = datetime.now(UTC)
    first = WorkEvent(
        id=uuid4(),
        company_id=uuid4(),
        source="feishu",
        event_type="feishu.approvals.snapshot",
        title="付款审批 - PAY-001",
        content_text="",
        occurred_at=now,
        payload={"item": {"instance_code": "PAY-001", "status": "pending", "approval_name": "付款审批"}},
    )
    repeated = WorkEvent(
        id=uuid4(),
        company_id=first.company_id,
        source="feishu",
        event_type="feishu.approvals.snapshot",
        title="付款审批 - PAY-001",
        content_text="",
        occurred_at=now,
        payload={"item": {"instance_code": "PAY-001", "status": "pending", "approval_name": "付款审批"}},
    )

    items = _dedupe_approval_items([_approval_item(first), _approval_item(repeated)])

    assert len(items) == 1


def test_task_attention_levels_match_owner_cockpit() -> None:
    company_id = uuid4()
    decision_task = ExtractedItem(
        id=uuid4(),
        company_id=company_id,
        item_type="task",
        title="确认付款安排",
        description="金额较高，需要老板确认。",
        status="open",
        priority="high",
        payload={},
    )
    assignee_task = ExtractedItem(
        id=uuid4(),
        company_id=company_id,
        item_type="task",
        title="跟进项目资料",
        description="补齐资料",
        owner="项目负责人",
        status="open",
        priority="medium",
        payload={},
    )
    system_task = ExtractedItem(
        id=uuid4(),
        company_id=company_id,
        item_type="task",
        title="使用指南提醒",
        description="欢迎使用帮助中心。",
        status="open",
        priority="medium",
        payload={},
    )

    assert _task_attention_level(decision_task) == "owner_decision"
    assert _task_item(decision_task).attention_label == "需要你决策"
    assert _task_attention_level(assignee_task) == "assignee_action"
    assert _task_item(assignee_task).attention_label == "需要负责人推进"
    assert _task_attention_level(system_task) == "system_record"
    assert _task_item(system_task).attention_label == "系统记录"


def test_task_module_deduplicates_and_demotes_system_notices() -> None:
    company_id = uuid4()
    first = ExtractedItem(
        id=uuid4(),
        company_id=company_id,
        item_type="task",
        title="邮箱同步完成，写入或更新 10 条工作事件。",
        description="同步完成",
        status="open",
        priority="medium",
        payload={},
    )
    repeated = ExtractedItem(
        id=uuid4(),
        company_id=company_id,
        item_type="task",
        title="邮箱同步完成，写入或更新 10 条工作事件。",
        description="同步完成",
        status="open",
        priority="medium",
        payload={},
    )

    first_item = _task_item(first)
    repeated_item = _task_item(repeated)
    items = _dedupe_task_items([first_item, repeated_item])

    assert first_item.attention_label == "系统记录"
    assert len(items) == 1


def test_task_module_demotes_welcome_mail_even_with_project_terms() -> None:
    item = ExtractedItem(
        id=uuid4(),
        company_id=uuid4(),
        item_type="task",
        title="欢迎使用飞书邮箱！",
        description="你可以把邮件分享至项目群聊，也可以查看帮助中心。",
        status="open",
        priority="medium",
        payload={},
    )

    assert _task_attention_level(item) == "system_record"


def test_task_next_actions_follow_owner_decision_count() -> None:
    actions = _task_next_actions(owner_decisions=0, assignee_actions=3, system_records=2)

    assert actions[0] == "当前没有必须你决策的待办；有 3 条需要负责人推进。"
    assert "系统记录" not in actions[0]

    owner_actions = _task_next_actions(owner_decisions=1, assignee_actions=0, system_records=7)
    assert owner_actions[1] == "当前没有需要负责人推进的待办；系统记录项不需要你处理。"


def test_task_module_keeps_system_records_out_of_action_list_when_actionable_exists() -> None:
    source = Path("app/services/cockpit/modules/tasks.py").read_text()

    assert "actionable_items = [*owner_decisions, *assignee_actions]" in source
    assert "ordered = (actionable_items or system_records)[:limit]" in source


def test_resource_health_status_separates_auto_pending_from_owner_actions() -> None:
    never_synced = type(
        "ResourceStub",
        (),
        {"enabled": True, "last_sync_at": None},
    )()
    disabled = type(
        "ResourceStub",
        (),
        {"enabled": False, "last_sync_at": None},
    )()
    failed_run = type("RunStub", (), {"status": "failed"})()
    decision = {"status": "ready", "next_action": "进入自动同步。"}
    blocked_decision = {"status": "access_blocked", "next_action": "请先完成授权。"}

    assert _resource_health_status(never_synced, decision=decision, latest_run=None) == "never_synced"
    assert _resource_health_status(disabled, decision=decision, latest_run=None) == "disabled"
    assert _resource_health_status(never_synced, decision=decision, latest_run=failed_run) == "failed"
    assert _resource_health_status(never_synced, decision=blocked_decision, latest_run=None) == "access_blocked"


def test_resource_health_payload_exposes_data_layer_query_path(monkeypatch) -> None:
    monkeypatch.setattr("app.services.cockpit.modules.resources.get_v5_resource_sync_policy", lambda db, company_id: {})
    resource = type(
        "ResourceStub",
        (),
        {
            "id": uuid4(),
            "company_id": uuid4(),
            "resource_name": "客户主数据",
            "resource_id": "bascn_x",
            "resource_sub_id": "tbl_x",
            "resource_type": "bitable_table",
            "enabled": True,
            "last_sync_at": None,
            "config_json": {},
        },
    )()

    payload = _resource_health_payload(object(), resource=resource, latest_run=None)

    assert payload["sync_action"] == "master_data_index"
    assert payload["query_path"] == "lark_cli_first_then_index"


def test_resource_health_payload_exposes_rag_indexing_from_latest_run(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.cockpit.modules.resources.get_v5_resource_sync_policy",
        lambda db, company_id: {"large_document_mode": "knowledge_vectorize"},
    )
    resource = type(
        "ResourceStub",
        (),
        {
            "id": uuid4(),
            "company_id": uuid4(),
            "resource_name": "高频制度文档",
            "resource_id": "doccn_hot",
            "resource_sub_id": None,
            "resource_type": "drive_file",
            "enabled": True,
            "last_sync_at": datetime.now(UTC),
            "config_json": {"document_type": "docx"},
        },
    )()
    run = type(
        "RunStub",
        (),
        {
            "status": "success",
            "summary": {
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
        },
    )()

    payload = _resource_health_payload(object(), resource=resource, latest_run=run)

    assert payload["rag_indexing"]["document_store"] == "work_events"
    assert payload["rag_indexing_summary"] == "store=work_events / chunk=pending / vector=qdrant_vectors / rag=pending"
    assert payload["document_store"]["store"] == "work_events"
    assert payload["document_store_summary"] == "store=work_events / chunks=3 / events=1"


def test_resource_health_payload_keeps_cold_knowledge_out_of_rag_status(monkeypatch) -> None:
    monkeypatch.setattr("app.services.cockpit.modules.resources.get_v5_resource_sync_policy", lambda db, company_id: {})
    resource = type(
        "ResourceStub",
        (),
        {
            "id": uuid4(),
            "company_id": uuid4(),
            "resource_name": "大型技术手册",
            "resource_id": "doccn_cold",
            "resource_sub_id": None,
            "resource_type": "drive_file",
            "enabled": True,
            "last_sync_at": datetime.now(UTC),
            "config_json": {"document_type": "docx"},
        },
    )()
    run = type(
        "RunStub",
        (),
        {
            "status": "success",
            "summary": {
                "data_layer": "knowledge_cold",
                "sync_action": "document_index_only",
                "query_path": "lark_cli_realtime",
            },
        },
    )()

    payload = _resource_health_payload(object(), resource=resource, latest_run=run)

    assert payload["sync_action"] == "document_index_only"
    assert payload["query_path"] == "lark_cli_realtime"
    assert payload["rag_indexing"] is None
    assert payload["rag_indexing_summary"] is None


def test_resource_next_actions_keep_auto_sync_out_of_owner_work() -> None:
    actions = _resource_next_actions(blindspots=0, sync_errors=0, pending_auto=3, policy_excluded=0)

    assert actions[0] == "3 个数据源等待自动同步，通常不需要手工处理。"
    assert "技术细节放在设置页处理" in actions[1]

    normal_actions = _resource_next_actions(blindspots=0, sync_errors=0, pending_auto=0, policy_excluded=0)
    assert normal_actions[0] == "数据覆盖状态正常，可以继续优化报告和机器人问答质量。"


def test_decision_attention_levels_match_owner_cockpit() -> None:
    company_id = uuid4()
    owner_decision = ExtractedItem(
        id=uuid4(),
        company_id=company_id,
        item_type="decision",
        title="确认客户合同付款方案",
        description="涉及合同和付款，需要拍板。",
        status="open",
        priority="high",
        payload={},
    )
    evidence_needed = ExtractedItem(
        id=uuid4(),
        company_id=company_id,
        item_type="decision",
        title="评估新项目方案",
        description="需要补充测算数据和方案依据。",
        status="open",
        priority="medium",
        payload={},
    )
    record = ExtractedItem(
        id=uuid4(),
        company_id=company_id,
        item_type="decision",
        title="为确认邮件及讯息的成功传达，请您收到邮件后及时回复，谢谢~",
        description="邮件回执提醒。",
        status="open",
        priority="medium",
        payload={},
    )

    assert _decision_attention_level(owner_decision) == "owner_final_decision"
    assert _decision_item(owner_decision).attention_label == "需要老板拍板"
    assert _decision_attention_level(evidence_needed) == "evidence_needed"
    assert _decision_item(evidence_needed).attention_label == "需要补充依据"
    assert _decision_attention_level(record) == "decision_record"
    assert _decision_item(record).attention_label == "已沉淀记录"


def test_decision_module_deduplicates_repeated_items() -> None:
    company_id = uuid4()
    first = ExtractedItem(
        id=uuid4(),
        company_id=company_id,
        item_type="decision",
        title="录用通知书-固势科技-王悦",
        description="涉及合同和付款，需要拍板。",
        status="open",
        priority="high",
        payload={},
    )
    repeated = ExtractedItem(
        id=uuid4(),
        company_id=company_id,
        item_type="decision",
        title="回复：录用通知书-固势科技-王悦",
        description="涉及合同和付款，需要拍板。",
        status="open",
        priority="high",
        payload={},
    )

    items = _dedupe_decision_items([_decision_item(first), _decision_item(repeated)])

    assert len(items) == 1


def test_decision_next_actions_prioritize_owner_decisions() -> None:
    actions = _decision_next_actions(owner_decisions=2, evidence_needed=3, records=5)

    assert actions[0] == "先处理 2 条需要老板拍板的决策。"
    assert actions[1] == "3 条先要求补充依据，不要直接拍板。"


def test_project_attention_levels_match_owner_cockpit() -> None:
    now = datetime.now(UTC)
    company_id = uuid4()
    delivery_risk = WorkEvent(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        event_type="feishu.message",
        title="客户现场交付延期",
        content_text="设备验收异常，可能影响客户承诺。",
        occurred_at=now,
        payload={},
    )
    rd_progress = WorkEvent(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        event_type="feishu.message",
        title="控制器模块测试完成",
        content_text="研发测试完成，进入下一轮验证。",
        occurred_at=now,
        payload={},
    )
    normal_record = WorkEvent(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        event_type="feishu.message",
        title="项目周会记录",
        content_text="同步项目背景。",
        occurred_at=now,
        payload={},
    )

    assert _project_attention_level(delivery_risk) == "project_risk"
    assert _project_item(delivery_risk).attention_label == "客户/交付风险"
    assert _project_attention_level(rd_progress) == "rd_progress"
    assert _project_item(rd_progress).attention_label == "研发测试进展"
    assert _project_attention_level(normal_record) == "project_record"
    assert _project_item(normal_record).attention_label == "普通项目记录"


def test_project_module_filters_and_deduplicates_low_signal_events() -> None:
    now = datetime.now(UTC)
    company_id = uuid4()
    low_signal = WorkEvent(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        event_type="system",
        title="邮箱同步完成，写入或更新项目邮件。",
        content_text="同步完成",
        occurred_at=now,
        payload={},
    )
    first = WorkEvent(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        event_type="feishu.message",
        title="客户现场交付延期",
        content_text="设备验收异常。",
        occurred_at=now,
        payload={},
    )
    repeated = WorkEvent(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        event_type="feishu.message",
        title="客户现场交付延期",
        content_text="设备验收异常。",
        occurred_at=now,
        payload={},
    )

    items = _dedupe_project_items([_project_item(first), _project_item(repeated)])

    assert _is_low_signal_event(low_signal) is True
    assert _is_low_signal_event(first) is False
    assert len(items) == 1


def test_project_next_actions_prioritize_delivery_risks() -> None:
    actions = _project_next_actions(risks=2, rd_progress=3, records=5)

    assert actions[0] == "优先处理 2 条客户/交付风险，确认影响、责任人和恢复时间。"
    assert actions[1] == "3 条研发测试进展用于跟踪节点，异常再升级。"


def test_communication_attention_levels_match_owner_cockpit() -> None:
    now = datetime.now(UTC)
    company_id = uuid4()
    owner_reply = WorkEvent(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        event_type="feishu.message",
        title="请陈总确认付款安排",
        content_text="请您确认是否同意。",
        occurred_at=now,
        payload={},
    )
    business_attention = WorkEvent(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        event_type="mail.message",
        title="客户合同附件",
        content_text="客户发来合同附件，请查看。",
        occurred_at=now,
        payload={},
    )
    background = WorkEvent(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        event_type="feishu.message",
        title="群聊消息",
        content_text="收到。",
        occurred_at=now,
        payload={},
    )

    assert _communication_attention_level(owner_reply) == "owner_reply"
    assert _communication_item(owner_reply).attention_label == "需要你回应"
    assert _communication_attention_level(business_attention) == "business_attention"
    assert _communication_item(business_attention).attention_label == "需要关注"
    assert _communication_attention_level(background) == "communication_record"
    assert _communication_item(background).attention_label == "背景记录"


def test_communication_module_filters_and_deduplicates_low_signal_events() -> None:
    now = datetime.now(UTC)
    company_id = uuid4()
    low_signal = WorkEvent(
        id=uuid4(),
        company_id=company_id,
        source="system",
        event_type="mail.sync",
        title="邮箱同步完成，写入或更新 10 条工作事件。",
        content_text="同步完成",
        occurred_at=now,
        payload={},
    )
    welcome_mail = WorkEvent(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        event_type="mail.message",
        title="欢迎使用飞书邮箱！",
        content_text="请您查看帮助中心和使用指南。",
        occurred_at=now,
        payload={},
    )
    signature_notice = WorkEvent(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        event_type="mail.message",
        title="关于邮件系统迁移后统一邮件签名规范的提醒",
        content_text="请及时更新邮件签名。",
        occurred_at=now,
        payload={},
    )
    salary_sheet = WorkEvent(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        event_type="mail.message",
        title="2025年11月固势工资明细汇总表",
        content_text="附件为工资明细，请核对。",
        occurred_at=now,
        payload={},
    )
    first = WorkEvent(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        event_type="feishu.message",
        title="请陈总确认客户合同",
        content_text="请您确认是否同意。",
        occurred_at=now,
        payload={},
    )
    repeated = WorkEvent(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        event_type="feishu.message",
        title="请陈总确认客户合同",
        content_text="请您确认是否同意。",
        occurred_at=now,
        payload={},
    )

    items = _dedupe_communication_items([_communication_item(first), _communication_item(repeated)])

    assert _is_low_signal_communication_event(low_signal) is True
    assert _is_low_signal_communication_event(welcome_mail) is True
    assert _is_low_signal_communication_event(signature_notice) is True
    assert _is_low_signal_communication_event(salary_sheet) is True
    assert _is_low_signal_communication_event(first) is False
    assert len(items) == 1


def test_communication_next_actions_prioritize_owner_replies() -> None:
    actions = _communication_next_actions(owner_replies=2, business_attention=4, records=8)

    assert actions[0] == "先处理 2 条可能需要你本人回应的沟通。"
    assert actions[1] == "再扫 4 条业务关注线索，必要时转成任务或风险。"


def test_meeting_attention_levels_match_owner_cockpit() -> None:
    now = datetime.now(UTC)
    company_id = uuid4()
    decision_meeting = WorkEvent(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        event_type="calendar.meeting",
        title="项目方案评审会议",
        content_text="需要确认预算和合同安排。",
        occurred_at=now,
        payload={},
    )
    follow_up_meeting = WorkEvent(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        event_type="meeting.summary",
        title="销售会后纪要",
        content_text="行动项：负责人本周五前跟进客户。",
        occurred_at=now,
        payload={},
    )
    background_meeting = WorkEvent(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        event_type="calendar.event",
        title="例行日程",
        content_text="部门例会。",
        occurred_at=now,
        payload={},
    )

    assert _meeting_attention_level(decision_meeting) == "meeting_decision"
    assert _meeting_item(decision_meeting).attention_label == "需要决策"
    assert _meeting_attention_level(follow_up_meeting) == "meeting_follow_up"
    assert _meeting_item(follow_up_meeting).attention_label == "会后跟进"
    assert _meeting_attention_level(background_meeting) == "meeting_record"
    assert _meeting_item(background_meeting).attention_label == "背景日程"


def test_meeting_module_filters_and_deduplicates_low_signal_events() -> None:
    now = datetime.now(UTC)
    company_id = uuid4()
    low_signal = WorkEvent(
        id=uuid4(),
        company_id=company_id,
        source="system",
        event_type="calendar.sync",
        title="日历同步完成，写入或更新 3 条工作事件。",
        content_text="同步完成",
        occurred_at=now,
        payload={},
    )
    sync_record = WorkEvent(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        event_type="meeting.sync",
        title="飞书meetings同步记录",
        content_text="同步记录",
        occurred_at=now,
        payload={},
    )
    first = WorkEvent(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        event_type="meeting.summary",
        title="销售会后纪要",
        content_text="行动项：负责人本周五前跟进客户。",
        occurred_at=now,
        payload={},
    )
    repeated = WorkEvent(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        event_type="meeting.summary",
        title="销售会后纪要",
        content_text="行动项：负责人本周五前跟进客户。",
        occurred_at=now,
        payload={},
    )

    items = _dedupe_meeting_items([_meeting_item(first), _meeting_item(repeated)])

    assert _is_low_signal_meeting_event(low_signal) is True
    assert _is_low_signal_meeting_event(sync_record) is True
    assert _is_low_signal_meeting_event(first) is False
    assert len(items) == 1


def test_meeting_next_actions_prioritize_decisions() -> None:
    actions = _meeting_next_actions(decisions=1, follow_ups=3, records=8)

    assert actions[0] == "先处理 1 条可能需要你拍板的会议事项。"
    assert actions[1] == "再检查 3 条会后跟进，确认负责人和截止时间。"


def test_today_focus_prioritizes_owner_decision_items() -> None:
    company_id = uuid4()
    now = datetime.now(UTC)
    high_amount_approval = WorkEvent(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        event_type="feishu.approvals.instance",
        title="付款审批",
        content_text="付款申请 金额 50000",
        occurred_at=now,
        payload={"item": {"amount": 50000}, "status": "pending"},
        importance_score=0.2,
    )
    low_value_event = WorkEvent(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        event_type="message",
        title="普通消息",
        content_text="收到",
        occurred_at=now,
        payload={},
        importance_score=0.0,
    )
    risk = ExtractedItem(
        id=uuid4(),
        company_id=company_id,
        item_type="risk",
        title="客户现场延期风险",
        description="测试设备交付可能延期",
        status="open",
        priority="high",
        payload={},
    )

    items = _build_today_focus_items([low_value_event, high_amount_approval], [risk], limit=3)

    assert [item.title for item in items] == ["客户现场延期风险", "付款审批"]
    assert items[0].subtitle == "这是开放风险，可能影响经营结果或执行进度。"
    assert items[0].attention_label == "必须处理"
    assert items[0].description == "确认负责人、处理时限和是否需要升级处理。"
    assert items[1].payload["amount"] == 50000
    assert items[1].attention_label == "必须处理"
    assert items[1].description == "查看审批明细，确认同意、拒绝或补充资料。"


def test_today_focus_uses_business_payload_for_owner_guidance() -> None:
    company_id = uuid4()
    item = ExtractedItem(
        id=uuid4(),
        company_id=company_id,
        item_type="decision",
        title="固势2025年度继续服务奖金",
        description="发放规则需要确认",
        status="open",
        priority="high",
        payload={
            "business_object": "compensation",
            "suggested_action": "确认发放时间、顺延条件、税务处理和通知责任人。",
        },
    )

    items = _build_today_focus_items([], [item], limit=1)

    assert items[0].subtitle == "涉及薪酬/奖金发放规则或现金流安排，需要老板确认口径。"
    assert items[0].description == "确认发放时间、顺延条件、税务处理和通知责任人。"


def test_today_focus_splits_owner_attention_levels() -> None:
    company_id = uuid4()
    now = datetime.now(UTC)
    approved_event = WorkEvent(
        id=uuid4(),
        company_id=company_id,
        source="feishu",
        event_type="feishu.approval",
        title="审批已通过",
        content_text="审批已通过",
        occurred_at=now,
        payload={"status": "approved"},
        importance_score=0.0,
    )
    monitor_task = ExtractedItem(
        id=uuid4(),
        company_id=company_id,
        item_type="task",
        title="跟进项目资料",
        description="补齐项目资料",
        status="open",
        priority="medium",
        payload={},
    )
    must_handle_decision = ExtractedItem(
        id=uuid4(),
        company_id=company_id,
        item_type="decision",
        title="确认付款安排",
        description="金额较高",
        status="open",
        priority="high",
        payload={},
    )

    items = _build_today_focus_items([approved_event], [monitor_task, must_handle_decision], limit=5)
    labels = {item.title: item.attention_label for item in items}

    assert labels["确认付款安排"] == "必须处理"
    assert labels["跟进项目资料"] == "可关注"
    assert labels["审批已通过"] == "系统自动处理"


def test_today_focus_hides_low_signal_noise_items() -> None:
    company_id = uuid4()
    salary_notice = ExtractedItem(
        id=uuid4(),
        company_id=company_id,
        item_type="risk",
        title="2026年3月固势工资明细汇总表",
        description="附件为固势3月的薪资汇总，请核对审批，有问题随时沟通。",
        status="open",
        priority="medium",
        payload={},
    )
    admin_notice = ExtractedItem(
        id=uuid4(),
        company_id=company_id,
        item_type="risk",
        title="关于邮件系统迁移后统一邮件签名规范的提醒",
        description="请及时更新邮件签名。",
        status="open",
        priority="medium",
        payload={},
    )
    real_risk = ExtractedItem(
        id=uuid4(),
        company_id=company_id,
        item_type="risk",
        title="客户现场延期风险",
        description="客户现场验收可能延期。",
        status="open",
        priority="medium",
        payload={},
    )

    items = _build_today_focus_items([], [salary_notice, admin_notice, real_risk], limit=5)

    assert [item.title for item in items] == ["客户现场延期风险"]


def test_today_focus_query_filters_historical_medium_items() -> None:
    source = Path("app/services/cockpit/modules/today.py").read_text()

    assert "WorkEvent.occurred_at >= today_start" in source
    assert "ExtractedItem.created_at.desc()" in source
    assert "ExtractedItem.priority.in_(HIGH_PRIORITIES)" in source


def test_today_focus_deduplicates_repeated_extracted_titles() -> None:
    company_id = uuid4()
    repeated_a = ExtractedItem(
        id=uuid4(),
        company_id=company_id,
        item_type="decision",
        title="固势2025年度继续服务奖金",
        description="发放规则需要确认",
        status="open",
        priority="medium",
        payload={},
    )
    repeated_b = ExtractedItem(
        id=uuid4(),
        company_id=company_id,
        item_type="decision",
        title="固势2025年度继续服务奖金",
        description="发放规则需要确认",
        status="open",
        priority="medium",
        payload={},
    )

    items = _build_today_focus_items([], [repeated_a, repeated_b], limit=5)

    assert [item.title for item in items] == ["固势2025年度继续服务奖金"]


def test_today_focus_empty_state_guides_owner_to_workbenches() -> None:
    summary = _today_summary(today_events=5, risk_count=0, task_count=20, decision_count=16, focus_count=0)
    actions = _today_next_actions(focus_count=0, risk_count=0, task_count=20, decision_count=16, today_events=5)

    assert "当前没有必须老板立即处理的高优先级事项" in summary
    assert actions[0] == "今天暂无必须老板立即处理的事项。"
    assert "16 条开放决策事项" in actions[1]
    assert "20 条开放待办" in actions[2]


def test_today_focus_next_actions_follow_attention_levels() -> None:
    summary = _today_summary(
        today_events=8,
        risk_count=0,
        task_count=2,
        decision_count=3,
        focus_count=4,
        must_handle_count=0,
        monitor_count=4,
        system_managed_count=0,
    )
    actions = _today_next_actions(
        focus_count=4,
        risk_count=0,
        task_count=2,
        decision_count=3,
        today_events=8,
        must_handle_count=0,
        monitor_count=4,
        system_managed_count=0,
    )

    assert "必须处理 0 项、可关注 4 项、系统自动处理 0 项" in summary
    assert actions[0] == "今天暂无必须老板立即处理的事项；有 4 项可关注事项，建议快速扫一遍。"
    assert "高风险" not in actions[0]


def test_risk_summary_ignores_closed_history_noise() -> None:
    summary = _risk_summary(open_items=[], high_items=[], coverage_risks=[])

    assert summary == "当前没有开放风险或数据盲区处理动作。"
    assert "当前共" not in summary


def test_cockpit_risk_module_hides_normal_salary_sheet_noise() -> None:
    salary_notice = ExtractedItem(
        id=uuid4(),
        company_id=uuid4(),
        item_type="risk",
        title="2026年3月固势工资明细汇总表",
        description="附件为固势3月的薪资汇总，请核对审批，有问题随时沟通。",
        status="open",
        priority="medium",
        payload={},
    )
    real_risk = ExtractedItem(
        id=uuid4(),
        company_id=uuid4(),
        item_type="risk",
        title="工资明细存在差异和问题",
        description="请尽快确认风险。",
        status="open",
        priority="high",
        payload={},
    )

    assert _is_open_business_risk(salary_notice) is False
    assert _is_open_business_risk(real_risk) is True


def test_cockpit_risk_module_hides_low_signal_notices() -> None:
    notice = ExtractedItem(
        id=uuid4(),
        company_id=uuid4(),
        item_type="risk",
        title="关于邮件系统迁移后统一邮件签名规范的提醒",
        description="请及时更新邮件签名。",
        status="open",
        priority="medium",
        payload={},
    )
    payment = ExtractedItem(
        id=uuid4(),
        company_id=uuid4(),
        item_type="risk",
        title="付款审批通知",
        description="付款申请金额 50000，等待审批。",
        status="open",
        priority="medium",
        payload={},
    )

    assert _is_open_business_risk(notice) is False
    assert _is_open_business_risk(payment) is True


def test_data_coverage_items_show_business_owner() -> None:
    resources_module = Path("app/services/cockpit/modules/resources.py").read_text()
    risks_module = Path("app/services/cockpit/modules/risks.py").read_text()

    assert 'owner=item.get("responsible_role") or "系统管理员"' in resources_module
    assert 'owner=item.get("responsible_role") or "系统管理员"' in risks_module
    assert '"business_domain": item.get("business_domain")' in resources_module
    assert '"responsible_role": item.get("responsible_role")' in risks_module
    assert "owner_next_step_detail" in resources_module
    assert "数据盲区：{_owner_action_title(item)}" in risks_module
