from types import SimpleNamespace
from uuid import uuid4

from app.models.entities import ToolConfig
from app.services.agent.policies import BotActor
from app.services.tools.base import ToolContext, ToolDefinition, ToolExecutionStatus, ToolProvider, ToolRequest
from app.services.tools.providers.feishu_api import FEISHU_API_CAPABILITIES, FeishuApiRisk, feishu_write_confirmation_token
from app.services.tools.router import TOOL_REGISTRY, _execute_provider_tool, execute_agent_tool
from app.services.tools.write_audit import write_target_metadata, write_target_summary


def test_execute_agent_tool_routes_company_qa(monkeypatch) -> None:
    captured = {}

    def fake_company(*args, **kwargs):
        captured.update(kwargs)
        return "公司摘要"

    monkeypatch.setattr("app.services.tools.providers.report.answer_company_question", fake_company)

    result = execute_agent_tool(
        ToolContext(db=None, company_id=uuid4(), actor=BotActor(role="owner", access_scope="company")),
        ToolRequest(tool_name="company_qa", question="公司风险", normalized_command="公司风险"),
    )

    assert result.tool_name == "company_qa"
    assert result.provider == ToolProvider.REPORT
    assert result.answer == "公司摘要"
    assert result.structured_result["response_text"] == "公司摘要"
    assert result.structured_result["final_answer_allowed"] is False
    assert result.structured_result["final_answer_owner"] == "agent_runtime"
    assert result.data_source == "PostgreSQL"
    assert result.execution_source is None
    assert result.metadata["tool_decides_data_or_execution_source"] is True
    assert result.metadata["tool_returns_structured_result"] is True
    assert result.metadata["final_answer_owner"] == "agent_runtime"
    assert result.metadata["business_tool"] == "ReportTool"
    assert result.metadata["tool_is_global_shared_business_capability"] is True
    assert result.metadata["tool_invocation_policy"] == "global_shared_tool"
    assert result.metadata["data_permission_model"] == "identity_scoped_tighten_only"
    assert result.metadata["data_boundary_policy"] == "tool_global_data_identity_bounded"
    assert result.metadata["company_scope"]
    assert result.metadata["role_scope"] == {
        "role": "owner",
        "access_scope": "company",
        "domains": [],
        "company_data_allowed": True,
    }
    assert result.metadata["enterprise_identity"] == "app_identity"
    assert result.metadata["enterprise_identity_boundary"] == "App Identity + Company Scope + Role Scope"
    assert result.metadata["enterprise_company_scope"] == str(result.metadata["company_scope"])
    assert result.metadata["enterprise_role_scope"] == result.metadata["role_scope"]
    assert result.metadata["can_exceed_feishu_app_permissions"] is False
    assert result.metadata["data_boundary_enforcement"] == {
        "tool_is_global_shared": True,
        "enterprise_resources": ["app_identity", "company_scope", "role_scope"],
        "user_resources": ["resource_owner_authorization"],
        "digital_advisor_can_only_tighten": True,
        "can_escalate_original_permissions": False,
    }
    assert result.metadata["tool_access_policy"] == "all_business_tools_shared"
    assert result.metadata["tool_sharing_model"] == "shared_business_tools_per_employee_agent"
    contract = result.metadata["identity_permission_contract"]
    assert contract["agent_model"] == "per_user_personal_agent"
    assert contract["tool_model"] == "global_shared_business_tools"
    assert contract["shared_business_tools"][-1] == "PeopleTool"
    assert contract["enterprise_resource_boundary"]["constraints"] == ["app_identity", "company_scope", "role_scope"]
    assert contract["permission_enforcement"]["enterprise_resources"] == "app_identity_company_scope_role_scope"
    assert contract["permission_enforcement"]["user_resources"] == "resource_owner_authorization"
    assert result.metadata["app_identity_required"] is True
    assert result.metadata["enterprise_resource_boundary"] == {
        "identity": "app_identity",
        "resource_owner": "feishu_custom_app_da_fei_ge",
        "constraints": ["app_identity", "company_scope", "role_scope"],
        "company_scope": str(result.metadata["company_scope"]),
        "role_scope": result.metadata["role_scope"],
        "can_exceed_feishu_app_permissions": False,
    }
    assert result.metadata["enterprise_resource_boundary"]["can_exceed_feishu_app_permissions"] is False
    assert result.metadata["user_identity_supported_resources"] == [
        "personal_feishu",
        "external_mail",
        "personal_dingtalk",
        "personal_wechat",
    ]
    assert result.metadata["digital_advisor_permission_policy"] == "tighten_only"
    assert result.metadata["cannot_escalate_original_permissions"] is True
    assert result.structured_result["enterprise_identity"] == "app_identity"
    assert result.structured_result["enterprise_identity_boundary"] == "App Identity + Company Scope + Role Scope"
    assert result.structured_result["enterprise_resource_boundary"] == result.metadata["enterprise_resource_boundary"]
    assert result.structured_result["enterprise_role_scope"] == result.metadata["role_scope"]
    assert result.structured_result["can_exceed_feishu_app_permissions"] is False
    assert result.metadata["source_chain"] == ["PostgreSQL"]
    assert result.metadata["required_permissions"] == ["company:read"]
    assert result.metadata["supports_write"] is False
    assert result.metadata["audit_action"] == "tool.company_qa.report"
    assert captured["question"] == "公司风险"
    assert captured["normalized_command"] == "公司风险"


def test_tool_router_blocks_feishu_api_provider_for_realtime_business_tools() -> None:
    context = ToolContext(db=None, company_id=uuid4(), actor=BotActor(role="owner", access_scope="company"))
    request = ToolRequest(tool_name="task_qa", question="任务", normalized_command="任务")

    try:
        _execute_provider_tool(ToolProvider.FEISHU_API, context, request)
    except PermissionError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected Feishu API provider to be blocked in Tool Router")

    assert "reserved for Sync Engine data synchronization" in message
    assert "realtime business tools must use MCP -> CLI" in message


def test_bitable_qa_uses_local_data_source_without_explicit_base_target(monkeypatch) -> None:
    captured = {}

    def fake_local_tool(context, request):
        captured["request"] = request
        return "本地主数据索引结果"

    monkeypatch.setattr("app.services.tools.router.execute_local_tool", fake_local_tool)

    result = execute_agent_tool(
        ToolContext(db=None, company_id=uuid4(), actor=BotActor(role="owner", access_scope="company")),
        ToolRequest(tool_name="bitable_qa", question="项目进展怎么样", normalized_command="项目进展怎么样"),
    )

    assert result.status == ToolExecutionStatus.SUCCESS
    assert result.provider == ToolProvider.LOCAL
    assert result.data_source == "PostgreSQL"
    assert result.execution_source is None
    assert result.metadata["execution_chain"] == ["local"]
    assert "preferred_execution_engine" not in result.metadata
    assert result.answer == "本地主数据索引结果"
    assert captured["request"].params == {}


def test_bitable_qa_uses_mcp_execution_source_with_explicit_base_target(monkeypatch) -> None:
    captured = {}

    def fake_feishu_mcp_tool(context, request):
        captured["request"] = request
        return "飞书实时 Base 结果"

    monkeypatch.setattr("app.services.tools.router.execute_feishu_mcp_tool", fake_feishu_mcp_tool)

    result = execute_agent_tool(
        ToolContext(db=None, company_id=uuid4(), actor=BotActor(role="owner", access_scope="company")),
        ToolRequest(
            tool_name="bitable_qa",
            question="读取这个多维表格",
            normalized_command="读取这个多维表格",
            params={"app_token": "bascn_1"},
        ),
    )

    assert result.status == ToolExecutionStatus.SUCCESS
    assert result.provider == ToolProvider.FEISHU_MCP
    assert result.data_source is None
    assert result.execution_source == "MCP -> CLI -> Feishu"
    assert result.answer == "飞书实时 Base 结果"
    assert captured["request"].params["app_token"] == "bascn_1"


def test_execute_agent_tool_marks_general_chat_as_fast_no_enterprise_data() -> None:
    result = execute_agent_tool(
        ToolContext(db=None, company_id=uuid4(), actor=BotActor(role="member", access_scope="chat")),
        ToolRequest(tool_name="general_chat", question="你好", normalized_command="你好"),
    )

    assert result.status == ToolExecutionStatus.SUCCESS
    assert result.data_source is None
    assert result.execution_source == "Pure Reasoning"
    assert result.metadata["source_kind"] == "execution_source"
    assert result.metadata["source_chain"] == ["Pure Reasoning"]
    assert result.structured_result["data_source"] is None
    assert result.structured_result["execution_source"] == "Pure Reasoning"
    assert result.structured_result["source_chain"] == ["Pure Reasoning"]


def test_execute_agent_tool_routes_current_chat_summary(monkeypatch) -> None:
    captured = {}

    def fake_chat_summary(*args, **kwargs):
        captured.update(kwargs)
        return "群摘要"

    monkeypatch.setattr("app.services.tools.providers.local.answer_current_chat_summary", fake_chat_summary)

    result = execute_agent_tool(
        ToolContext(db=None, company_id=uuid4(), actor=BotActor(role="member", access_scope="chat"), chat_id="oc_1"),
        ToolRequest(tool_name="chat_summary", question="总结", normalized_command="总结"),
    )

    assert result.answer == "群摘要"
    assert captured["chat_id"] == "oc_1"


def test_execute_agent_tool_marks_personal_task_boundary(monkeypatch) -> None:
    monkeypatch.setattr("app.services.tools.config.get_tool_config", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.tools.providers.local.answer_personal_tasks", lambda *args, **kwargs: "本人待办")
    actor = BotActor(role="member", access_scope="personal", display_name="王工", open_id="ou_1")

    class FakeDb:
        def scalar(self, query):
            return SimpleNamespace(
                settings={
                    "user_identity_authorizations": {
                        "user_identity_bundle": {"status": "authorized", "owner_open_id": "ou_1"},
                    }
                }
            )

        def add(self, item):
            pass

    result = execute_agent_tool(
        ToolContext(db=FakeDb(), company_id=uuid4(), actor=actor),
        ToolRequest(tool_name="personal_tasks", question="我的待办", normalized_command="我的待办"),
    )

    assert result.answer == "本人待办"
    assert result.metadata["data_access_scope"] == "personal"
    assert result.metadata["personal_owner_open_id"] == "ou_1"
    assert result.metadata["has_strong_identity"] is True
    assert result.metadata["cross_user_data_allowed"] is False
    assert result.metadata["user_identity"] == "resource_owner_identity"
    assert result.metadata["user_identity_owner_open_id"] == "ou_1"
    assert result.metadata["user_identity_boundary"] == "User Identity + Resource Owner Authorization"
    assert result.metadata["can_exceed_user_original_authorization"] is False
    assert result.structured_result["data_access_scope"] == "personal"
    assert result.structured_result["personal_owner_open_id"] == "ou_1"
    assert result.structured_result["cross_user_data_allowed"] is False
    assert result.structured_result["business_tool"] == "TaskTool"
    assert result.structured_result["user_identity_required"] is True
    assert result.structured_result["user_identity_constraints"] == ["resource_owner_authorization"]
    assert result.structured_result["user_identity"] == "resource_owner_identity"
    assert result.structured_result["user_identity_owner_open_id"] == "ou_1"
    assert result.structured_result["user_identity_boundary"] == "User Identity + Resource Owner Authorization"
    assert result.structured_result["user_resource_boundary"]["can_exceed_original_authorization"] is False
    assert result.structured_result["can_exceed_user_original_authorization"] is False
    assert result.structured_result["cannot_escalate_original_permissions"] is True


def test_execute_agent_tool_requires_personal_feishu_authorization_for_personal_tasks(monkeypatch) -> None:
    monkeypatch.setattr("app.services.tools.config.get_tool_config", lambda *args, **kwargs: None)

    class FakeDb:
        def scalar(self, query):
            return SimpleNamespace(settings={"user_identity_authorizations": {}})

        def add(self, item):
            pass

    company_id = uuid4()
    result = execute_agent_tool(
        ToolContext(db=FakeDb(), company_id=company_id, actor=BotActor(role="member", access_scope="personal", open_id="ou_1")),
        ToolRequest(tool_name="personal_tasks", question="我的待办", normalized_command="我的待办"),
    )

    assert result.status == ToolExecutionStatus.DENIED
    assert result.error == "user_identity_authorization_required:user_identity_bundle"
    assert result.metadata["user_identity_authorization_required"] is True
    assert result.metadata["required_user_identity_resources"] == ["user_identity_bundle"]
    assert result.metadata["authorization_actions"] == [
        {
            "resource_type": "user_identity_bundle",
            "label": "授权个人能力包",
            "channel": "feishu_oauth",
            "authorization_flow": "feishu_in_app_oauth",
            "url": (
                "http://127.0.0.1:8000/api/user-identity/oauth/feishu/start"
                f"?company_id={company_id}&open_id=ou_1"
            ),
            "start_endpoint": "/api/user-identity/oauth/feishu/start",
            "callback_endpoint": "/api/feishu/oauth/callback",
            "instruction": "从大飞哥授权卡片打开飞书内授权页，由资源所有者本人确认授权；系统只按本人原始授权范围读取个人飞书资源。",
            "fallback_debug_flow": "feishu_cli_split_flow",
            "fallback_debug_url": (
                "http://127.0.0.1:8000/user-auth/feishu-cli"
                f"?company_id={company_id}&open_id=ou_1"
            ),
            "covered_resources": ["personal_feishu", "external_mail", "personal_dingtalk", "personal_wechat"],
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
    assert "一次整体授权" in result.metadata["first_use_guidance"]
    assert result.structured_result["authorization_actions"][0]["resource_type"] == "user_identity_bundle"


def test_execute_agent_tool_requires_personal_feishu_authorization_for_personal_calendar(monkeypatch) -> None:
    monkeypatch.setattr("app.services.tools.config.get_tool_config", lambda *args, **kwargs: None)

    class FakeDb:
        def scalar(self, query):
            return SimpleNamespace(settings={"user_identity_authorizations": {}})

        def add(self, item):
            pass

    company_id = uuid4()
    result = execute_agent_tool(
        ToolContext(db=FakeDb(), company_id=company_id, actor=BotActor(role="member", access_scope="personal", open_id="ou_1")),
        ToolRequest(tool_name="calendar_qa", question="我的日程", normalized_command="我的日程"),
    )

    assert result.status == ToolExecutionStatus.DENIED
    assert result.error == "user_identity_authorization_required:user_identity_bundle"
    assert result.metadata["required_user_identity_resources"] == ["user_identity_bundle"]
    assert result.metadata["authorization_actions"][0]["resource_type"] == "user_identity_bundle"
    assert f"company_id={company_id}" in result.metadata["authorization_actions"][0]["url"]
    assert "open_id=ou_1" in result.metadata["authorization_actions"][0]["url"]
    assert result.structured_result["authorization_actions"][0]["label"] == "授权个人能力包"


def test_execute_agent_tool_requires_user_identity_for_approval_tasks_query(monkeypatch) -> None:
    monkeypatch.setattr("app.services.tools.config.get_tool_config", lambda *args, **kwargs: None)

    class FakeDb:
        def scalar(self, query):
            return SimpleNamespace(settings={"user_identity_authorizations": {}})

        def add(self, item):
            pass

    company_id = uuid4()
    result = execute_agent_tool(
        ToolContext(
            db=FakeDb(),
            company_id=company_id,
            actor=BotActor(role="owner", access_scope="company", open_id="ou_owner"),
        ),
        ToolRequest(
            tool_name="feishu_approval_task_query",
            question="待我处理的审批",
            normalized_command="待我处理的审批",
        ),
    )

    assert result.status == ToolExecutionStatus.DENIED
    assert result.error == "user_identity_authorization_required:user_identity_bundle"
    assert result.metadata["user_identity_authorization_required"] is True
    assert result.metadata["required_user_identity_resources"] == ["user_identity_bundle"]
    authorization_url = result.structured_result["authorization_actions"][0]["url"]
    assert "/api/user-identity/oauth/feishu/start" in authorization_url
    assert f"company_id={company_id}" in authorization_url
    assert "open_id=ou_owner" in authorization_url


def test_execute_agent_tool_allows_personal_calendar_with_feishu_authorization(monkeypatch) -> None:
    monkeypatch.setattr("app.services.tools.config.get_tool_config", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.tools.router.execute_feishu_mcp_tool", lambda context, request: "本人今日日程")

    class FakeDb:
        def scalar(self, query):
            return SimpleNamespace(
                settings={
                    "user_identity_authorizations": {
                        "user_identity_bundle": {"status": "authorized", "owner_open_id": "ou_1"},
                    }
                }
            )

        def add(self, item):
            pass

    result = execute_agent_tool(
        ToolContext(db=FakeDb(), company_id=uuid4(), actor=BotActor(role="member", access_scope="personal", open_id="ou_1")),
        ToolRequest(tool_name="calendar_qa", question="我的日程", normalized_command="我的日程"),
    )

    assert result.status == ToolExecutionStatus.SUCCESS
    assert result.answer == "本人今日日程"
    assert result.provider == ToolProvider.FEISHU_MCP
    assert result.metadata["user_identity_required"] is True
    assert result.metadata["user_resource_boundary"]["can_exceed_original_authorization"] is False
    assert result.metadata["required_user_identity_resources"] == ["user_identity_bundle"]
    assert result.metadata["execution_source"] == "MCP -> CLI -> Feishu"
    assert result.metadata["source_chain"] == ["MCP", "CLI", "Feishu"]
    assert result.metadata["final_answer_owner"] == "agent_runtime"
    assert result.structured_result["business_tool"] == "CalendarTool"
    assert result.structured_result["final_answer_allowed"] is False
    assert result.structured_result["final_answer_owner"] == "agent_runtime"


def test_execute_agent_tool_uses_context_cli_profile_for_feishu_mcp(monkeypatch) -> None:
    monkeypatch.setattr("app.services.tools.config.get_tool_config", lambda *args, **kwargs: None)
    captured = {}

    def fake_execute(context, request):
        captured["params"] = dict(request.params)
        return "公司日程"

    monkeypatch.setattr("app.services.tools.router.execute_feishu_mcp_tool", fake_execute)

    class FakeDb:
        def __init__(self):
            self.added = []

        def scalar(self, query):
            return None

        def add(self, item):
            self.added.append(item)

    db = FakeDb()
    result = execute_agent_tool(
        ToolContext(
            db=db,
            company_id=uuid4(),
            actor=BotActor(role="owner", access_scope="company", open_id="ou_owner"),
            cli_profile="company-gaustek",
        ),
        ToolRequest(
            tool_name="calendar_qa",
            question="今天有什么会议",
            normalized_command="今天有什么会议",
            params={"cli_profile": "malicious-profile"},
        ),
    )

    assert result.status == ToolExecutionStatus.SUCCESS
    assert captured["params"]["cli_profile"] == "company-gaustek"
    assert result.metadata["cli_profile"] == "company-gaustek"
    assert result.metadata["cli_profile_source"] == "feishu_app_config"
    assert db.added[-1].payload["cli_profile"] == "company-gaustek"
    assert db.added[-1].payload["cli_profile_source"] == "feishu_app_config"


def test_execute_agent_tool_rejects_other_users_personal_feishu_authorization(monkeypatch) -> None:
    monkeypatch.setattr("app.services.tools.config.get_tool_config", lambda *args, **kwargs: None)

    class FakeDb:
        def scalar(self, query):
            return SimpleNamespace(
                settings={
                    "user_identity_authorizations": {
                        "user_identity_bundle": {"status": "authorized", "owner_open_id": "ou_other"},
                    }
                }
            )

        def add(self, item):
            pass

    result = execute_agent_tool(
        ToolContext(db=FakeDb(), company_id=uuid4(), actor=BotActor(role="member", access_scope="personal", open_id="ou_1")),
        ToolRequest(tool_name="calendar_qa", question="我的日程", normalized_command="我的日程"),
    )

    assert result.status == ToolExecutionStatus.DENIED
    assert result.error == "user_identity_authorization_required:user_identity_bundle"
    assert result.metadata["authorization_owner"] == "resource_owner"
    assert result.metadata["can_escalate_original_permissions"] is False
    assert result.metadata["required_user_identity_resources"] == ["user_identity_bundle"]
    assert result.structured_result["authorization_actions"][0]["resource_type"] == "user_identity_bundle"


def test_execute_agent_tool_requires_personal_feishu_authorization_for_personal_meeting_search(monkeypatch) -> None:
    monkeypatch.setattr("app.services.tools.config.get_tool_config", lambda *args, **kwargs: None)

    class FakeDb:
        def scalar(self, query):
            return SimpleNamespace(settings={"user_identity_authorizations": {}})

        def add(self, item):
            pass

    result = execute_agent_tool(
        ToolContext(db=FakeDb(), company_id=uuid4(), actor=BotActor(role="member", access_scope="personal", open_id="ou_1")),
        ToolRequest(tool_name="feishu_vc_meeting_search", question="我的会议", normalized_command="我的会议", params={"query": "复盘"}),
    )

    assert result.status == ToolExecutionStatus.DENIED
    assert result.error == "user_identity_authorization_required:user_identity_bundle"
    assert result.metadata["authorization_actions"][0]["resource_type"] == "user_identity_bundle"
    assert result.structured_result["data_access_denied_reason"] == "user_identity_authorization_required:user_identity_bundle"


def test_execute_agent_tool_requires_user_identity_authorization_for_mail(monkeypatch) -> None:
    monkeypatch.setattr("app.services.tools.config.get_tool_config", lambda *args, **kwargs: None)

    class FakeDb:
        def scalar(self, query):
            return SimpleNamespace(settings={"user_identity_authorizations": {}})

        def add(self, item):
            pass

    result = execute_agent_tool(
        ToolContext(db=FakeDb(), company_id=uuid4(), actor=BotActor(role="member", access_scope="personal", open_id="ou_1")),
        ToolRequest(tool_name="mail_qa", question="最近邮件", normalized_command="最近邮件"),
    )

    assert result.status == ToolExecutionStatus.DENIED
    assert result.error == "user_identity_authorization_required:user_identity_bundle"
    assert result.metadata["tool_invocation_allowed_for_agents"] is True
    assert result.metadata["user_identity_authorization_required"] is True
    assert result.metadata["authorization_policy"] == "owner_granted_tighten_only"
    assert result.metadata["can_escalate_original_permissions"] is False
    assert result.metadata["authorization_actions"][0]["resource_type"] == "user_identity_bundle"
    assert "资源所有者本人完成" in result.answer
    assert result.structured_result["data_access_denied_reason"] == "user_identity_authorization_required:user_identity_bundle"
    assert result.structured_result["authorization_actions"][0]["label"] == "授权个人能力包"
    assert "/api/user-identity/oauth/feishu/start" in result.structured_result["authorization_actions"][0]["url"]
    assert result.structured_result["authorization_actions"][0]["start_endpoint"] == "/api/user-identity/oauth/feishu/start"
    assert "open_id=ou_1" in result.structured_result["authorization_actions"][0]["url"]


def test_execute_agent_tool_allows_mail_with_resource_owner_authorization(monkeypatch) -> None:
    monkeypatch.setattr("app.services.tools.config.get_tool_config", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.tools.router.execute_feishu_mcp_tool", lambda context, request: "本人邮件摘要")

    class FakeDb:
        def scalar(self, query):
            return SimpleNamespace(
                settings={
                    "user_identity_authorizations": {
                        "external_mail": {"status": "authorized", "owner_open_id": "ou_1"},
                    }
                }
            )

        def add(self, item):
            pass

    result = execute_agent_tool(
        ToolContext(db=FakeDb(), company_id=uuid4(), actor=BotActor(role="member", access_scope="personal", open_id="ou_1")),
        ToolRequest(tool_name="mail_qa", question="最近邮件", normalized_command="最近邮件"),
    )

    assert result.status == ToolExecutionStatus.SUCCESS
    assert result.answer == "本人邮件摘要"
    assert result.provider == ToolProvider.FEISHU_MCP
    assert result.metadata["user_identity_required"] is True
    assert result.metadata["user_identity_constraints"] == ["resource_owner_authorization"]


def test_execute_agent_tool_rejects_unknown_tool() -> None:
    try:
        execute_agent_tool(
            ToolContext(db=None, company_id=uuid4(), actor=BotActor(role="member", access_scope="chat")),
            ToolRequest(tool_name="unknown", question="x", normalized_command="x"),
        )
    except ValueError as exc:
        assert "Unknown agent tool" in str(exc)
    else:
        raise AssertionError("expected unknown tool to raise")


def test_tool_registry_definitions_are_traceable() -> None:
    assert set(TOOL_REGISTRY) == {
        "approval_qa",
        "bitable_qa",
        "calendar_qa",
        "chat_qa",
        "chat_summary",
        "chat_tasks",
        "company_qa",
        "domain_qa",
        "feishu_approval_attachment_download",
        "feishu_approval_instance_cancel",
        "feishu_approval_instance_cc",
        "feishu_approval_instance_get",
        "feishu_approval_instance_initiated",
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
        "feishu_cli_doctor",
        "feishu_cli_status",
        "feishu_contact_department_children",
        "feishu_contact_department_users",
        "feishu_contact_organization_snapshot",
        "feishu_contact_scope_list",
        "feishu_doc_fetch",
        "feishu_drive_file_list",
        "feishu_drive_search",
        "feishu_im_auto_join_public_chats",
        "feishu_im_chat_list",
        "feishu_im_chat_members_list",
        "feishu_im_chat_search",
        "feishu_im_chat_update",
        "feishu_im_create_chat",
        "feishu_im_feed_group_list",
        "feishu_im_feed_group_list_item",
        "feishu_im_feed_group_query_item",
        "feishu_im_feed_shortcut_create",
        "feishu_im_feed_shortcut_list",
        "feishu_im_feed_shortcut_remove",
        "feishu_im_flag_cancel",
        "feishu_im_flag_create",
        "feishu_im_flag_list",
        "feishu_im_image_upload",
        "feishu_im_message_list",
        "feishu_im_message_mget",
        "feishu_im_message_reply",
        "feishu_im_message_resource_download",
        "feishu_im_message_search",
        "feishu_im_pin_create",
        "feishu_im_pin_delete",
        "feishu_im_pin_list",
        "feishu_im_reaction_create",
        "feishu_im_reaction_delete",
        "feishu_im_reaction_list",
        "feishu_im_send_message",
        "feishu_im_thread_messages_list",
        "feishu_mail_attachment_download",
        "feishu_mail_contact_create",
        "feishu_mail_contact_delete",
        "feishu_mail_contact_list",
        "feishu_mail_decline_receipt",
        "feishu_mail_drafts_create",
        "feishu_mail_drafts_delete",
        "feishu_mail_drafts_send",
        "feishu_mail_drafts_update",
        "feishu_mail_folder_create",
        "feishu_mail_folder_list",
        "feishu_mail_folder_messages",
        "feishu_mail_folders_list",
        "feishu_mail_label_create",
        "feishu_mail_label_delete",
        "feishu_mail_label_list",
        "feishu_mail_label_update",
        "feishu_mail_lint_html",
        "feishu_mail_mailbox_info",
        "feishu_mail_message_delete",
        "feishu_mail_message_forward",
        "feishu_mail_message_get",
        "feishu_mail_message_mark",
        "feishu_mail_message_move",
        "feishu_mail_message_reply",
        "feishu_mail_message_send",
        "feishu_mail_messages_batch_get",
        "feishu_mail_reply_all",
        "feishu_mail_rule_create",
        "feishu_mail_rule_delete",
        "feishu_mail_rule_list",
        "feishu_mail_rule_update",
        "feishu_mail_send_receipt",
        "feishu_mail_sent_message_list",
        "feishu_mail_settings_get",
        "feishu_mail_settings_update",
        "feishu_mail_share_to_chat",
        "feishu_mail_signature_list",
        "feishu_mail_template_create",
        "feishu_mail_template_delete",
        "feishu_mail_template_list",
        "feishu_mail_template_update",
        "feishu_mail_threads_list",
        "feishu_mail_watch",
        "feishu_okr_cycle_list",
        "feishu_okr_objective_list",
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
        "feishu_vc_meeting_search",
        "feishu_wiki_node_list",
        "feishu_wiki_space_list",
        "general_chat",
        "mail_qa",
        "owner_cockpit",
        "personal_tasks",
        "public_knowledge_qa",
        "task_qa"
    }
    expected_providers = {
        "bitable_qa": ToolProvider.FEISHU_MCP,
        "calendar_qa": ToolProvider.FEISHU_MCP,
        "company_qa": ToolProvider.REPORT,
        "feishu_approval_instance_cancel": ToolProvider.FEISHU_MCP,
        "feishu_approval_instance_cc": ToolProvider.FEISHU_MCP,
        "feishu_approval_instance_remind": ToolProvider.FEISHU_MCP,
        "feishu_approval_instance_get": ToolProvider.FEISHU_MCP,
        "feishu_approval_instance_initiated": ToolProvider.FEISHU_MCP,
        "feishu_approval_attachment_download": ToolProvider.FEISHU_MCP,
        "feishu_approval_task_add_sign": ToolProvider.FEISHU_MCP,
        "feishu_approval_task_approve": ToolProvider.FEISHU_MCP,
        "feishu_approval_task_query": ToolProvider.FEISHU_MCP,
        "feishu_approval_task_reject": ToolProvider.FEISHU_MCP,
        "feishu_approval_task_rollback": ToolProvider.FEISHU_MCP,
        "feishu_approval_task_transfer": ToolProvider.FEISHU_MCP,
        "feishu_doc_fetch": ToolProvider.FEISHU_MCP,
        "feishu_drive_search": ToolProvider.FEISHU_MCP,
        "feishu_bitable_record_batch_create": ToolProvider.FEISHU_MCP,
        "feishu_bitable_record_batch_delete": ToolProvider.FEISHU_MCP,
        "feishu_bitable_record_batch_update": ToolProvider.FEISHU_MCP,
        "feishu_bitable_record_create": ToolProvider.FEISHU_MCP,
        "feishu_bitable_record_delete": ToolProvider.FEISHU_MCP,
        "feishu_bitable_record_remove_attachment": ToolProvider.FEISHU_MCP,
        "feishu_bitable_record_update": ToolProvider.FEISHU_MCP,
        "feishu_bitable_record_upload_attachment": ToolProvider.FEISHU_MCP,
        "feishu_bitable_record_upsert": ToolProvider.FEISHU_MCP,
            "feishu_bitable_field_create": ToolProvider.FEISHU_MCP,
            "feishu_bitable_field_delete": ToolProvider.FEISHU_MCP,
            "feishu_bitable_field_list": ToolProvider.FEISHU_MCP,
            "feishu_bitable_field_update": ToolProvider.FEISHU_MCP,
            "feishu_bitable_table_create": ToolProvider.FEISHU_MCP,
            "feishu_bitable_table_delete": ToolProvider.FEISHU_MCP,
            "feishu_bitable_table_update": ToolProvider.FEISHU_MCP,
            "feishu_bitable_view_create": ToolProvider.FEISHU_MCP,
            "feishu_bitable_view_delete": ToolProvider.FEISHU_MCP,
            "feishu_bitable_view_rename": ToolProvider.FEISHU_MCP,
            "feishu_bitable_view_get_card": ToolProvider.FEISHU_MCP,
            "feishu_bitable_view_get_timebar": ToolProvider.FEISHU_MCP,
            "feishu_bitable_view_get_visible_fields": ToolProvider.FEISHU_MCP,
            "feishu_bitable_view_set_card": ToolProvider.FEISHU_MCP,
            "feishu_bitable_view_set_filter": ToolProvider.FEISHU_MCP,
            "feishu_bitable_view_set_group": ToolProvider.FEISHU_MCP,
            "feishu_bitable_view_set_sort": ToolProvider.FEISHU_MCP,
            "feishu_bitable_view_set_timebar": ToolProvider.FEISHU_MCP,
            "feishu_bitable_view_set_visible_fields": ToolProvider.FEISHU_MCP,
        "feishu_calendar_create_event": ToolProvider.FEISHU_MCP,
        "feishu_contact_department_children": ToolProvider.FEISHU_MCP,
        "feishu_contact_department_users": ToolProvider.FEISHU_MCP,
        "feishu_contact_organization_snapshot": ToolProvider.FEISHU_MCP,
        "feishu_contact_scope_list": ToolProvider.FEISHU_MCP,
        "feishu_drive_file_list": ToolProvider.FEISHU_MCP,
        "feishu_cli_doctor": ToolProvider.DEVOPS,
        "feishu_cli_status": ToolProvider.DEVOPS,
        "feishu_im_auto_join_public_chats": ToolProvider.FEISHU_MCP,
        "feishu_im_chat_search": ToolProvider.FEISHU_MCP,
        "feishu_im_create_chat": ToolProvider.FEISHU_MCP,
        "feishu_im_message_list": ToolProvider.FEISHU_MCP,
        "feishu_im_send_message": ToolProvider.FEISHU_MCP,
        "feishu_mail_folder_list": ToolProvider.FEISHU_MCP,
        "feishu_mail_message_get": ToolProvider.FEISHU_MCP,
        "feishu_okr_cycle_list": ToolProvider.FEISHU_MCP,
        "feishu_okr_objective_list": ToolProvider.FEISHU_MCP,
        "feishu_vc_meeting_search": ToolProvider.FEISHU_MCP,
        "feishu_wiki_node_list": ToolProvider.FEISHU_MCP,
        "feishu_wiki_space_list": ToolProvider.FEISHU_MCP,
        "feishu_task_assign_members": ToolProvider.FEISHU_MCP,
        "feishu_task_comment": ToolProvider.FEISHU_MCP,
        "feishu_task_clear_ancestor": ToolProvider.FEISHU_MCP,
        "feishu_task_complete": ToolProvider.FEISHU_MCP,
        "feishu_task_create": ToolProvider.FEISHU_MCP,
        "feishu_task_delete": ToolProvider.FEISHU_MCP,
        "feishu_task_subtask_create": ToolProvider.FEISHU_MCP,
        "feishu_task_add_to_tasklist": ToolProvider.FEISHU_MCP,
        "feishu_task_reopen": ToolProvider.FEISHU_MCP,
        "feishu_task_set_ancestor": ToolProvider.FEISHU_MCP,
        "feishu_task_update": ToolProvider.FEISHU_MCP,
        "feishu_task_update_followers": ToolProvider.FEISHU_MCP,
        "feishu_task_update_reminders": ToolProvider.FEISHU_MCP,
        "feishu_task_upload_attachment": ToolProvider.FEISHU_MCP,
        "feishu_tasklist_create": ToolProvider.FEISHU_MCP,
        "feishu_tasklist_delete": ToolProvider.FEISHU_MCP,
        "feishu_tasklist_update": ToolProvider.FEISHU_MCP,
        "feishu_tasklist_set_members": ToolProvider.FEISHU_MCP,
        "feishu_tasklist_update_members": ToolProvider.FEISHU_MCP,
        "feishu_task_section_create": ToolProvider.FEISHU_MCP,
        "feishu_task_section_delete": ToolProvider.FEISHU_MCP,
        "feishu_task_section_update": ToolProvider.FEISHU_MCP,
        "mail_qa": ToolProvider.FEISHU_MCP,
        "owner_cockpit": ToolProvider.REPORT,
        "task_qa": ToolProvider.FEISHU_MCP,
    }
    for name, definition in TOOL_REGISTRY.items():
        assert definition.name == name
        assert definition.provider == expected_providers.get(name, ToolProvider.LOCAL)
        assert definition.audit_action.startswith("tool.")


def test_execute_agent_tool_rejects_disabled_tool(monkeypatch) -> None:
    disabled = ToolDefinition(
        name="general_chat",
        provider=ToolProvider.LOCAL,
        required_permissions=(),
        audit_action="tool.general_chat.answer",
        enabled=False,
    )
    monkeypatch.setitem(TOOL_REGISTRY, "general_chat", disabled)

    try:
        execute_agent_tool(
            ToolContext(db=None, company_id=uuid4(), actor=BotActor(role="member", access_scope="chat")),
            ToolRequest(tool_name="general_chat", question="你好", normalized_command="你好"),
        )
    except ValueError as exc:
        assert "Disabled agent tool" in str(exc)
    else:
        raise AssertionError("expected disabled tool to raise")


def test_execute_agent_tool_dispatches_to_provider(monkeypatch) -> None:
    monkeypatch.setitem(
        TOOL_REGISTRY,
        "company_qa",
        ToolDefinition(
            name="company_qa",
            provider=ToolProvider.REPORT,
            required_permissions=("company:read",),
            audit_action="tool.company_qa.report",
        ),
    )
    monkeypatch.setattr("app.services.tools.router.execute_report_tool", lambda context, request: "报告结果")

    result = execute_agent_tool(
        ToolContext(db=None, company_id=uuid4(), actor=BotActor(role="owner", access_scope="company")),
        ToolRequest(tool_name="company_qa", question="公司概览", normalized_command="公司概览"),
    )

    assert result.provider == ToolProvider.REPORT
    assert result.answer == "报告结果"
    assert result.metadata["provider"] == "report"


def test_execute_agent_tool_denies_missing_permission() -> None:
    result = execute_agent_tool(
        ToolContext(db=None, company_id=uuid4(), actor=BotActor(role="member", access_scope="chat")),
        ToolRequest(tool_name="company_qa", question="公司风险", normalized_command="公司风险"),
    )

    assert result.status == ToolExecutionStatus.DENIED
    assert result.error == "data_permission_denied:company:read"
    assert result.metadata["data_access_denied_reason"] == "data_permission_denied:company:read"
    assert result.metadata["tool_invocation_allowed"] is True
    assert result.metadata["tool_invocation_allowed_for_agents"] is True
    assert result.metadata["tool_invocation_policy"] == "global_shared_tool"
    assert result.metadata["data_permission_model"] == "identity_scoped_tighten_only"


def test_execute_agent_tool_denies_people_and_meeting_reads_for_regular_chat_member() -> None:
    for tool_name, (params, expected_error) in {
        "feishu_okr_cycle_list": ({"user_id": "ou_1"}, "data_permission_denied:okr:read"),
        "feishu_okr_objective_list": ({"cycle_id": "cycle_1"}, "data_permission_denied:okr:read"),
        "feishu_vc_meeting_search": ({"query": "复盘"}, "data_permission_denied:calendar:read"),
    }.items():
        result = execute_agent_tool(
            ToolContext(db=None, company_id=uuid4(), actor=BotActor(role="member", access_scope="chat")),
            ToolRequest(tool_name=tool_name, question="看 OKR", normalized_command="看 OKR", params=params),
        )

        assert result.status == ToolExecutionStatus.DENIED
        assert result.error == expected_error


def test_execute_agent_tool_denies_contact_read_for_regular_chat_member() -> None:
    for tool_name, params in {
        "feishu_contact_department_children": {"department_id": "0"},
        "feishu_contact_department_users": {"department_id": "0"},
        "feishu_contact_organization_snapshot": {"root_department_id": "0"},
        "feishu_contact_scope_list": {},
    }.items():
        result = execute_agent_tool(
            ToolContext(db=None, company_id=uuid4(), actor=BotActor(role="member", access_scope="chat")),
            ToolRequest(tool_name=tool_name, question="看通讯录", normalized_command="看通讯录", params=params),
        )

        assert result.status == ToolExecutionStatus.DENIED
        assert result.error == "data_permission_denied:contact:read"


def test_execute_agent_tool_restricts_devops_to_admins(monkeypatch) -> None:
    monkeypatch.setattr("app.services.tools.router.execute_devops_tool", lambda context, request: "CLI 正常")

    for tool_name in ("feishu_cli_status", "feishu_cli_doctor"):
        denied = execute_agent_tool(
            ToolContext(db=None, company_id=uuid4(), actor=BotActor(role="member", access_scope="chat")),
            ToolRequest(tool_name=tool_name, question="CLI 状态", normalized_command="CLI 状态"),
        )
        allowed = execute_agent_tool(
            ToolContext(db=None, company_id=uuid4(), actor=BotActor(role="admin", access_scope="company")),
            ToolRequest(tool_name=tool_name, question="CLI 状态", normalized_command="CLI 状态"),
        )

        assert denied.status == ToolExecutionStatus.DENIED
        assert denied.error == "missing_permission:system:admin"
        assert allowed.answer == "CLI 正常"
        assert allowed.provider == ToolProvider.DEVOPS


def test_execute_agent_tool_denies_task_write_for_regular_member() -> None:
    for tool_name, params in {
        "feishu_task_assign_members": {"task_guid": "task_1", "add_assignees": ["ou_1"]},
        "feishu_task_create": {"summary": "跟进客户"},
        "feishu_task_delete": {"task_guid": "task_1"},
        "feishu_task_subtask_create": {"parent_task_guid": "parent_1", "summary": "拆解子任务"},
        "feishu_task_add_to_tasklist": {"task_guid": "task_1", "tasklist_guid": "tl_1"},
        "feishu_task_reopen": {"task_guid": "task_1"},
        "feishu_task_clear_ancestor": {"task_guid": "task_1"},
        "feishu_task_set_ancestor": {"task_guid": "task_1", "ancestor_guid": "parent_1"},
        "feishu_task_update_followers": {"task_guid": "task_1", "add_followers": ["ou_1"]},
        "feishu_task_update_reminders": {"task_guid": "task_1", "relative_fire_minutes": [15]},
        "feishu_task_upload_attachment": {"resource_id": "task_1", "file_path": "attachments/customer.pdf"},
        "feishu_tasklist_create": {"name": "销售跟进清单"},
        "feishu_tasklist_delete": {"tasklist_guid": "tl_1"},
        "feishu_tasklist_update": {"tasklist_guid": "tl_1", "name": "销售跟进清单2026"},
        "feishu_tasklist_set_members": {"tasklist_guid": "tl_1", "set_members": ["ou_1", "ou_2"]},
        "feishu_tasklist_update_members": {"tasklist_guid": "tl_1", "add_members": ["ou_1"]},
        "feishu_task_section_create": {"name": "销售跟进", "resource_id": "tl_1"},
        "feishu_task_section_delete": {"section_guid": "sec_1"},
        "feishu_task_section_update": {"section_guid": "sec_1", "name": "已完成跟进"},
    }.items():
        result = execute_agent_tool(
            ToolContext(db=None, company_id=uuid4(), actor=BotActor(role="member", access_scope="chat")),
            ToolRequest(
                tool_name=tool_name,
                question="写入任务",
                normalized_command="写入任务",
                params={"dry_run": True, **params},
            ),
        )

        assert result.status == ToolExecutionStatus.DENIED
        assert result.error == "data_permission_denied:task:write"
        assert result.metadata["supports_write"] is True


def test_execute_agent_tool_denies_approval_write_for_regular_member() -> None:
    for tool_name, params in {
        "feishu_approval_task_approve": {"task_id": "task_1"},
        "feishu_approval_instance_cancel": {"instance_code": "inst_1"},
        "feishu_approval_instance_cc": {"instance_code": "inst_1", "cc_user_ids": ["ou_target"]},
        "feishu_approval_instance_remind": {"instance_code": "inst_1", "task_ids": ["task_1"]},
        "feishu_approval_task_add_sign": {"task_id": "task_1", "add_sign_user_ids": ["ou_target"], "add_sign_type": 3},
        "feishu_approval_task_rollback": {"task_id": "task_1", "node_ids": ["node_1"]},
        "feishu_approval_task_transfer": {"task_id": "task_1", "transfer_user_id": "ou_target"},
    }.items():
        result = execute_agent_tool(
            ToolContext(db=None, company_id=uuid4(), actor=BotActor(role="member", access_scope="chat")),
            ToolRequest(
                tool_name=tool_name,
                question="处理审批",
                normalized_command="处理审批",
                params={"dry_run": True, **params},
            ),
        )

        assert result.status == ToolExecutionStatus.DENIED
        assert result.error == "data_permission_denied:approval:write"
        assert result.metadata["supports_write"] is True


def test_execute_agent_tool_routes_feishu_doc_fetch_through_mcp(monkeypatch) -> None:
    captured = {}

    def fake_mcp(context, request):
        captured["request"] = request
        return "飞书文档已通过 CLI 读取：doccn_1"

    monkeypatch.setattr("app.services.tools.router.execute_feishu_mcp_tool", fake_mcp)
    result = execute_agent_tool(
        ToolContext(db=None, company_id=uuid4(), actor=BotActor(role="owner", access_scope="company")),
        ToolRequest(
            tool_name="feishu_doc_fetch",
            question="查制度",
            normalized_command="查制度",
            params={"doc": "doccn_1", "keyword": "制度", "scope": "keyword"},
        ),
    )

    assert result.status == ToolExecutionStatus.SUCCESS
    assert result.provider == ToolProvider.FEISHU_MCP
    assert captured["request"].tool_name == "feishu_doc_fetch"
    assert result.metadata["mcp_provider"] == "feishu_mcp"
    assert result.metadata["execution_chain"] == ["agent_runtime", "tool_router", "tool", "mcp", "cli", "feishu"]
    assert result.metadata["sync_engine_mcp_access_allowed"] is False


def test_execute_agent_tool_routes_feishu_drive_search_through_mcp(monkeypatch) -> None:
    captured = {}

    def fake_mcp(context, request):
        captured["request"] = request
        return "飞书云空间已通过 CLI 搜索 1 条：制度"

    monkeypatch.setattr("app.services.tools.router.execute_feishu_mcp_tool", fake_mcp)
    result = execute_agent_tool(
        ToolContext(db=None, company_id=uuid4(), actor=BotActor(role="owner", access_scope="company")),
        ToolRequest(
            tool_name="feishu_drive_search",
            question="找制度",
            normalized_command="找制度",
            params={"query": "制度", "doc_types": ["docx", "wiki"]},
        ),
    )

    assert result.status == ToolExecutionStatus.SUCCESS
    assert result.provider == ToolProvider.FEISHU_MCP
    assert captured["request"].tool_name == "feishu_drive_search"
    assert result.metadata["mcp_provider"] == "feishu_mcp"
    assert result.metadata["execution_chain"] == ["agent_runtime", "tool_router", "tool", "mcp", "cli", "feishu"]
    assert result.metadata["sync_engine_mcp_access_allowed"] is False


def test_execute_agent_tool_routes_operational_qa_through_feishu_cli_first_provider(monkeypatch) -> None:
    calls = []

    def fake_feishu_mcp(context, request):
        calls.append(request.tool_name)
        return f"飞书实时结果：{request.tool_name}"

    monkeypatch.setattr("app.services.tools.router.execute_feishu_mcp_tool", fake_feishu_mcp)
    context = ToolContext(db=None, company_id=uuid4(), actor=BotActor(role="owner", access_scope="company"))

    requests = (
        ToolRequest(
            tool_name="bitable_qa",
            question="实时查询",
            normalized_command="实时查询",
            params={"app_token": "bascn_1"},
        ),
        ToolRequest(tool_name="task_qa", question="实时查询", normalized_command="实时查询"),
    )
    for request in requests:
        result = execute_agent_tool(
            context,
            request,
        )

        assert result.status == ToolExecutionStatus.SUCCESS
        assert result.provider == ToolProvider.FEISHU_MCP
        assert result.metadata["preferred_execution_engine"] == "lark_cli"
        assert result.metadata["realtime_bridge"] == "feishu_mcp"
        assert result.metadata["mcp_provider"] == "feishu_mcp"
        assert result.metadata["execution_chain"] == [
            "agent_runtime",
            "tool_router",
            "tool",
            "mcp",
            "cli",
            "feishu",
        ]

    assert calls == ["bitable_qa", "task_qa"]


def test_execute_agent_tool_denies_feishu_doc_fetch_without_knowledge_scope() -> None:
    result = execute_agent_tool(
        ToolContext(db=None, company_id=uuid4(), actor=BotActor(role="member", access_scope="chat")),
        ToolRequest(
            tool_name="feishu_doc_fetch",
            question="查制度",
            normalized_command="查制度",
            params={"doc": "doccn_1"},
        ),
    )

    assert result.status == ToolExecutionStatus.DENIED
    assert result.error == "data_permission_denied:knowledge:read"


def test_execute_agent_tool_denies_bitable_write_for_regular_member() -> None:
    for tool_name, params in {
        "feishu_bitable_record_create": {"fields": {"任务名称": "拜访"}},
        "feishu_bitable_record_batch_create": {"fields": ["任务名称"], "rows": [["拜访"]]},
        "feishu_bitable_record_batch_delete": {"record_id_list": ["rec_1", "rec_2"]},
        "feishu_bitable_record_batch_update": {"record_id_list": ["rec_1", "rec_2"], "patch": {"状态": "完成"}},
        "feishu_bitable_record_update": {"record_id": "rec_1", "fields": {"任务名称": "拜访"}},
        "feishu_bitable_record_upload_attachment": {
            "record_id": "rec_1",
            "field_id": "fld_attachment",
            "files": ["attachments/customer.pdf"],
        },
        "feishu_bitable_record_remove_attachment": {
            "record_id": "rec_1",
            "field_id": "fld_attachment",
            "file_tokens": ["file_1"],
        },
        "feishu_bitable_record_upsert": {"record_id": "rec_1", "fields": {"任务名称": "拜访"}},
        "feishu_bitable_record_delete": {"record_id": "rec_1"},
        "feishu_bitable_field_create": {"field": {"name": "状态", "type": "text"}},
        "feishu_bitable_field_delete": {"field_id": "fld_1"},
        "feishu_bitable_field_update": {"field_id": "fld_1", "field": {"name": "状态", "type": "text"}},
        "feishu_bitable_table_create": {"name": "客户档案", "fields": [{"name": "客户名称", "type": "text"}]},
        "feishu_bitable_table_delete": {"table_id": "tbl_1"},
        "feishu_bitable_table_update": {"table_id": "tbl_1", "name": "客户档案2026"},
        "feishu_bitable_view_create": {"view": {"name": "客户跟进视图", "type": "grid"}},
        "feishu_bitable_view_delete": {"view_id": "viw_1"},
        "feishu_bitable_view_rename": {"view_id": "viw_1", "name": "客户跟进视图2026"},
        "feishu_bitable_view_set_filter": {
            "view_id": "viw_1",
            "filter": {"logic": "and", "conditions": [["状态", "==", "跟进中"]]},
        },
        "feishu_bitable_view_set_sort": {
            "view_id": "viw_1",
            "sort": {"sort_config": [{"field": "优先级", "desc": True}]},
        },
        "feishu_bitable_view_set_group": {
            "view_id": "viw_1",
            "group": {"group_config": [{"field": "状态", "desc": False}]},
        },
        "feishu_bitable_view_set_visible_fields": {
            "view_id": "viw_1",
            "visible_fields": ["客户名称", "状态"],
        },
        "feishu_bitable_view_set_card": {
            "view_id": "viw_1",
            "card": {"cover_field": "附件字段"},
        },
        "feishu_bitable_view_set_timebar": {
            "view_id": "viw_1",
            "timebar": {"start_time": "开始时间", "end_time": "结束时间", "title": "任务名称"},
        },
    }.items():
        result = execute_agent_tool(
            ToolContext(db=None, company_id=uuid4(), actor=BotActor(role="member", access_scope="chat")),
            ToolRequest(
                tool_name=tool_name,
                question="写入记录",
                normalized_command="写入记录",
                params={"dry_run": True, "app_token": "app_1", "table_id": "tbl_1", **params},
            ),
        )

        assert result.status == ToolExecutionStatus.DENIED
        assert result.error == "data_permission_denied:bitable:write"
        assert result.metadata["supports_write"] is True


def test_execute_agent_tool_denies_calendar_write_for_regular_member() -> None:
    result = execute_agent_tool(
        ToolContext(db=None, company_id=uuid4(), actor=BotActor(role="member", access_scope="chat")),
        ToolRequest(
            tool_name="feishu_calendar_create_event",
            question="创建日程",
            normalized_command="创建日程",
            params={"dry_run": True, "summary": "经营例会"},
        ),
    )

    assert result.status == ToolExecutionStatus.DENIED
    assert result.error == "data_permission_denied:calendar:write"
    assert result.metadata["supports_write"] is True


def test_execute_agent_tool_requires_authorization_before_personal_calendar_write_dry_run(monkeypatch) -> None:
    monkeypatch.setattr("app.services.tools.config.get_tool_config", lambda *args, **kwargs: None)

    class FakeDb:
        def scalar(self, query):
            return SimpleNamespace(settings={"user_identity_authorizations": {}})

        def add(self, item):
            pass

    result = execute_agent_tool(
        ToolContext(db=FakeDb(), company_id=uuid4(), actor=BotActor(role="member", access_scope="personal", open_id="ou_1")),
        ToolRequest(
            tool_name="feishu_calendar_create_event",
            question="创建我的日程",
            normalized_command="创建我的日程",
            params={"dry_run": True, "summary": "客户跟进"},
        ),
    )

    assert result.status == ToolExecutionStatus.DENIED
    assert result.error == "user_identity_authorization_required:user_identity_bundle"
    assert result.metadata["authorization_actions"][0]["resource_type"] == "user_identity_bundle"
    assert result.metadata["write_mode"] == "dry_run"
    assert result.metadata["dry_run"] is True
    assert result.structured_result["authorization_actions"][0]["label"] == "授权个人能力包"


def test_execute_agent_tool_allows_personal_calendar_write_dry_run_after_authorization(monkeypatch) -> None:
    monkeypatch.setattr("app.services.tools.config.get_tool_config", lambda *args, **kwargs: None)

    class FakeDb:
        def scalar(self, query):
            return SimpleNamespace(
                settings={
                    "user_identity_authorizations": {
                        "user_identity_bundle": {"status": "authorized", "owner_open_id": "ou_1"},
                    }
                }
            )

        def add(self, item):
            pass

    result = execute_agent_tool(
        ToolContext(db=FakeDb(), company_id=uuid4(), actor=BotActor(role="member", access_scope="personal", open_id="ou_1")),
        ToolRequest(
            tool_name="feishu_calendar_create_event",
            question="创建我的日程",
            normalized_command="创建我的日程",
            params={"dry_run": True, "summary": "客户跟进", "calendar_id": "primary"},
        ),
    )

    assert result.status == ToolExecutionStatus.SUCCESS
    assert result.answer.startswith("Dry-run only.")
    assert "confirmation_token:" in result.answer
    assert result.metadata["user_identity_required"] is True
    assert result.metadata["required_user_identity_resources"] == ["user_identity_bundle"]
    assert result.metadata["write_mode"] == "dry_run"
    assert result.metadata["dry_run"] is True
    assert result.metadata["execution_chain"] == ["agent_runtime", "tool_router", "tool", "mcp", "cli", "feishu"]


def test_execute_agent_tool_denies_personal_calendar_write_without_dry_run_after_authorization(monkeypatch) -> None:
    monkeypatch.setattr("app.services.tools.config.get_tool_config", lambda *args, **kwargs: None)

    class FakeDb:
        def scalar(self, query):
            return SimpleNamespace(
                settings={
                    "user_identity_authorizations": {
                        "user_identity_bundle": {"status": "authorized", "owner_open_id": "ou_1"},
                    }
                }
            )

        def add(self, item):
            pass

    result = execute_agent_tool(
        ToolContext(db=FakeDb(), company_id=uuid4(), actor=BotActor(role="member", access_scope="personal", open_id="ou_1")),
        ToolRequest(
            tool_name="feishu_calendar_create_event",
            question="创建我的日程",
            normalized_command="创建我的日程",
            params={"summary": "客户跟进"},
        ),
    )

    assert result.status == ToolExecutionStatus.DENIED
    assert "requires dry_run or confirmed=true" in result.error
    assert result.metadata["policy_reason"] == "write_confirmation_required"
    assert result.metadata["write_policy_denied"] is True
    assert result.metadata["write_mode"] == "pending_confirmation"
    assert result.structured_result["data_access_denied_reason"] == result.error
    assert "confirmation_token" in result.answer


def test_execute_agent_tool_denies_im_write_for_regular_member() -> None:
    result = execute_agent_tool(
        ToolContext(db=None, company_id=uuid4(), actor=BotActor(role="member", access_scope="chat")),
        ToolRequest(
            tool_name="feishu_im_send_message",
            question="发消息",
            normalized_command="发消息",
            params={"dry_run": True, "chat_id": "oc_1", "text": "提醒"},
        ),
    )

    assert result.status == ToolExecutionStatus.DENIED
    assert result.error == "data_permission_denied:im:write"
    assert result.metadata["supports_write"] is True


def test_execute_agent_tool_writes_audit_log(monkeypatch) -> None:
    added = []

    class FakeDb:
        def add(self, item):
            added.append(item)

    monkeypatch.setattr("app.services.tools.router.execute_report_tool", lambda context, request: "报告结果")

    result = execute_agent_tool(
        ToolContext(
            db=FakeDb(),
            company_id=uuid4(),
            actor=BotActor(role="owner", access_scope="company", open_id="ou_owner"),
        ),
        ToolRequest(tool_name="company_qa", question="公司概览", normalized_command="公司概览"),
    )

    assert result.status == ToolExecutionStatus.SUCCESS
    assert len(added) == 1
    assert added[0].action == "tool.company_qa.report"
    assert added[0].actor == "ou_owner"
    assert added[0].target_type == "tool"
    assert added[0].target_id == "company_qa"
    assert added[0].payload["status"] == "success"


def test_execute_agent_tool_audits_write_confirmation_boundary(monkeypatch) -> None:
    added = []

    class FakeDb:
        def scalar(self, query):
            return None

        def add(self, item):
            added.append(item)

    result = execute_agent_tool(
        ToolContext(
            db=FakeDb(),
            company_id=uuid4(),
            actor=BotActor(role="owner", access_scope="company", open_id="ou_owner"),
        ),
        ToolRequest(
            tool_name="feishu_task_create",
            question="建任务",
            normalized_command="建任务",
            params={"dry_run": True, "summary": "跟进客户"},
        ),
    )

    assert result.status == ToolExecutionStatus.SUCCESS
    assert result.metadata["write_mode"] == "dry_run"
    assert result.metadata["dry_run"] is True
    assert result.metadata["confirmed"] is False
    assert result.metadata["has_confirmation_token"] is False
    assert result.metadata["preferred_execution_engine"] == "lark_cli"
    assert result.metadata["realtime_policy"] == "tool_mcp_cli_only"
    assert result.metadata["realtime_bridge"] == "feishu_mcp"
    assert result.metadata["mcp_provider"] == "feishu_mcp"
    assert result.metadata["api_role"] == "sync_engine_only"
    assert result.metadata["responsibility_boundary"] == {
        "tool_router": "business_capability",
        "mcp": "tool_scheduling",
        "cli": "action_execution",
        "api": "sync_engine_data_sync",
    }
    assert result.metadata["execution_chain"] == ["agent_runtime", "tool_router", "tool", "mcp", "cli", "feishu"]
    assert result.metadata["source_chain"] == ["MCP", "CLI", "Feishu"]
    assert result.execution_source == "MCP -> CLI -> Feishu"
    assert result.structured_result["response_text"].startswith("Dry-run only.")
    assert "Tool 已选择执行源: MCP -> CLI -> Feishu" in result.structured_result["response_text"]
    assert result.metadata["agent_runtime_direct_access"] is False
    assert result.metadata["sync_engine_direct_api_allowed"] is False
    assert result.metadata["sync_engine_mcp_access_allowed"] is False
    assert len(added) == 1
    assert added[0].action == "tool.task.create"
    assert added[0].payload["supports_write"] is True
    assert added[0].payload["business_tool"] == "TaskTool"
    assert added[0].payload["execution_source"] == "MCP -> CLI -> Feishu"
    assert added[0].payload["data_source"] is None
    assert added[0].payload["source_chain"] == ["MCP", "CLI", "Feishu"]
    assert added[0].payload["source_kind"] == "execution_source"
    assert added[0].payload["tool_decides_data_or_execution_source"] is True
    assert added[0].payload["tool_returns_structured_result"] is True
    assert added[0].payload["final_answer_owner"] == "agent_runtime"
    assert added[0].payload["data_permission_model"] == "identity_scoped_tighten_only"
    assert added[0].payload["data_boundary_policy"] == "tool_global_data_identity_bounded"
    assert added[0].payload["company_scope"]
    assert added[0].payload["role_scope"] == {
        "role": "owner",
        "access_scope": "company",
        "domains": [],
        "company_data_allowed": True,
    }
    assert added[0].payload["enterprise_identity"] == "app_identity"
    assert added[0].payload["enterprise_identity_boundary"] == "App Identity + Company Scope + Role Scope"
    assert added[0].payload["enterprise_identity_constraints"] == ["app_identity", "company_scope", "role_scope"]
    assert added[0].payload["can_exceed_feishu_app_permissions"] is False
    assert added[0].payload["data_boundary_enforcement"]["tool_is_global_shared"] is True
    assert added[0].payload["data_boundary_enforcement"]["digital_advisor_can_only_tighten"] is True
    assert added[0].payload["digital_advisor_permission_policy"] == "tighten_only"
    assert added[0].payload["cannot_escalate_original_permissions"] is True
    assert added[0].payload["preferred_execution_engine"] == "lark_cli"
    assert added[0].payload["realtime_policy"] == "tool_mcp_cli_only"
    assert added[0].payload["realtime_bridge"] == "feishu_mcp"
    assert added[0].payload["mcp_provider"] == "feishu_mcp"
    assert added[0].payload["api_role"] == "sync_engine_only"
    assert added[0].payload["responsibility_boundary"] == {
        "tool_router": "business_capability",
        "mcp": "tool_scheduling",
        "cli": "action_execution",
        "api": "sync_engine_data_sync",
    }
    assert added[0].payload["execution_chain"] == [
        "agent_runtime",
        "tool_router",
        "tool",
        "mcp",
        "cli",
        "feishu",
    ]
    assert added[0].payload["agent_runtime_direct_access"] is False
    assert added[0].payload["sync_engine_direct_api_allowed"] is False
    assert added[0].payload["sync_engine_mcp_access_allowed"] is False
    assert added[0].payload["write_mode"] == "dry_run"
    assert added[0].payload["dry_run"] is True
    assert added[0].payload["confirmed"] is False
    assert added[0].payload["has_confirmation_token"] is False
    assert added[0].payload["write_target"]["operation"] == "task.create"
    assert "confirmation_token" not in added[0].payload["write_target"]["param_keys"]


def test_execute_agent_tool_strips_runtime_api_params_before_feishu_provider(monkeypatch) -> None:
    captured = {}

    def fake_feishu_mcp(context, request):
        captured["params"] = dict(request.params)
        return "飞书任务已通过 CLI 创建。"

    monkeypatch.setattr("app.services.tools.router.execute_feishu_mcp_tool", fake_feishu_mcp)
    context = ToolContext(
        db=None,
        company_id=uuid4(),
        actor=BotActor(role="owner", access_scope="company", open_id="ou_owner"),
    )
    request_without_token = ToolRequest(
        tool_name="feishu_task_create",
        question="建任务",
        normalized_command="建任务",
        params={
            "confirmed": True,
            "summary": "跟进客户",
            "client": object(),
            "app_config": object(),
        },
    )
    token = feishu_write_confirmation_token(context, request_without_token)
    result = execute_agent_tool(
        context,
        ToolRequest(
            tool_name="feishu_task_create",
            question="建任务",
            normalized_command="建任务",
            params={
                "confirmed": True,
                "summary": "跟进客户",
                "client": object(),
                "app_config": object(),
                "confirmation_token": token,
            },
        ),
    )

    assert result.status == ToolExecutionStatus.SUCCESS
    assert "client" not in captured["params"]
    assert "app_config" not in captured["params"]


def test_execute_agent_tool_injects_only_trusted_cli_profile_for_feishu_mcp(monkeypatch) -> None:
    captured = {}

    def fake_feishu_mcp(context, request):
        captured["params"] = dict(request.params)
        return "飞书任务结果"

    monkeypatch.setattr("app.services.tools.router.execute_feishu_mcp_tool", fake_feishu_mcp)
    context = ToolContext(
        db=None,
        company_id=uuid4(),
        actor=BotActor(role="owner", access_scope="company"),
        cli_profile="company-gaustek",
    )

    result = execute_agent_tool(
        context,
        ToolRequest(
            tool_name="task_qa",
            question="任务",
            normalized_command="任务",
            params={"profile": "wrong-default", "cli_profile": "wrong-company", "lark_profile": "wrong-lark"},
        ),
    )

    assert result.status == ToolExecutionStatus.SUCCESS
    assert captured["params"]["cli_profile"] == "company-gaustek"
    assert "profile" not in captured["params"]
    assert "lark_profile" not in captured["params"]


def test_write_target_metadata_summarizes_bitable_task_approval_and_im_targets() -> None:
    bitable = write_target_metadata(
        "feishu_bitable_record_update",
        {
            "app_token": "app_1",
            "table_id": "tbl_1",
            "record_id": "rec_1",
            "fields": {"客户名称": "A 公司", "状态": "跟进中"},
            "confirmed": True,
            "confirmation_token": "secret-token",
        },
    )
    bitable_batch = write_target_metadata(
        "feishu_bitable_record_batch_create",
        {
            "app_token": "app_1",
            "table_id": "tbl_1",
            "fields": ["任务名称", "状态"],
            "rows": [["拜访", "待处理"], ["跟进", "处理中"]],
            "confirmation_token": "secret-token",
        },
    )
    bitable_batch_update = write_target_metadata(
        "feishu_bitable_record_batch_update",
        {
            "app_token": "app_1",
            "table_id": "tbl_1",
            "record_id_list": ["rec_1", "rec_2"],
            "patch": {"状态": "已完成"},
            "confirmation_token": "secret-token",
        },
    )
    bitable_batch_delete = write_target_metadata(
        "feishu_bitable_record_batch_delete",
        {
            "app_token": "app_1",
            "table_id": "tbl_1",
            "record_id_list": ["rec_1", "rec_2"],
            "confirmation_token": "secret-token",
        },
    )
    bitable_upsert = write_target_metadata(
        "feishu_bitable_record_upsert",
        {
            "app_token": "app_1",
            "table_id": "tbl_1",
            "record_id": "rec_1",
            "fields": {"状态": "已成交"},
            "confirmation_token": "secret-token",
        },
    )
    bitable_attachment = write_target_metadata(
        "feishu_bitable_record_upload_attachment",
        {
            "app_token": "app_1",
            "table_id": "tbl_1",
            "record_id": "rec_1",
            "field_id": "fld_attachment",
            "files": ["attachments/customer.pdf", "attachments/photo.png"],
            "confirmation_token": "secret-token",
        },
    )
    bitable_remove_attachment = write_target_metadata(
        "feishu_bitable_record_remove_attachment",
        {
            "app_token": "app_1",
            "table_id": "tbl_1",
            "record_id": "rec_1",
            "field_id": "fld_attachment",
            "file_tokens": ["file_1", "file_2"],
            "confirmation_token": "secret-token",
        },
    )
    bitable_table = write_target_metadata(
        "feishu_bitable_table_create",
        {
            "app_token": "app_1",
            "name": "客户档案",
            "fields": [{"name": "客户名称", "type": "text"}, {"name": "状态", "type": "select"}],
            "confirmation_token": "secret-token",
        },
    )
    bitable_table_update = write_target_metadata(
        "feishu_bitable_table_update",
        {
            "app_token": "app_1",
            "table_id": "tbl_1",
            "name": "客户档案2026",
            "confirmation_token": "secret-token",
        },
    )
    bitable_table_delete = write_target_metadata(
        "feishu_bitable_table_delete",
        {
            "app_token": "app_1",
            "table_id": "tbl_1",
            "confirmation_token": "secret-token",
        },
    )
    bitable_field = write_target_metadata(
        "feishu_bitable_field_create",
        {
            "app_token": "app_1",
            "table_id": "tbl_1",
            "field": {"name": "状态", "type": "select"},
            "confirmation_token": "secret-token",
        },
    )
    bitable_field_delete = write_target_metadata(
        "feishu_bitable_field_delete",
        {
            "app_token": "app_1",
            "table_id": "tbl_1",
            "field_id": "fld_1",
            "confirmation_token": "secret-token",
        },
    )
    bitable_field_update = write_target_metadata(
        "feishu_bitable_field_update",
        {
            "app_token": "app_1",
            "table_id": "tbl_1",
            "field_id": "fld_1",
            "field": {"name": "客户状态", "type": "select"},
            "confirmation_token": "secret-token",
        },
    )
    bitable_view_create = write_target_metadata(
        "feishu_bitable_view_create",
        {
            "app_token": "app_1",
            "table_id": "tbl_1",
            "view": {"name": "客户跟进视图", "type": "grid"},
            "confirmation_token": "secret-token",
        },
    )
    bitable_view_delete = write_target_metadata(
        "feishu_bitable_view_delete",
        {
            "app_token": "app_1",
            "table_id": "tbl_1",
            "view_id": "viw_1",
            "confirmation_token": "secret-token",
        },
    )
    bitable_view_rename = write_target_metadata(
        "feishu_bitable_view_rename",
        {
            "app_token": "app_1",
            "table_id": "tbl_1",
            "view_id": "viw_1",
            "name": "客户跟进视图2026",
            "confirmation_token": "secret-token",
        },
    )
    bitable_view_set_filter = write_target_metadata(
        "feishu_bitable_view_set_filter",
        {
            "app_token": "app_1",
            "table_id": "tbl_1",
            "view_id": "viw_1",
            "filter": {"logic": "and", "conditions": [["状态", "==", "跟进中"]]},
            "confirmation_token": "secret-token",
        },
    )
    bitable_view_set_sort = write_target_metadata(
        "feishu_bitable_view_set_sort",
        {
            "app_token": "app_1",
            "table_id": "tbl_1",
            "view_id": "viw_1",
            "sort": {"sort_config": [{"field": "优先级", "desc": True}]},
            "confirmation_token": "secret-token",
        },
    )
    bitable_view_set_group = write_target_metadata(
        "feishu_bitable_view_set_group",
        {
            "app_token": "app_1",
            "table_id": "tbl_1",
            "view_id": "viw_1",
            "group": {"group_config": [{"field": "状态", "desc": False}]},
            "confirmation_token": "secret-token",
        },
    )
    bitable_view_set_visible_fields = write_target_metadata(
        "feishu_bitable_view_set_visible_fields",
        {
            "app_token": "app_1",
            "table_id": "tbl_1",
            "view_id": "viw_1",
            "visible_fields": ["客户名称", "状态"],
            "confirmation_token": "secret-token",
        },
    )
    bitable_view_set_card = write_target_metadata(
        "feishu_bitable_view_set_card",
        {
            "app_token": "app_1",
            "table_id": "tbl_1",
            "view_id": "viw_1",
            "card": {"cover_field": "附件字段"},
            "confirmation_token": "secret-token",
        },
    )
    bitable_view_set_timebar = write_target_metadata(
        "feishu_bitable_view_set_timebar",
        {
            "app_token": "app_1",
            "table_id": "tbl_1",
            "view_id": "viw_1",
            "timebar": {"start_time": "开始时间", "end_time": "结束时间", "title": "任务名称"},
            "confirmation_token": "secret-token",
        },
    )
    task = write_target_metadata(
        "feishu_task_update",
        {"task_guid": "task_1", "summary": "跟进客户", "update_fields": ["summary"]},
    )
    task_delete = write_target_metadata(
        "feishu_task_delete",
        {"task_guid": "task_1", "confirmation_token": "secret-token"},
    )
    task_create = write_target_metadata(
        "feishu_task_create",
        {"summary": "跟进客户", "dry_run": True, "confirmation_token": "secret-token"},
    )
    task_subtask_create = write_target_metadata(
        "feishu_task_subtask_create",
        {
            "parent_task_guid": "parent_1",
            "summary": "准备报价单",
            "confirmation_token": "secret-token",
        },
    )
    task_assign = write_target_metadata(
        "feishu_task_assign_members",
        {
            "task_guid": "task_1",
            "add_assignees": ["ou_1", "ou_2"],
            "remove_assignees": ["ou_3"],
            "confirmation_token": "secret-token",
        },
    )
    task_followers = write_target_metadata(
        "feishu_task_update_followers",
        {
            "task_guid": "task_1",
            "add_followers": ["ou_1"],
            "remove_followers": ["ou_2"],
            "confirmation_token": "secret-token",
        },
    )
    task_reminders = write_target_metadata(
        "feishu_task_update_reminders",
        {"task_guid": "task_1", "relative_fire_minutes": [15], "confirmation_token": "secret-token"},
    )
    task_attachment = write_target_metadata(
        "feishu_task_upload_attachment",
        {
            "resource_id": "task_1",
            "resource_type": "task",
            "file_path": "attachments/customer.pdf",
            "confirmation_token": "secret-token",
        },
    )
    tasklist_create = write_target_metadata(
        "feishu_tasklist_create",
        {
            "name": "销售跟进清单",
            "editors": ["ou_1", "ou_2"],
            "archive_tasklist": True,
            "confirmation_token": "secret-token",
        },
    )
    tasklist_delete = write_target_metadata(
        "feishu_tasklist_delete",
        {"tasklist_guid": "tl_1", "confirmation_token": "secret-token"},
    )
    tasklist_update = write_target_metadata(
        "feishu_tasklist_update",
        {"tasklist_guid": "tl_1", "name": "销售跟进清单2026", "confirmation_token": "secret-token"},
    )
    tasklist_update_members = write_target_metadata(
        "feishu_tasklist_update_members",
        {
            "tasklist_guid": "tl_1",
            "add_members": ["ou_1", "ou_2"],
            "remove_members": ["ou_3"],
            "confirmation_token": "secret-token",
        },
    )
    tasklist_set_members = write_target_metadata(
        "feishu_tasklist_set_members",
        {
            "tasklist_guid": "tl_1",
            "set_members": ["ou_1", "ou_2"],
            "confirmation_token": "secret-token",
        },
    )
    task_section_create = write_target_metadata(
        "feishu_task_section_create",
        {
            "name": "销售跟进",
            "resource_type": "tasklist",
            "resource_id": "tl_1",
            "insert_after": "sec_0",
            "confirmation_token": "secret-token",
        },
    )
    task_section_update = write_target_metadata(
        "feishu_task_section_update",
        {
            "section_guid": "sec_1",
            "name": "已完成跟进",
            "update_fields": ["name"],
            "confirmation_token": "secret-token",
        },
    )
    task_section_delete = write_target_metadata(
        "feishu_task_section_delete",
        {"section_guid": "sec_1", "confirmation_token": "secret-token"},
    )
    task_add_to_tasklist = write_target_metadata(
        "feishu_task_add_to_tasklist",
        {
            "task_guid": "task_1",
            "tasklist_guid": "tl_1",
            "section_guid": "sec_1",
            "confirmation_token": "secret-token",
        },
    )
    task_set_ancestor = write_target_metadata(
        "feishu_task_set_ancestor",
        {
            "task_guid": "task_1",
            "ancestor_guid": "parent_1",
            "confirmation_token": "secret-token",
        },
    )
    task_clear_ancestor = write_target_metadata(
        "feishu_task_clear_ancestor",
        {
            "task_guid": "task_1",
            "confirmation_token": "secret-token",
        },
    )
    calendar_create = write_target_metadata(
        "feishu_calendar_create_event",
        {"summary": "经营例会", "calendar_id": "primary", "confirmation_token": "secret-token"},
    )
    approval = write_target_metadata(
        "feishu_approval_task_reject",
        {
            "item": {"approval_code": "approval_1", "instance_code": "inst_1", "task_id": "task_1"},
            "comment": "请勿泄露这段审批意见",
            "form": [{"id": "field_1", "type": "input", "value": "must-not-leak"}],
        },
    )
    approval_remind = write_target_metadata(
        "feishu_approval_instance_remind",
        {"instance_code": "inst_1", "task_ids": ["task_1", "task_2"]},
    )
    approval_cancel = write_target_metadata(
        "feishu_approval_instance_cancel",
        {"instance_code": "inst_1", "confirmation_token": "secret-token"},
    )
    approval_cc = write_target_metadata(
        "feishu_approval_instance_cc",
        {"instance_code": "inst_1", "cc_user_ids": ["ou_target"], "confirmation_token": "secret-token"},
    )
    approval_add_sign = write_target_metadata(
        "feishu_approval_task_add_sign",
        {
            "item": {"approval_code": "approval_1", "instance_code": "inst_1", "task_id": "task_1"},
            "add_sign_user_ids": ["ou_target"],
            "add_sign_type": 3,
            "approval_method": 2,
        },
    )
    approval_rollback = write_target_metadata(
        "feishu_approval_task_rollback",
        {
            "item": {"approval_code": "approval_1", "instance_code": "inst_1", "task_id": "task_1"},
            "node_ids": ["node_1", "node_2"],
        },
    )
    approval_transfer = write_target_metadata(
        "feishu_approval_task_transfer",
        {
            "item": {"approval_code": "approval_1", "instance_code": "inst_1", "task_id": "task_1"},
            "transfer_user_id": "ou_target",
        },
    )
    create_chat = write_target_metadata(
        "feishu_im_create_chat",
        {"name": "经营例会群", "user_id_list": ["ou_owner"], "confirmation_token": "secret-token"},
    )
    auto_join = write_target_metadata(
        "feishu_im_auto_join_public_chats",
        {"chat_ids": ["oc_1", "oc_2"], "query": "销售", "dry_run": True},
    )

    assert bitable["operation"] == "bitable_record.update"
    assert bitable["identifiers"] == {"app_token": "app_1", "table_id": "tbl_1", "record_id": "rec_1"}
    assert bitable["field_keys"] == ["客户名称", "状态"]
    assert "confirmation_token" not in bitable["param_keys"]
    assert bitable_batch["operation"] == "bitable_record.batch_create"
    assert bitable_batch["identifiers"] == {"app_token": "app_1", "table_id": "tbl_1"}
    assert bitable_batch["field_keys"] == ["任务名称", "状态"]
    assert bitable_batch["row_count"] == 2
    assert "confirmation_token" not in bitable_batch["param_keys"]
    assert bitable_batch_update["operation"] == "bitable_record.batch_update"
    assert bitable_batch_update["identifiers"] == {
        "app_token": "app_1",
        "table_id": "tbl_1",
        "record_ids": "rec_1,rec_2",
    }
    assert bitable_batch_update["field_keys"] == ["状态"]
    assert bitable_batch_update["record_count"] == 2
    assert "confirmation_token" not in bitable_batch_update["param_keys"]
    assert bitable_batch_delete["operation"] == "bitable_record.batch_delete"
    assert bitable_batch_delete["identifiers"] == {
        "app_token": "app_1",
        "table_id": "tbl_1",
        "record_ids": "rec_1,rec_2",
    }
    assert bitable_batch_delete["record_count"] == 2
    assert "confirmation_token" not in bitable_batch_delete["param_keys"]
    assert bitable_upsert["operation"] == "bitable_record.upsert"
    assert bitable_upsert["identifiers"] == {"app_token": "app_1", "record_id": "rec_1", "table_id": "tbl_1"}
    assert bitable_upsert["field_keys"] == ["状态"]
    assert bitable_upsert["upsert_mode"] == "update_by_record_id"
    assert "confirmation_token" not in bitable_upsert["param_keys"]
    assert bitable_attachment["operation"] == "bitable_record.upload_attachment"
    assert bitable_attachment["identifiers"] == {
        "app_token": "app_1",
        "field_id": "fld_attachment",
        "record_id": "rec_1",
        "table_id": "tbl_1",
    }
    assert bitable_attachment["file_count"] == 2
    assert bitable_attachment["file_names"] == ["customer.pdf", "photo.png"]
    attachment_summary = str(write_target_summary(bitable_attachment))
    assert "files=2" in attachment_summary
    assert "file_names=customer.pdf,photo.png" in attachment_summary
    assert "attachments/customer.pdf" not in attachment_summary
    assert "confirmation_token" not in bitable_attachment["param_keys"]
    assert bitable_remove_attachment["operation"] == "bitable_record.remove_attachment"
    assert bitable_remove_attachment["identifiers"] == {
        "app_token": "app_1",
        "field_id": "fld_attachment",
        "record_id": "rec_1",
        "table_id": "tbl_1",
    }
    assert bitable_remove_attachment["file_token_count"] == 2
    remove_attachment_summary = str(write_target_summary(bitable_remove_attachment))
    assert "file_tokens=2" in remove_attachment_summary
    assert "file_1" not in remove_attachment_summary
    assert "confirmation_token" not in bitable_remove_attachment["param_keys"]
    assert bitable_table["operation"] == "bitable_table.create"
    assert bitable_table["identifiers"] == {"app_token": "app_1", "name": "客户档案"}
    assert bitable_table["title"] == "客户档案"
    assert bitable_table["field_keys"] == ["客户名称", "状态"]
    assert "confirmation_token" not in bitable_table["param_keys"]
    assert bitable_table_update["operation"] == "bitable_table.update"
    assert bitable_table_update["identifiers"] == {
        "app_token": "app_1",
        "name": "客户档案2026",
        "table_id": "tbl_1",
    }
    assert "confirmation_token" not in bitable_table_update["param_keys"]
    assert bitable_table_delete["operation"] == "bitable_table.delete"
    assert bitable_table_delete["identifiers"] == {"app_token": "app_1", "table_id": "tbl_1"}
    assert "confirmation_token" not in bitable_table_delete["param_keys"]
    assert bitable_field["operation"] == "bitable_field.create"
    assert bitable_field["identifiers"] == {"app_token": "app_1", "table_id": "tbl_1"}
    assert bitable_field["field_keys"] == ["状态"]
    assert "confirmation_token" not in bitable_field["param_keys"]
    assert bitable_field_delete["operation"] == "bitable_field.delete"
    assert bitable_field_delete["identifiers"] == {
        "app_token": "app_1",
        "field_id": "fld_1",
        "table_id": "tbl_1",
    }
    assert "confirmation_token" not in bitable_field_delete["param_keys"]
    assert bitable_field_update["operation"] == "bitable_field.update"
    assert bitable_field_update["identifiers"] == {
        "app_token": "app_1",
        "field_id": "fld_1",
        "table_id": "tbl_1",
    }
    assert bitable_field_update["field_keys"] == ["客户状态"]
    assert "confirmation_token" not in bitable_field_update["param_keys"]
    assert bitable_view_create["operation"] == "bitable_view.create"
    assert bitable_view_create["identifiers"] == {"app_token": "app_1", "table_id": "tbl_1"}
    assert bitable_view_create["title"] == "客户跟进视图"
    assert "confirmation_token" not in bitable_view_create["param_keys"]
    assert bitable_view_delete["operation"] == "bitable_view.delete"
    assert bitable_view_delete["identifiers"] == {
        "app_token": "app_1",
        "table_id": "tbl_1",
        "view_id": "viw_1",
    }
    assert "confirmation_token" not in bitable_view_delete["param_keys"]
    assert bitable_view_rename["operation"] == "bitable_view.rename"
    assert bitable_view_rename["identifiers"] == {
        "app_token": "app_1",
        "name": "客户跟进视图2026",
        "table_id": "tbl_1",
        "view_id": "viw_1",
    }
    assert bitable_view_rename["title"] == "客户跟进视图2026"
    assert "confirmation_token" not in bitable_view_rename["param_keys"]
    assert bitable_view_set_filter["operation"] == "bitable_view.set_filter"
    assert bitable_view_set_filter["identifiers"] == {
        "app_token": "app_1",
        "table_id": "tbl_1",
        "view_id": "viw_1",
    }
    assert bitable_view_set_filter["filter_logic"] == "and"
    assert bitable_view_set_filter["condition_count"] == 1
    assert "跟进中" not in str(write_target_summary(bitable_view_set_filter))
    assert "conditions=1" in str(write_target_summary(bitable_view_set_filter))
    assert "confirmation_token" not in bitable_view_set_filter["param_keys"]
    assert bitable_view_set_sort["operation"] == "bitable_view.set_sort"
    assert bitable_view_set_sort["identifiers"] == {
        "app_token": "app_1",
        "table_id": "tbl_1",
        "view_id": "viw_1",
    }
    assert bitable_view_set_sort["sort_count"] == 1
    assert bitable_view_set_sort["sort_fields"] == ["优先级"]
    assert "sorts=1" in str(write_target_summary(bitable_view_set_sort))
    assert "sort_fields=优先级" in str(write_target_summary(bitable_view_set_sort))
    assert "confirmation_token" not in bitable_view_set_sort["param_keys"]
    assert bitable_view_set_group["operation"] == "bitable_view.set_group"
    assert bitable_view_set_group["identifiers"] == {
        "app_token": "app_1",
        "table_id": "tbl_1",
        "view_id": "viw_1",
    }
    assert bitable_view_set_group["group_count"] == 1
    assert bitable_view_set_group["group_fields"] == ["状态"]
    assert "groups=1" in str(write_target_summary(bitable_view_set_group))
    assert "group_fields=状态" in str(write_target_summary(bitable_view_set_group))
    assert "confirmation_token" not in bitable_view_set_group["param_keys"]
    assert bitable_view_set_visible_fields["operation"] == "bitable_view.set_visible_fields"
    assert bitable_view_set_visible_fields["identifiers"] == {
        "app_token": "app_1",
        "table_id": "tbl_1",
        "view_id": "viw_1",
    }
    assert bitable_view_set_visible_fields["visible_field_count"] == 2
    assert bitable_view_set_visible_fields["visible_fields"] == ["客户名称", "状态"]
    visible_summary = str(write_target_summary(bitable_view_set_visible_fields))
    assert "visible_fields=2" in visible_summary
    assert "visible_field_names=客户名称,状态" in visible_summary
    assert "confirmation_token" not in bitable_view_set_visible_fields["param_keys"]
    assert bitable_view_set_card["operation"] == "bitable_view.set_card"
    assert bitable_view_set_card["identifiers"] == {
        "app_token": "app_1",
        "table_id": "tbl_1",
        "view_id": "viw_1",
    }
    assert bitable_view_set_card["cover_field"] == "附件字段"
    assert "cover_field=附件字段" in str(write_target_summary(bitable_view_set_card))
    assert "confirmation_token" not in bitable_view_set_card["param_keys"]
    assert bitable_view_set_timebar["operation"] == "bitable_view.set_timebar"
    assert bitable_view_set_timebar["identifiers"] == {
        "app_token": "app_1",
        "table_id": "tbl_1",
        "view_id": "viw_1",
    }
    assert bitable_view_set_timebar["timebar_fields"] == {
        "start_time": "开始时间",
        "end_time": "结束时间",
        "title": "任务名称",
    }
    assert "timebar_fields=start_time:开始时间,end_time:结束时间,title:任务名称" in str(
        write_target_summary(bitable_view_set_timebar)
    )
    assert "confirmation_token" not in bitable_view_set_timebar["param_keys"]
    assert task["operation"] == "task.update"
    assert task["identifiers"] == {"task_guid": "task_1"}
    assert task["update_fields"] == ["summary"]
    assert task.get("title") is None
    assert task_delete["operation"] == "task.delete"
    assert task_delete["identifiers"] == {"task_guid": "task_1"}
    assert "confirmation_token" not in task_delete["param_keys"]
    assert task_create["operation"] == "task.create"
    assert task_create["title"] == "跟进客户"
    assert "confirmation_token" not in task_create["param_keys"]
    assert task_subtask_create["operation"] == "task.subtask_create"
    assert task_subtask_create["identifiers"] == {"parent_task_guid": "parent_1"}
    assert task_subtask_create["title"] == "准备报价单"
    assert "confirmation_token" not in task_subtask_create["param_keys"]
    assert task_assign["operation"] == "task.assign_members"
    assert task_assign["identifiers"] == {"task_guid": "task_1"}
    assert task_assign["add_assignees"] == ["ou_1", "ou_2"]
    assert task_assign["remove_assignees"] == ["ou_3"]
    assert "confirmation_token" not in task_assign["param_keys"]
    assert task_followers["operation"] == "task.update_followers"
    assert task_followers["identifiers"] == {"task_guid": "task_1"}
    assert task_followers["add_followers"] == ["ou_1"]
    assert task_followers["remove_followers"] == ["ou_2"]
    assert "confirmation_token" not in task_followers["param_keys"]
    assert task_reminders["operation"] == "task.update_reminders"
    assert task_reminders["identifiers"] == {"task_guid": "task_1"}
    assert task_reminders["relative_fire_minutes"] == [15]
    assert "confirmation_token" not in task_reminders["param_keys"]
    assert task_attachment["operation"] == "task.upload_attachment"
    assert task_attachment["identifiers"] == {"resource_id": "task_1"}
    assert task_attachment["resource_type"] == "task"
    assert task_attachment["file_name"] == "customer.pdf"
    assert "file_name=customer.pdf" in str(write_target_summary(task_attachment))
    assert "confirmation_token" not in task_attachment["param_keys"]
    assert tasklist_create["operation"] == "tasklist.create"
    assert tasklist_create["identifiers"] == {"name": "销售跟进清单"}
    assert tasklist_create["title"] == "销售跟进清单"
    assert tasklist_create["member_count"] == 2
    assert tasklist_create["archive_tasklist"] is True
    assert "archive_tasklist=true" in str(write_target_summary(tasklist_create))
    assert "confirmation_token" not in tasklist_create["param_keys"]
    assert tasklist_delete["operation"] == "tasklist.delete"
    assert tasklist_delete["identifiers"] == {"tasklist_guid": "tl_1"}
    assert "confirmation_token" not in tasklist_delete["param_keys"]
    assert tasklist_update["operation"] == "tasklist.update"
    assert tasklist_update["identifiers"] == {"tasklist_guid": "tl_1", "name": "销售跟进清单2026"}
    assert tasklist_update["title"] == "销售跟进清单2026"
    assert "confirmation_token" not in tasklist_update["param_keys"]
    assert tasklist_update_members["operation"] == "tasklist.update_members"
    assert tasklist_update_members["identifiers"] == {"tasklist_guid": "tl_1"}
    assert tasklist_update_members["add_members"] == ["ou_1", "ou_2"]
    assert tasklist_update_members["remove_members"] == ["ou_3"]
    assert "confirmation_token" not in tasklist_update_members["param_keys"]
    assert tasklist_set_members["operation"] == "tasklist.set_members"
    assert tasklist_set_members["identifiers"] == {"tasklist_guid": "tl_1"}
    assert tasklist_set_members["set_members"] == ["ou_1", "ou_2"]
    assert "confirmation_token" not in tasklist_set_members["param_keys"]
    assert task_section_create["operation"] == "task_section.create"
    assert task_section_create["identifiers"] == {"name": "销售跟进", "resource_id": "tl_1"}
    assert task_section_create["title"] == "销售跟进"
    assert task_section_create["resource_type"] == "tasklist"
    assert task_section_create["insert_after"] == "sec_0"
    assert "confirmation_token" not in task_section_create["param_keys"]
    assert "resource_type=tasklist" in str(write_target_summary(task_section_create))
    assert task_section_update["operation"] == "task_section.update"
    assert task_section_update["identifiers"] == {"section_guid": "sec_1", "name": "已完成跟进"}
    assert task_section_update["update_fields"] == ["name"]
    assert task_section_delete["operation"] == "task_section.delete"
    assert task_section_delete["identifiers"] == {"section_guid": "sec_1"}
    assert task_add_to_tasklist["operation"] == "task.add_to_tasklist"
    assert task_add_to_tasklist["identifiers"] == {
        "section_guid": "sec_1",
        "task_guid": "task_1",
        "tasklist_guid": "tl_1",
    }
    assert "confirmation_token" not in task_add_to_tasklist["param_keys"]
    assert task_set_ancestor["operation"] == "task.set_ancestor"
    assert task_set_ancestor["identifiers"] == {"ancestor_guid": "parent_1", "task_guid": "task_1"}
    assert "confirmation_token" not in task_set_ancestor["param_keys"]
    assert task_clear_ancestor["operation"] == "task.clear_ancestor"
    assert task_clear_ancestor["identifiers"] == {"task_guid": "task_1"}
    assert "confirmation_token" not in task_clear_ancestor["param_keys"]
    assert calendar_create["operation"] == "calendar.create_event"
    assert calendar_create["identifiers"] == {"calendar_id": "primary"}
    assert calendar_create["title"] == "经营例会"
    assert "confirmation_token" not in calendar_create["param_keys"]
    assert approval["operation"] == "approval_task.reject"
    assert approval["identifiers"] == {
        "approval_code": "approval_1",
        "instance_code": "inst_1",
        "task_id": "task_1",
    }
    assert approval["comment_present"] is True
    assert approval["form_count"] == 1
    assert approval["form_fields"] == ["field_1"]
    assert "must-not-leak" not in str(approval)
    assert write_target_summary(approval) == (
        "approval_task.reject / approval_code=approval_1 / instance_code=inst_1 / task_id=task_1 "
        "/ form_count=1 / form_fields=field_1 / comment_present=true"
    )
    assert approval_remind["operation"] == "approval_instance.remind"
    assert approval_remind["identifiers"] == {
        "instance_code": "inst_1",
        "task_ids": "task_1,task_2",
    }
    assert approval_cancel["operation"] == "approval_instance.cancel"
    assert approval_cancel["identifiers"] == {"instance_code": "inst_1"}
    assert "confirmation_token" not in approval_cancel["param_keys"]
    assert approval_cc["operation"] == "approval_instance.cc"
    assert approval_cc["identifiers"] == {"instance_code": "inst_1"}
    assert approval_cc["cc_user_ids"] == ["ou_target"]
    assert "confirmation_token" not in approval_cc["param_keys"]
    assert approval_add_sign["operation"] == "approval_task.add_sign"
    assert approval_add_sign["identifiers"] == {
        "approval_code": "approval_1",
        "instance_code": "inst_1",
        "task_id": "task_1",
    }
    assert approval_add_sign["add_sign_user_ids"] == ["ou_target"]
    assert approval_add_sign["add_sign_type"] == 3
    assert approval_add_sign["approval_method"] == 2
    assert approval_rollback["operation"] == "approval_task.rollback"
    assert approval_rollback["identifiers"] == {
        "approval_code": "approval_1",
        "instance_code": "inst_1",
        "node_ids": "node_1,node_2",
        "task_id": "task_1",
    }
    assert approval_transfer["operation"] == "approval_task.transfer"
    assert approval_transfer["identifiers"] == {
        "approval_code": "approval_1",
        "instance_code": "inst_1",
        "task_id": "task_1",
        "transfer_user_id": "ou_target",
    }
    assert create_chat["operation"] == "im.create_chat"
    assert create_chat["identifiers"] == {"name": "经营例会群"}
    assert "confirmation_token" not in create_chat["param_keys"]
    assert auto_join["operation"] == "im.auto_join_public_chats"
    assert auto_join["identifiers"] == {"chat_ids": "oc_1,oc_2"}


def test_all_feishu_write_capabilities_have_auditable_target_operations() -> None:
    write_tools = {
        tool_name for tool_name, capability in FEISHU_API_CAPABILITIES.items() if capability.risk == FeishuApiRisk.WRITE
    }

    assert write_tools
    for tool_name in write_tools:
        target = write_target_metadata(tool_name, {"confirmation_token": "must-not-leak"})
        assert target["operation"]
        assert target["operation"] != tool_name
        assert "confirmation_token" not in target["param_keys"]


def test_execute_agent_tool_does_not_inject_active_feishu_app_config_for_mcp_provider(monkeypatch) -> None:
    company_id = uuid4()
    override = ToolConfig(
        company_id=company_id,
        tool_name="task_qa",
        provider="feishu_mcp",
        enabled=True,
        required_permissions=["task:read"],
        supports_write=False,
        audit_action="tool.task_qa.read",
        config_json={},
    )
    captured = {}

    class FakeDb:
        def __init__(self):
            self.scalar_results = [override]
            self.added = []

        def scalar(self, query):
            return self.scalar_results.pop(0)

        def add(self, item):
            self.added.append(item)

    def fake_feishu_mcp_tool(context, request):
        captured["request"] = request
        return "飞书任务结果"

    db = FakeDb()
    monkeypatch.setattr("app.services.tools.router.execute_feishu_mcp_tool", fake_feishu_mcp_tool)

    result = execute_agent_tool(
        ToolContext(db=db, company_id=company_id, actor=BotActor(role="owner", access_scope="company")),
        ToolRequest(tool_name="task_qa", question="任务", normalized_command="任务"),
    )

    assert result.status == ToolExecutionStatus.SUCCESS
    assert result.provider == ToolProvider.FEISHU_MCP
    assert result.answer == "飞书任务结果"
    assert "app_config" not in captured["request"].params
    assert len(db.added) == 1


