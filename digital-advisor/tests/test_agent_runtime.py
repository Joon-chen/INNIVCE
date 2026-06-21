from types import SimpleNamespace
from typing import Any
from uuid import uuid4
import json

from app.services.agent.runtime import (
    _plan_context_payload,
    _query_rows_from_payload,
    agent_runtime_result_payload,
    _tool_result_execution_step,
    finalize_agent_workflow_reply_with_trace,
    answer_agent_message,
    answer_agent_message_with_trace,
    answer_route_label,
    answer_scope_label,
    with_scope_label,
)
from app.services.agent.planner import AgentPlan, AgentPlanStep
from app.services.agent.policies import BotActor, BotAnswerRoute
from app.services.tools.base import ToolExecutionStatus, ToolProvider, ToolResult, SHARED_TOOL_COUNT


def _stub_tool(monkeypatch, *, answer: str, capture: dict):
    def fake_execute_tool(context, request):
        capture["context"] = context
        capture["request"] = request
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer=answer,
            structured_result={
                "tool_name": request.tool_name,
                "response_text": answer,
                "data_source": "PostgreSQL",
                "execution_source": None,
                "final_answer_allowed": False,
                "final_answer_owner": "agent_runtime",
            },
            data_source="PostgreSQL",
            execution_source=None,
            metadata={
                "tool_decides_data_or_execution_source": True,
                "tool_returns_structured_result": True,
                "final_answer_owner": "agent_runtime",
            },
        )

    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)


def assert_thinking_answer(answer: str, *, header: str, body: str) -> None:
    assert answer.startswith(f"{header}\n思考路径：\n")
    assert "3. 调用数据层/工具：WorkEvent / Knowledge / Memory / 多 Tool 分析" in answer
    assert answer.endswith(f"\n\n{body}")


def test_query_rows_from_payload_handles_records_and_dict_fields() -> None:
    payload = {
        "records": [
            {"fields": {"名称": "项目A", "状态": "进行中", "负责人": "王小明"}},
            {"name": "项目B", "owner": "李小红"},
            ["项目C", "已完成", "赵小刚"],
        ]
    }

    assert _query_rows_from_payload(payload) == [
        ["项目A", "进行中", "王小明"],
        ["项目B"],
        ["项目C", "已完成", "赵小刚"],
    ]

    legacy_payload = {"data": {"items": [["任务-1", "owner-1"], ["任务-2", "owner-2"]]}}
    assert _query_rows_from_payload(legacy_payload) == [
        ["任务-1, owner-1"],
        ["任务-2, owner-2"],
    ]

    rows_payload = {
        "rows": [
            {"name": "任务X", "owner": "owner-x", "active": False},
            ["任务Y", "owner-y", 3],
        ]
    }
    assert _query_rows_from_payload(rows_payload) == [
        ["任务X"],
        ["任务Y, owner-y, 3"],
    ]

    data_rows_payload = {"data": {"rows": [["任务Z", "owner-z"]]}}
    assert _query_rows_from_payload(data_rows_payload) == [["任务Z, owner-z"]]


def test_query_rows_from_payload_returns_empty_for_unknown_payload_shapes() -> None:
    assert _query_rows_from_payload({}) == []
    assert _query_rows_from_payload({"meta": {"ok": True}}) == []


def test_agent_runtime_denies_owner_only_intent_before_tool_execution(monkeypatch) -> None:
    called = False

    def fake_answer(*args, **kwargs):
        nonlocal called
        called = True
        return "should not be called"

    monkeypatch.setattr("app.services.agent.runtime.answer_advisor_question", fake_answer)

    answer = answer_agent_message(
        None,
        company_id=uuid4(),
        question="打开驾驶舱",
        normalized_command="驾驶舱概览",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="chat"),
    )

    assert "这部分信息未向你开放" in answer
    assert answer.startswith("范围：当前群｜权限拦截")
    assert called is False


def test_agent_runtime_routes_domain_qa_to_domain_service(monkeypatch) -> None:
    called = {"advisor": False}
    captured = {}

    def fake_advisor(*args, **kwargs):
        called["advisor"] = True
        return "advisor"

    monkeypatch.setattr("app.services.agent.runtime.answer_advisor_question", fake_advisor)
    _stub_tool(monkeypatch, answer="业务域摘要", capture=captured)

    answer = answer_agent_message(
        None,
        company_id=uuid4(),
        question="最近财务资金有什么风险",
        normalized_command="最近财务资金有什么风险",
        chat_id="oc_group",
        actor=BotActor(role="manager", access_scope="domain", domains=("finance", "approval")),
    )

    assert_thinking_answer(answer, header="范围：授权业务域｜授权业务域问答", body="业务域摘要")
    assert called == {"advisor": False}
    assert captured["request"].tool_name == "domain_qa"
    assert captured["request"].question == "最近财务资金有什么风险"
    assert captured["context"].actor.domains == ("finance", "approval")


def test_agent_runtime_trace_records_tool_route(monkeypatch) -> None:
    captured = {}
    company_id = uuid4()
    _stub_tool(monkeypatch, answer="公司摘要", capture=captured)

    result = answer_agent_message_with_trace(
        None,
        company_id=company_id,
        question="固势现在有什么风险",
        normalized_command="固势现在有什么风险",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
    )

    assert_thinking_answer(result.answer, header="范围：指定公司｜公司级问答", body="公司摘要")
    assert result.trace.semantic_intent == "intent_rules"
    assert result.trace.steps[0].metadata["module_hint"] == "risks"
    assert result.trace.route_path == "company_qa"
    assert result.trace.route_label == "公司级问答"
    assert result.trace.reply_mode["mode_id"] == "thinking"
    assert result.trace.reply_mode["show_thinking_map"] is True
    assert result.trace.thinking_preview is not None
    assert result.trace.thinking_preview["kind"] == "thinking_flow_animation"
    assert result.trace.thinking_preview["send_before_final_answer"] is True
    assert result.trace.thinking_preview["final_answer_owner"] == "agent_runtime"
    assert [step.kind for step in result.trace.steps] == ["semantic", "route", "tool"]
    assert result.trace.steps[-1].name == "company_qa"
    assert result.trace.steps[-1].metadata["provider"] == "local"
    assert result.trace.steps[-1].metadata["data_source"] == "PostgreSQL"
    assert result.trace.steps[-1].metadata["execution_source"] is None
    assert result.trace.steps[-1].metadata["tool_returns_structured_result"] is True
    assert result.trace.steps[-1].metadata["structured_result"]["final_answer_allowed"] is False
    assert result.trace.steps[-1].metadata["final_answer_owner"] == "agent_runtime"
    payload = agent_runtime_result_payload(result)
    assert payload["trace"]["runtime_contract"]["tool_decides_data_or_execution_source"] is True
    assert payload["trace"]["runtime_contract"]["tool_returns_structured_result"] is True
    assert payload["trace"]["runtime_contract"]["final_answer_owner"] == "agent_runtime"
    assert "execution_category_source" in payload["trace"]
    assert payload["trace"]["execution_category_source"] in {"semantic", "route_fallback"}
    agent_identity = payload["trace"]["agent_identity"]
    assert agent_identity["agent_type"] == "employee_personal_agent"
    assert agent_identity["agent_id"] == f"{company_id}:owner"
    assert agent_identity["entrypoint"] == "feishu_bot"
    assert agent_identity["tool_access_policy"] == "all_business_tools_shared"
    assert agent_identity["tool_sharing_model"] == "shared_business_tools_per_employee_agent"
    assert agent_identity["shared_business_tools"] == ["ApprovalTool","KnowledgeTool","BitableTool","ChatTool","CalendarTool","MeetingTool","ReportTool","AutomationTool","PeopleTool"]
    assert agent_identity["shared_business_tool_count"] == 9
    assert agent_identity["agent_can_call_all_business_tools"] is True
    assert agent_identity["data_permission_model"] == "identity_scoped_tighten_only"
    contract = agent_identity["identity_permission_contract"]
    assert contract["agent_model"] == "per_user_personal_agent"
    assert contract["tool_model"] == "global_shared_business_tools"
    assert contract["agent_can_call_all_business_tools"] is True
    assert contract["enterprise_resource_boundary"]["can_exceed_feishu_app_permissions"] is False
    assert contract["user_resource_boundary"]["can_exceed_original_authorization"] is False
    assert contract["permission_enforcement"]["digital_advisor_can_only_tighten"] is True
    assert contract["permission_enforcement"]["can_escalate_original_permissions"] is False
    assert agent_identity["enterprise_identity_constraints"] == ["app_identity", "company_scope", "role_scope"]
    assert agent_identity["enterprise_resource_boundary"]["resource_owner"] == "feishu_custom_app_da_fei_ge"
    assert agent_identity["enterprise_resource_boundary"]["company_scope"] == str(company_id)
    assert agent_identity["enterprise_resource_boundary"]["role_scope"] == {
        "role": "owner",
        "access_scope": "company",
        "domains": ["all"],
        "company_data_allowed": True,
    }
    assert agent_identity["enterprise_resource_boundary"]["can_exceed_feishu_app_permissions"] is False
    assert agent_identity["user_identity_supported_resources"] == [
        "personal_feishu",
        "external_mail",
        "personal_dingtalk",
        "personal_wechat",
    ]
    assert agent_identity["user_resource_boundary"]["can_exceed_original_authorization"] is False
    assert agent_identity["data_boundary_policy"] == "tool_global_data_identity_bounded"
    assert agent_identity["digital_advisor_permission_policy"] == "tighten_only"
    assert agent_identity["cannot_escalate_original_permissions"] is True
    assert agent_identity["final_answer_owner"] == "agent_runtime"
    assert payload["trace"]["actor_context"]["data_access_scope"] == "company"
    assert payload["trace"]["actor_context"]["company_data_allowed"] is True
    assert payload["trace"]["actor_context"]["cross_user_data_allowed"] is True
    assert payload["trace"]["actor_context"]["enterprise_resource_boundary"]["company_scope"] == str(company_id)
    assert payload["trace"]["actor_context"]["enterprise_resource_boundary"]["resource_owner"] == "feishu_custom_app_da_fei_ge"
    assert payload["trace"]["reply_mode"]["mode_id"] == "thinking"
    assert payload["trace"]["thinking_preview"]["presentation"] == "animated_thinking_map"


def test_agent_runtime_uses_filtered_memory_context_without_trace_content(monkeypatch) -> None:
    captured = {}
    company_id = uuid4()
    _stub_tool(monkeypatch, answer="本人待办", capture={})

    class FakeScalars:
        def all(self):
            return [
                type(
                    "Fact",
                    (),
                    {
                        "fact_type": "communication_preference",
                        "subject": "user:ou_1:preference",
                        "content": "用户希望先看风险和下一步。",
                        "confidence": "high",
                        "scope": "personal",
                    },
                )()
            ]

    class FakeDb:
        def scalar(self, query):
            return None

        def scalars(self, query):
            return FakeScalars()

    def fake_rewrite_bot_answer(**kwargs):
        captured.update(kwargs)
        return kwargs["answer"]

    monkeypatch.setattr("app.services.agent.runtime.rewrite_bot_answer", fake_rewrite_bot_answer)

    result = answer_agent_message_with_trace(
        FakeDb(),
        company_id=company_id,
        question="我的待办是什么",
        normalized_command="我的待办是什么",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="personal", open_id="ou_1"),
    )

    assert "本人待办" in result.answer
    assert result.trace.memory_context["scope"] == "personal"
    assert result.trace.memory_context["user_open_id"] == "ou_1"
    assert result.trace.memory_context["fact_count"] == 1
    assert result.trace.memory_context["fact_types"] == ["communication_preference"]
    assert result.trace.memory_context["subjects"] == ["user:ou_1:preference"]
    assert result.trace.memory_context["content_in_trace"] is False
    assert "_facts" not in result.trace.memory_context
    assert "用户希望先看风险" not in str(result.trace.memory_context)
    assert "长期记忆（已按 company_id/open_id/chat_id 权限过滤）" in captured["style_override"]
    assert "用户希望先看风险和下一步" in captured["style_override"]


def test_agent_runtime_trace_records_planner_when_enabled(monkeypatch) -> None:
    captured = {}
    _stub_tool(monkeypatch, answer="公司摘要", capture=captured)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="固势现在有什么风险",
        normalized_command="固势现在有什么风险",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=2,
    )

    assert_thinking_answer(result.answer, header="范围：指定公司｜公司级问答", body="公司摘要")
    assert result.trace.execution_category == "analysis"
    assert [step.kind for step in result.trace.steps] == ["semantic", "route", "planner", "tool"]
    planner = result.trace.steps[2]
    assert planner.metadata["route_path"] == "company_qa"
    assert planner.metadata["max_steps"] == 2
    assert [step["kind"] for step in planner.metadata["steps"]] == ["guardrail", "tool"]
    assert planner.metadata["steps"][1]["metadata"]["provider"] == "report"
    assert planner.metadata["execution_category"] == "analysis"
    assert planner.metadata["execution_category_source"] in {"semantic", "route_fallback"}
    assert result.trace.steps[-1].metadata["planned"] is True


def test_agent_runtime_respects_semantic_decision_category_when_route_supports_analysis(monkeypatch) -> None:
    _stub_tool(monkeypatch, answer="审批建议", capture={})

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这笔报销该不该通过？",
        normalized_command="这笔报销该不该通过？",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
    )

    assert result.trace.execution_category == "decision"
    assert result.trace.reply_mode["mode_id"] == "thinking"
    assert result.trace.reply_mode["show_thinking_map"] is True


def test_agent_runtime_normalizes_semantic_execution_category_input(monkeypatch) -> None:
    _stub_tool(monkeypatch, answer="审批建议", capture={})

    fake_semantic = SimpleNamespace(
        route_hint="company_qa",
        module_hint="approval",
        canonical_question="这笔报销该不该通过？",
        confidence=0.99,
        source="test",
        execution_category="  Decision ",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="company_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这笔报销该不该通过？",
        normalized_command="这笔报销该不该通过？",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
    )

    assert result.trace.execution_category == "decision"
    assert result.trace.reply_mode["mode_id"] == "thinking"
    assert result.trace.reply_mode["show_thinking_map"] is True
    assert result.trace.execution_category_source == "semantic"


def test_agent_runtime_falls_back_to_route_action_category_when_semantic_category_invalid(monkeypatch) -> None:
    captured = {}
    _stub_tool(monkeypatch, answer="任务已创建", capture=captured)

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="tasks",
        canonical_question="创建一个任务",
        confidence=0.99,
        source="test",
        execution_category="invalid",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="feishu_task_create", scope="company", reason="permission_scope")

    monkeypatch.setattr(
        "app.services.agent.runtime.semantic_intent_for_question",
        lambda **_: fake_semantic,
    )
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime._preflight_write_tool_data_permission", lambda context, request: None)
    monkeypatch.setattr("app.services.agent.runtime._write_tool_policy_result", lambda *args, **kwargs: None)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="创建一个任务",
        normalized_command="创建一个任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
    )

    assert result.trace.execution_category == "action"
    assert result.trace.reply_mode["mode_id"] == "normal"
    assert result.trace.execution_category_source == "route_fallback"
    assert result.trace.steps[-1].name == "feishu_task_create"
    assert "planned" not in result.trace.steps[-1].metadata
    assert captured["request"].tool_name == "feishu_task_create"


def test_agent_runtime_routes_task_create_with_colloquial_phrase(monkeypatch) -> None:
    captured = {}
    _stub_tool(monkeypatch, answer="任务已创建", capture=captured)

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="tasks",
        canonical_question="帮我起个任务",
        confidence=0.98,
        source="test",
        execution_category="action",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="feishu_task_create", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime._preflight_write_tool_data_permission", lambda context, request: None)
    monkeypatch.setattr("app.services.agent.runtime._write_tool_policy_result", lambda *args, **kwargs: None)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="帮我起个任务",
        normalized_command="帮我起个任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        require_write_confirmation=False,
    )

    assert [step.kind for step in result.trace.steps] == ["semantic", "route", "tool"]
    assert result.trace.steps[-1].name == "feishu_task_create"
    assert captured["request"].tool_name == "feishu_task_create"
    assert result.trace.execution_category == "action"


def test_agent_runtime_routes_create_task_question_to_task_create_tool(monkeypatch) -> None:
    captured = {}
    _stub_tool(monkeypatch, answer="任务已创建", capture=captured)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="帮我创建一个任务",
        normalized_command="帮我创建一个任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        require_write_confirmation=False,
    )

    assert result.trace.route_path == "feishu_task_create"
    assert result.trace.route_label == "创建任务"
    assert result.trace.execution_category == "action"
    assert result.trace.steps[-1].name == "feishu_task_create"
    assert captured["request"].tool_name == "feishu_task_create"


def test_agent_runtime_query_approval_task_uses_query_category(monkeypatch) -> None:
    captured = {}
    _stub_tool(monkeypatch, answer="待我审批的任务：3条", capture=captured)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="请查一下需要我审批的单子",
        normalized_command="请查一下需要我审批的单子",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
    )

    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "semantic"
    assert result.trace.reply_mode["mode_id"] == "normal"
    assert result.trace.reply_mode["show_thinking_map"] is False
    assert result.trace.steps[-1].name == "feishu_approval_task_query"
    assert captured["request"].tool_name == "feishu_approval_task_query"
    payload = agent_runtime_result_payload(result)
    assert payload["trace"]["execution_category_source"] == "semantic"
    assert payload["trace"]["execution_category"] == "query"


def test_agent_runtime_planner_preflight_denial_uses_route_fallback_source(monkeypatch) -> None:
    calls: list[str] = []

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="feishu_task_create",
            max_steps=4,
            execution_category="action",
            execution_category_source="route_fallback",
            steps=(
                AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                AgentPlanStep(kind="tool", name="feishu_task_create", purpose="create task", metadata={}),
                AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
            ),
        )

    captured: dict[str, Any] = {}

    def fake_preflight(context, request):
        captured["request"] = request
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            status=ToolExecutionStatus.DENIED,
            answer="需要管理员授权",
            structured_result=None,
        )

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(tool_name=request.tool_name, provider=ToolProvider.LOCAL, answer="created")

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="tasks",
        canonical_question="创建一个任务",
        confidence=0.99,
        source="test",
        execution_category="invalid",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="feishu_task_create", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime._preflight_write_tool_data_permission", fake_preflight)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="创建一个任务",
        normalized_command="创建一个任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=4,
    )

    payload = agent_runtime_result_payload(result)
    planner_step = next(step for step in result.trace.steps if step.kind == "planner")
    tool_step = next(step for step in result.trace.steps if step.kind == "tool")

    assert calls == []
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"
    assert planner_step.metadata["execution_category_source"] == "route_fallback"
    assert tool_step.metadata["execution_category_source"] == "route_fallback"
    assert payload["trace"]["execution_category_source"] == "route_fallback"
    assert captured["request"].tool_name == "feishu_task_create"


def test_agent_runtime_planner_preflight_denial_uses_semantic_source_when_valid(monkeypatch) -> None:
    calls: list[str] = []

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="feishu_task_create",
            max_steps=4,
            execution_category="analysis",
            execution_category_source="semantic",
            steps=(
                AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                AgentPlanStep(kind="tool", name="feishu_task_create", purpose="create task", metadata={}),
                AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
            ),
        )

    captured: dict[str, Any] = {}

    def fake_preflight(context, request):
        captured["request"] = request
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            status=ToolExecutionStatus.DENIED,
            answer="需要管理员授权",
            structured_result=None,
        )

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(tool_name=request.tool_name, provider=ToolProvider.LOCAL, answer="created")

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="tasks",
        canonical_question="先看任务再创建一个任务",
        confidence=0.99,
        source="test",
        execution_category="analysis",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="feishu_task_create", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime._preflight_write_tool_data_permission", fake_preflight)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="先看任务再创建一个任务",
        normalized_command="先看任务再创建一个任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=4,
    )

    payload = agent_runtime_result_payload(result)
    planner_step = next(step for step in result.trace.steps if step.kind == "planner")
    tool_step = next(step for step in result.trace.steps if step.kind == "tool")

    assert calls == []
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "semantic"
    assert planner_step.metadata["execution_category_source"] == "semantic"
    assert tool_step.metadata["execution_category_source"] == "semantic"
    assert payload["trace"]["execution_category_source"] == "semantic"
    assert captured["request"].tool_name == "feishu_task_create"


def test_agent_runtime_planner_preflight_denial_uses_semantic_source_for_valid_categories(monkeypatch) -> None:
    def run_case(semantic_category: str) -> Any:
        calls: list[str] = []
        captured: dict[str, Any] = {}

        def fake_plan(**kwargs):
            return AgentPlan(
                route_path="feishu_task_create",
                max_steps=4,
                execution_category=semantic_category,
                execution_category_source="semantic",
                steps=(
                    AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                    AgentPlanStep(kind="tool", name="feishu_task_create", purpose="create task", metadata={}),
                    AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
                ),
            )

        def fake_preflight(context, request):
            captured["request"] = request
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.LOCAL,
                status=ToolExecutionStatus.DENIED,
                answer="需要管理员授权",
                metadata={},
            )

        def fake_execute_tool(context, request):
            calls.append(request.tool_name)
            return ToolResult(tool_name=request.tool_name, provider=ToolProvider.LOCAL, answer="created")

        fake_semantic = SimpleNamespace(
            route_hint="approval_qa",
            module_hint="tasks",
            canonical_question="先看任务再创建一个任务",
            confidence=0.99,
            source="test",
            execution_category=semantic_category,
        )

        def fake_resolve_route(*args, **kwargs):
            return BotAnswerRoute(path="feishu_task_create", scope="company", reason="permission_scope")

        monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
        monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
        monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
        monkeypatch.setattr("app.services.agent.runtime._preflight_write_tool_data_permission", fake_preflight)
        monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

        result = answer_agent_message_with_trace(
            None,
            company_id=uuid4(),
            question="先看任务再创建一个任务",
            normalized_command="先看任务再创建一个任务",
            chat_id="oc_group",
            actor=BotActor(role="owner", access_scope="company", domains=("all",)),
            planner_enabled=True,
            max_planner_steps=4,
        )

        payload = agent_runtime_result_payload(result)
        planner_step = next(step for step in result.trace.steps if step.kind == "planner")
        tool_step = next(step for step in result.trace.steps if step.kind == "tool")
        return (
            result,
            payload,
            planner_step,
            tool_step,
            calls,
            captured,
        )

    for category in ("query", "analysis", "decision", "action"):
        result, payload, planner_step, tool_step, calls, captured = run_case(category)
        assert calls == []
        assert result.trace.execution_category == category
        assert result.trace.execution_category_source == "semantic"
        assert planner_step.metadata["execution_category_source"] == "semantic"
        assert tool_step.metadata["execution_category_source"] == "semantic"
        assert payload["trace"]["execution_category_source"] == "semantic"
        assert captured["request"].tool_name == "feishu_task_create"


def test_agent_runtime_planner_preflight_denial_uses_route_fallback_for_invalid_categories(monkeypatch) -> None:
    def run_case(invalid_category: Any) -> Any:
        calls: list[str] = []
        captured: dict[str, Any] = {}

        def fake_plan(**kwargs):
            return AgentPlan(
                route_path="feishu_task_create",
                max_steps=4,
                execution_category="action",
                execution_category_source="route_fallback",
                steps=(
                    AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                    AgentPlanStep(kind="tool", name="feishu_task_create", purpose="create task", metadata={}),
                    AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
                ),
            )

        def fake_preflight(context, request):
            captured["request"] = request
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.LOCAL,
                status=ToolExecutionStatus.DENIED,
                answer="需要管理员授权",
                metadata={},
            )

        def fake_execute_tool(context, request):
            calls.append(request.tool_name)
            return ToolResult(tool_name=request.tool_name, provider=ToolProvider.LOCAL, answer="created")

        fake_semantic = SimpleNamespace(
            route_hint="approval_qa",
            module_hint="tasks",
            canonical_question="先看任务再创建一个任务",
            confidence=0.99,
            source="test",
            execution_category=invalid_category,
        )

        def fake_resolve_route(*args, **kwargs):
            return BotAnswerRoute(path="feishu_task_create", scope="company", reason="permission_scope")

        monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
        monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
        monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
        monkeypatch.setattr("app.services.agent.runtime._preflight_write_tool_data_permission", fake_preflight)
        monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

        result = answer_agent_message_with_trace(
            None,
            company_id=uuid4(),
            question="先看任务再创建一个任务",
            normalized_command="先看任务再创建一个任务",
            chat_id="oc_group",
            actor=BotActor(role="owner", access_scope="company", domains=("all",)),
            planner_enabled=True,
            max_planner_steps=4,
        )

        payload = agent_runtime_result_payload(result)
        planner_step = next(step for step in result.trace.steps if step.kind == "planner")
        tool_step = next(step for step in result.trace.steps if step.kind == "tool")
        return (
            result,
            payload,
            planner_step,
            tool_step,
            calls,
            captured,
        )

    for category in (None, "", " ", "not_a_category", 0, True, 1.2, [], {}):
        result, payload, planner_step, tool_step, calls, captured = run_case(category)
        assert calls == []
        assert result.trace.execution_category == "action"
        assert result.trace.execution_category_source == "route_fallback"
        assert planner_step.metadata["execution_category_source"] == "route_fallback"
        assert tool_step.metadata["execution_category_source"] == "route_fallback"
        assert payload["trace"]["execution_category_source"] == "route_fallback"
        assert payload["trace"]["execution_category"] == "action"
        assert captured["request"].tool_name == "feishu_task_create"


def test_agent_runtime_planner_write_policy_source_consistency(monkeypatch) -> None:
    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="feishu_task_create",
            max_steps=3,
            execution_category="analysis",
            execution_category_source="semantic",
            steps=(
                AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                AgentPlanStep(kind="tool", name="feishu_task_create", purpose="create task", metadata={}),
                AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
            ),
        )

    def fake_write_policy(*args, **kwargs):
        return {
            "status": "blocked",
            "answer": "需要公司管理员授权",
            "metadata": {
                "policy_reason": "write_disabled_for_plan",
                "required_user_identity_resources": ["user_identity_bundle"],
            },
        }

    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(tool_name=request.tool_name, provider=ToolProvider.LOCAL, answer="created")

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="tasks",
        canonical_question="创建一个任务",
        confidence=0.99,
        source="test",
        execution_category="analysis",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="feishu_task_create", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime._preflight_write_tool_data_permission", lambda context, request: None)
    monkeypatch.setattr("app.services.agent.runtime._write_tool_policy_result", fake_write_policy)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="创建一个任务",
        normalized_command="创建一个任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=3,
    )

    payload = agent_runtime_result_payload(result)
    planner_step = next(step for step in result.trace.steps if step.kind == "planner")
    tool_step = next(step for step in result.trace.steps if step.kind == "tool")

    assert calls == []
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "semantic"
    assert planner_step.metadata["execution_category_source"] == "semantic"
    assert tool_step.metadata["execution_category_source"] == "semantic"
    assert payload["trace"]["execution_category_source"] == "semantic"
    assert tool_step.metadata["plan_stop_reason"] == "write_disabled_for_plan"
    assert tool_step.metadata["policy_reason"] == "write_disabled_for_plan"
    assert tool_step.metadata["planned"] is True
    assert tool_step.metadata["required"] is True
    assert tool_step.metadata["on_error"] == "stop"
    assert tool_step.metadata["depends_on"] == []


def test_agent_runtime_planner_stops_at_middle_tool_preflight_for_multi_tool_plan(monkeypatch) -> None:
    calls: list[str] = []

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="company_qa",
            max_steps=6,
            steps=(
                AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                AgentPlanStep(kind="tool", name="company_qa", purpose="read company", metadata={}),
                AgentPlanStep(kind="tool", name="feishu_task_create", purpose="create task", metadata={}),
                AgentPlanStep(kind="tool", name="task_qa", purpose="tasks", metadata={}),
                AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
            ),
        )

    captured: dict[str, ToolResult] = {}

    def fake_preflight(context, request):
        if request.tool_name == "feishu_task_create":
            captured["preflight"] = ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.LOCAL,
                status=ToolExecutionStatus.DENIED,
                answer="需要管理员授权",
                metadata={},
            )
            return captured["preflight"]
        return None

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(tool_name=request.tool_name, provider=ToolProvider.LOCAL, answer=f"{request.tool_name} answer")

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="tasks",
        canonical_question="先看风险再创建任务",
        confidence=0.99,
        source="test",
        execution_category="invalid",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="company_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime._preflight_write_tool_data_permission", fake_preflight)
    monkeypatch.setattr("app.services.agent.runtime._write_tool_policy_result", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="先看风险再创建任务",
        normalized_command="先看风险再创建任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    payload = agent_runtime_result_payload(result)
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert calls == ["company_qa"]
    assert [step.name for step in tool_steps] == ["company_qa", "feishu_task_create"]
    assert [step.status for step in tool_steps] == ["success", "denied"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"
    assert all(step.metadata["execution_category_source"] == "route_fallback" for step in tool_steps)
    assert payload["trace"]["execution_category_source"] == "route_fallback"
    assert captured["preflight"].tool_name == "feishu_task_create"


def test_agent_runtime_planner_tool_metadata_does_not_override_plan_execution_category_source(monkeypatch) -> None:
    calls: list[str] = []
    captured: dict[str, Any] = {}

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="feishu_task_create",
            max_steps=3,
            execution_category="action",
            execution_category_source="route_fallback",
            steps=(
                AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                AgentPlanStep(kind="tool", name="feishu_task_create", purpose="create task", metadata={}),
                AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
            ),
        )

    def fake_preflight(context, request):
        captured["preflight"] = ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            status=ToolExecutionStatus.DENIED,
            answer="需要管理员授权",
            metadata={"execution_category_source": "tool_overridden"},
        )
        return captured["preflight"]

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: SimpleNamespace(
        route_hint="approval_qa",
        module_hint="tasks",
        canonical_question="创建一个任务",
        confidence=0.99,
        source="test",
        execution_category="invalid",
    ))
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", lambda *args, **kwargs: BotAnswerRoute(path="feishu_task_create", scope="company", reason="permission_scope"))
    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime._preflight_write_tool_data_permission", fake_preflight)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", lambda context, request: calls.append(request.tool_name) or ToolResult(tool_name=request.tool_name, provider=ToolProvider.LOCAL, answer="created"))

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="创建一个任务",
        normalized_command="创建一个任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=3,
    )

    payload = agent_runtime_result_payload(result)
    tool_step = next(step for step in result.trace.steps if step.kind == "tool")
    assert calls == []
    assert result.trace.execution_category_source == "route_fallback"
    assert tool_step.metadata["execution_category_source"] == "route_fallback"
    assert payload["trace"]["execution_category_source"] == "route_fallback"
    assert tool_step.metadata["execution_category_source"] != captured["preflight"].metadata["execution_category_source"]


def test_agent_runtime_plan_context_payload_records_execution_category_source_in_planner_steps(monkeypatch) -> None:
    captured_sources: list[str] = []

    def fake_plan_context_payload(**kwargs):
        captured_sources.append(kwargs.get("execution_category_source"))
        return _plan_context_payload(**kwargs)

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="company_qa",
            max_steps=5,
            execution_category="query",
            execution_category_source="semantic",
            steps=(
                AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                AgentPlanStep(kind="tool", name="company_qa", purpose="company", metadata={}),
                AgentPlanStep(kind="tool", name="task_qa", purpose="tasks", metadata={}),
                AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
            ),
        )

    def fake_execute_tool(context, request):
        return ToolResult(tool_name=request.tool_name, provider=ToolProvider.LOCAL, answer="ok")

    monkeypatch.setattr("app.services.agent.runtime._plan_context_payload", fake_plan_context_payload)
    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)
    monkeypatch.setattr("app.services.agent.runtime._preflight_write_tool_data_permission", lambda context, request: None)
    monkeypatch.setattr("app.services.agent.runtime._write_tool_policy_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "app.services.agent.runtime.semantic_intent_for_question",
        lambda **_: SimpleNamespace(
            route_hint="company_qa",
            module_hint="tasks",
            canonical_question="查询公司任务",
            confidence=0.99,
            source="test",
            execution_category="query",
        ),
    )
    monkeypatch.setattr(
        "app.services.agent.runtime.resolve_bot_route",
        lambda *args, **kwargs: BotAnswerRoute(path="company_qa", scope="company", reason="permission_scope"),
    )

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="查询公司任务",
        normalized_command="查询公司任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=5,
    )

    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert result.trace.execution_category_source == "semantic"
    assert captured_sources == ["semantic", "semantic"]
    assert len(tool_steps) == 2
    assert all(step.metadata["execution_category_source"] == "semantic" for step in tool_steps)


def test_agent_runtime_query_category_falls_back_when_semantic_value_blank(monkeypatch) -> None:
    captured = {}
    _stub_tool(monkeypatch, answer="待我审批的任务：2条", capture=captured)

    fake_semantic = SimpleNamespace(
        route_hint="feishu_approval_task_query",
        module_hint="tasks",
        canonical_question="请查一下需要我审批的单子",
        confidence=0.99,
        source="test",
        execution_category=" ",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="请查一下需要我审批的单子",
        normalized_command="请查一下需要我审批的单子",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
    )

    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"
    assert result.trace.reply_mode["mode_id"] == "normal"
    assert result.trace.reply_mode["show_thinking_map"] is False
    assert result.trace.steps[-1].name == "feishu_approval_task_query"
    assert captured["request"].tool_name == "feishu_approval_task_query"


def test_agent_runtime_executes_planned_read_tools_in_order(monkeypatch) -> None:
    calls = []

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="company_qa",
            max_steps=4,
            execution_category="analysis",
            execution_category_source="route_fallback",
            steps=(
                AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                AgentPlanStep(kind="tool", name="company_qa", purpose="company", metadata={}),
                AgentPlanStep(kind="tool", name="task_qa", purpose="tasks", metadata={}),
                AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
            ),
        )

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer=f"{request.tool_name} answer",
        )

    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="固势现在有什么风险和任务",
        normalized_command="固势现在有什么风险和任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=4,
    )

    assert calls == ["company_qa", "task_qa"]
    assert "公司级问答\ncompany_qa answer\n\n任务问答\ntask_qa answer" in result.answer
    assert [step.name for step in result.trace.steps if step.kind == "tool"] == ["company_qa", "task_qa"]
    assert all(step.metadata["planned"] is True for step in result.trace.steps if step.kind == "tool")
    assert all(
        step.metadata["execution_category"] in {"analysis", "action", "query", "decision"}
        for step in result.trace.steps
        if step.kind == "tool"
    )
    assert all(
        step.metadata["execution_category_source"] in {"semantic", "route_fallback"}
        for step in result.trace.steps if step.kind == "tool"
    )
    planner_step = next(step for step in result.trace.steps if step.kind == "planner")
    assert planner_step.metadata["execution_category"] == result.trace.execution_category
    assert planner_step.metadata["execution_category_source"] == result.trace.execution_category_source
    assert all(
        step.metadata["execution_category"] == planner_step.metadata["execution_category"]
        for step in result.trace.steps
        if step.kind == "tool"
    )
    assert all(
        step.metadata["execution_category_source"] == planner_step.metadata["execution_category_source"]
        for step in result.trace.steps
        if step.kind == "tool"
    )


def test_agent_runtime_planner_multi_tool_query_uses_thinking_mode(monkeypatch) -> None:
    calls = []

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="bitable_qa",
            max_steps=5,
            steps=(
                AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                AgentPlanStep(kind="tool", name="feishu_contact_organization_snapshot", purpose="snapshot", metadata={}),
                AgentPlanStep(kind="tool", name="feishu_bitable_table_create", purpose="table", metadata={}),
                AgentPlanStep(kind="tool", name="feishu_bitable_record_batch_create", purpose="rows", metadata={}),
            ),
        )

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer=f"{request.tool_name} answer",
        )

    fake_semantic = SimpleNamespace(
        route_hint="bitable_qa",
        module_hint="tasks",
        canonical_question="请帮我创建一张表，并把最新组织放进去",
        confidence=0.98,
        source="test",
        execution_category="query",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)
    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="请帮我创建一张表并放入最新组织",
        normalized_command="请帮我创建一张表并放入最新组织",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=5,
        require_write_confirmation=False,
    )

    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"
    assert result.trace.reply_mode["mode_id"] == "thinking"
    assert result.trace.reply_mode["show_thinking_map"] is True
    assert calls == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]


def test_agent_runtime_planner_metadata_consistent_across_trace_and_payload(monkeypatch) -> None:
    calls = []

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="company_qa",
            max_steps=4,
            execution_category="analysis",
            execution_category_source="route_fallback",
            steps=(
                AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                AgentPlanStep(kind="tool", name="company_qa", purpose="company", metadata={}),
                AgentPlanStep(kind="tool", name="task_qa", purpose="tasks", metadata={}),
                AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
            ),
        )

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer=f"{request.tool_name} answer",
        )

    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="固势现在有什么风险和任务",
        normalized_command="固势现在有什么风险和任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=4,
    )

    payload = agent_runtime_result_payload(result)
    planner_step = next(step for step in result.trace.steps if step.kind == "planner")
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]

    assert calls == ["company_qa", "task_qa"]
    assert planner_step.metadata["execution_category"] == result.trace.execution_category
    assert planner_step.metadata["execution_category_source"] == result.trace.execution_category_source
    assert payload["trace"]["execution_category"] == result.trace.execution_category
    assert payload["trace"]["execution_category_source"] == result.trace.execution_category_source
    assert all(
        step.metadata["execution_category"] == planner_step.metadata["execution_category"]
        for step in tool_steps
    )
    assert all(
        step.metadata["execution_category_source"] == planner_step.metadata["execution_category_source"]
        for step in tool_steps
    )


def test_agent_runtime_planned_tool_errors_continue_when_on_error_continue(monkeypatch) -> None:
    calls = []

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="company_qa",
            max_steps=5,
            execution_category="analysis",
            execution_category_source="route_fallback",
            steps=(
                AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                AgentPlanStep(kind="tool", name="company_qa", purpose="company", metadata={}),
                AgentPlanStep(
                    kind="tool",
                    name="task_qa",
                    purpose="tasks",
                    metadata={},
                    on_error="continue",
                ),
                AgentPlanStep(kind="tool", name="feishu_task_create", purpose="create", metadata={}),
                AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
            ),
        )

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        if request.tool_name == "task_qa":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.LOCAL,
                answer="暂时失败",
                status=ToolExecutionStatus.ERROR,
                error="boom",
            )
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer=f"{request.tool_name} answer",
        )

    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)
    monkeypatch.setattr("app.services.agent.runtime._preflight_write_tool_data_permission", lambda context, request: None)
    monkeypatch.setattr("app.services.agent.runtime._write_tool_policy_result", lambda *args, **kwargs: None)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="先看公司风险再查任务并创建任务",
        normalized_command="先看公司风险再查任务并创建任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=5,
        require_write_confirmation=False,
    )

    assert calls == ["company_qa", "task_qa", "feishu_task_create"]
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == ["company_qa", "task_qa", "feishu_task_create"]
    assert [step.status for step in tool_steps] == ["success", "error", "success"]
    assert tool_steps[1].metadata["plan_stop_reason"] == "tool_error"
    assert tool_steps[1].metadata["required"] is True
    assert tool_steps[1].metadata["on_error"] == "continue"
    assert tool_steps[1].metadata["depends_on"] == []
    assert "暂时失败" in result.answer


def test_agent_runtime_planned_tool_optional_step_does_not_stop_on_error(monkeypatch) -> None:
    calls = []

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="company_qa",
            max_steps=5,
            execution_category="analysis",
            execution_category_source="route_fallback",
            steps=(
                AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                AgentPlanStep(kind="tool", name="company_qa", purpose="company", metadata={}),
                AgentPlanStep(
                    kind="tool",
                    name="task_qa",
                    purpose="tasks",
                    metadata={},
                    required=False,
                ),
                AgentPlanStep(kind="tool", name="feishu_task_create", purpose="create", metadata={}),
                AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
            ),
        )

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        if request.tool_name == "task_qa":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.LOCAL,
                answer="暂时失败",
                status=ToolExecutionStatus.ERROR,
                error="boom",
            )
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer=f"{request.tool_name} answer",
        )

    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)
    monkeypatch.setattr("app.services.agent.runtime._preflight_write_tool_data_permission", lambda context, request: None)
    monkeypatch.setattr("app.services.agent.runtime._write_tool_policy_result", lambda *args, **kwargs: None)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="先看公司风险，再看任务，再创建任务",
        normalized_command="先看公司风险，再看任务，再创建任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=5,
        require_write_confirmation=False,
    )

    assert calls == ["company_qa", "task_qa", "feishu_task_create"]
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == ["company_qa", "task_qa", "feishu_task_create"]
    assert [step.status for step in tool_steps] == ["success", "error", "success"]
    assert tool_steps[1].metadata["plan_stop_reason"] == "tool_error"
    assert "暂时失败" in result.answer


def test_agent_runtime_planned_tool_required_step_stops_when_dependency_missing(monkeypatch) -> None:
    calls: list[str] = []

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="company_qa",
            max_steps=5,
            execution_category="analysis",
            execution_category_source="route_fallback",
            steps=(
                AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                AgentPlanStep(
                    kind="tool",
                    name="task_qa",
                    purpose="tasks",
                    metadata={},
                    depends_on=("company_qa",),
                ),
                AgentPlanStep(
                    kind="tool",
                    name="feishu_task_create",
                    purpose="create",
                    metadata={},
                    depends_on=("task_qa",),
                ),
                AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
            ),
        )

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer=f"{request.tool_name} answer",
        )

    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)
    monkeypatch.setattr("app.services.agent.runtime._preflight_write_tool_data_permission", lambda context, request: None)
    monkeypatch.setattr("app.services.agent.runtime._write_tool_policy_result", lambda *args, **kwargs: None)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="先看任务，再创建任务",
        normalized_command="先看任务，再创建任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
    )

    assert calls == []
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == ["task_qa"]
    assert [step.status for step in tool_steps] == ["skipped"]
    assert tool_steps[0].metadata["plan_stop_reason"] == "dependency_unsatisfied:company_qa"
    assert tool_steps[0].metadata["required"] is True
    assert tool_steps[0].metadata["on_error"] == "stop"
    assert tool_steps[0].metadata["depends_on"] == ["company_qa"]
    assert "task_qa未执行（依赖未满足：company_qa）" in result.answer


def test_agent_runtime_planned_tool_circular_dependency_does_not_deadlock(monkeypatch) -> None:
    calls: list[str] = []

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="company_qa",
            max_steps=5,
            execution_category="analysis",
            execution_category_source="route_fallback",
            steps=(
                AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                AgentPlanStep(
                    kind="tool",
                    name="task_qa",
                    purpose="tasks",
                    metadata={},
                    required=False,
                    depends_on=("feishu_task_create",),
                ),
                AgentPlanStep(
                    kind="tool",
                    name="feishu_task_create",
                    purpose="create task",
                    metadata={},
                    required=False,
                    depends_on=("task_qa",),
                ),
                AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
            ),
        )

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer=f"{request.tool_name} answer",
        )

    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)
    monkeypatch.setattr("app.services.agent.runtime._preflight_write_tool_data_permission", lambda context, request: None)
    monkeypatch.setattr("app.services.agent.runtime._write_tool_policy_result", lambda *args, **kwargs: None)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="循环依赖场景",
        normalized_command="循环依赖场景",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=5,
    )

    assert calls == []
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == ["task_qa", "feishu_task_create"]
    assert [step.status for step in tool_steps] == ["skipped", "skipped"]
    assert tool_steps[0].metadata["plan_stop_reason"] == "dependency_unsatisfied:feishu_task_create"
    assert tool_steps[1].metadata["plan_stop_reason"] == "dependency_unsatisfied:task_qa"
    assert tool_steps[0].metadata["required"] is False
    assert tool_steps[1].metadata["required"] is False
    assert tool_steps[0].metadata["depends_on"] == ["feishu_task_create"]
    assert tool_steps[1].metadata["depends_on"] == ["task_qa"]
    assert "task_qa未执行（依赖未满足：feishu_task_create）" in result.answer



def test_agent_runtime_planned_tool_preflight_denied_continue_when_on_error_continue(monkeypatch) -> None:
    calls: list[str] = []

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="company_qa",
            max_steps=5,
            execution_category="analysis",
            execution_category_source="route_fallback",
            steps=(
                AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                AgentPlanStep(kind="tool", name="company_qa", purpose="company", metadata={}),
                AgentPlanStep(
                    kind="tool",
                    name="feishu_task_create",
                    purpose="create task",
                    metadata={},
                    on_error="continue",
                ),
                AgentPlanStep(kind="tool", name="task_qa", purpose="tasks", metadata={}),
                AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
            ),
        )

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer=f"{request.tool_name} answer",
        )

    def fake_preflight(context, request):
        if request.tool_name == "feishu_task_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.LOCAL,
                answer="任务创建被拒绝",
                status=ToolExecutionStatus.DENIED,
                error="policy",
            )
        return None

    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)
    monkeypatch.setattr("app.services.agent.runtime._preflight_write_tool_data_permission", fake_preflight)
    monkeypatch.setattr("app.services.agent.runtime._write_tool_policy_result", lambda *args, **kwargs: None)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="先查风险，再尝试创建任务，再查任务",
        normalized_command="先查风险，再尝试创建任务，再查任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        require_write_confirmation=False,
    )

    assert calls == ["company_qa", "task_qa"]
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == ["company_qa", "feishu_task_create", "task_qa"]
    assert [step.status for step in tool_steps] == ["success", "denied", "success"]
    assert tool_steps[1].metadata["plan_stop_reason"] == "tool_denied"
    assert tool_steps[1].metadata["required"] is True
    assert tool_steps[1].metadata["on_error"] == "continue"
    assert tool_steps[1].metadata["depends_on"] == []
    assert "任务创建被拒绝" in result.answer


def test_agent_runtime_planned_tool_preflight_denied_optional_tool_continues(monkeypatch) -> None:
    calls: list[str] = []

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="company_qa",
            max_steps=5,
            execution_category="analysis",
            execution_category_source="route_fallback",
            steps=(
                AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                AgentPlanStep(kind="tool", name="company_qa", purpose="company", metadata={}),
                AgentPlanStep(
                    kind="tool",
                    name="feishu_task_create",
                    purpose="create task",
                    metadata={},
                    required=False,
                ),
                AgentPlanStep(kind="tool", name="task_qa", purpose="tasks", metadata={}),
                AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
            ),
        )

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer=f"{request.tool_name} answer",
        )

    def fake_preflight(context, request):
        if request.tool_name == "feishu_task_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.LOCAL,
                answer="任务创建被拒绝",
                status=ToolExecutionStatus.DENIED,
                error="policy",
            )
        return None

    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)
    monkeypatch.setattr("app.services.agent.runtime._preflight_write_tool_data_permission", fake_preflight)
    monkeypatch.setattr("app.services.agent.runtime._write_tool_policy_result", lambda *args, **kwargs: None)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="先查风险，再尝试创建任务，再查任务",
        normalized_command="先查风险，再尝试创建任务，再查任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        require_write_confirmation=False,
    )

    assert calls == ["company_qa", "task_qa"]
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == ["company_qa", "feishu_task_create", "task_qa"]
    assert [step.status for step in tool_steps] == ["success", "denied", "success"]
    assert tool_steps[1].metadata["plan_stop_reason"] == "tool_denied"
    assert tool_steps[1].metadata["required"] is False
    assert "任务创建被拒绝" in result.answer


def test_agent_runtime_planned_tool_write_policy_blocked_can_continue_with_on_error_continue(monkeypatch) -> None:
    calls: list[str] = []

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="company_qa",
            max_steps=5,
            execution_category="analysis",
            execution_category_source="route_fallback",
            steps=(
                AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                AgentPlanStep(kind="tool", name="company_qa", purpose="company", metadata={}),
                AgentPlanStep(
                    kind="tool",
                    name="feishu_task_create",
                    purpose="create task",
                    metadata={},
                    on_error="continue",
                ),
                AgentPlanStep(kind="tool", name="task_qa", purpose="tasks", metadata={}),
                AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
            ),
        )

    def fake_write_policy(tool_name: str, allow_write_tools: bool, require_write_confirmation: bool):
        if tool_name == "feishu_task_create":
            return {
                "status": "blocked",
                "answer": "该写工具被禁止",
                "metadata": {
                    "policy_reason": "write_disabled_for_plan",
                    "required_user_identity_resources": ["user_identity_bundle"],
                },
            }
        return None

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer=f"{request.tool_name} answer",
        )

    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime._write_tool_policy_result", fake_write_policy)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)
    monkeypatch.setattr("app.services.agent.runtime._preflight_write_tool_data_permission", lambda context, request: None)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="先查风险，再尝试创建任务，再查任务",
        normalized_command="先查风险，再尝试创建任务，再查任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        allow_write_tools=False,
        require_write_confirmation=False,
    )

    assert calls == ["company_qa", "task_qa"]
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == ["company_qa", "feishu_task_create", "task_qa"]
    assert [step.status for step in tool_steps] == ["success", "blocked", "success"]
    assert tool_steps[1].metadata["planned"] is True
    assert tool_steps[1].metadata["plan_stop_reason"] == "write_disabled_for_plan"
    assert tool_steps[1].metadata["required"] is True
    assert tool_steps[1].metadata["on_error"] == "continue"
    assert tool_steps[1].metadata["depends_on"] == []
    assert "该写工具被禁止" in result.answer


def test_agent_runtime_planned_tool_write_policy_optional_blocked_continues(monkeypatch) -> None:
    calls: list[str] = []

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="company_qa",
            max_steps=5,
            execution_category="analysis",
            execution_category_source="route_fallback",
            steps=(
                AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                AgentPlanStep(kind="tool", name="company_qa", purpose="company", metadata={}),
                AgentPlanStep(
                    kind="tool",
                    name="feishu_task_create",
                    purpose="create task",
                    metadata={},
                    required=False,
                ),
                AgentPlanStep(kind="tool", name="task_qa", purpose="tasks", metadata={}),
                AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
            ),
        )

    def fake_write_policy(tool_name: str, allow_write_tools: bool, require_write_confirmation: bool):
        if tool_name == "feishu_task_create":
            return {
                "status": "blocked",
                "answer": "该写工具被禁止",
                "metadata": {
                    "policy_reason": "write_disabled_for_plan",
                    "required_user_identity_resources": ["user_identity_bundle"],
                },
            }
        return None

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer=f"{request.tool_name} answer",
        )

    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime._write_tool_policy_result", fake_write_policy)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)
    monkeypatch.setattr("app.services.agent.runtime._preflight_write_tool_data_permission", lambda context, request: None)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="先查风险，再尝试创建任务，再查任务",
        normalized_command="先查风险，再尝试创建任务，再查任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        allow_write_tools=False,
        require_write_confirmation=False,
    )

    assert calls == ["company_qa", "task_qa"]
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == ["company_qa", "feishu_task_create", "task_qa"]
    assert [step.status for step in tool_steps] == ["success", "blocked", "success"]
    assert tool_steps[1].metadata["planned"] is True
    assert tool_steps[1].metadata["plan_stop_reason"] == "write_disabled_for_plan"
    assert tool_steps[1].metadata["required"] is False
    assert tool_steps[1].metadata["on_error"] == "stop"
    assert tool_steps[1].metadata["depends_on"] == []
    assert "该写工具被禁止" in result.answer


def test_agent_runtime_planned_tool_write_policy_continue_preserves_step_order(monkeypatch) -> None:
    calls: list[str] = []

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="company_qa",
            max_steps=5,
            execution_category="analysis",
            execution_category_source="route_fallback",
            steps=(
                AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                AgentPlanStep(kind="tool", name="company_qa", purpose="company", metadata={}),
                AgentPlanStep(
                    kind="tool",
                    name="feishu_task_create",
                    purpose="create task",
                    metadata={},
                    on_error="continue",
                ),
                AgentPlanStep(kind="tool", name="task_qa", purpose="tasks", metadata={}),
                AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
            ),
        )

    def fake_write_policy(tool_name: str, allow_write_tools: bool, require_write_confirmation: bool):
        if tool_name == "feishu_task_create":
            return {
                "status": "blocked",
                "answer": "该写工具被禁止",
                "metadata": {
                    "policy_reason": "write_disabled_for_plan",
                    "required_user_identity_resources": ["user_identity_bundle"],
                },
            }
        return None

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer=f"{request.tool_name} answer",
        )

    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime._write_tool_policy_result", fake_write_policy)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)
    monkeypatch.setattr("app.services.agent.runtime._preflight_write_tool_data_permission", lambda context, request: None)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="先查风险，再尝试创建任务，再查任务",
        normalized_command="先查风险，再尝试创建任务，再查任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        allow_write_tools=False,
        require_write_confirmation=False,
    )

    assert calls == ["company_qa", "task_qa"]
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == ["company_qa", "feishu_task_create", "task_qa"]
    assert [step.status for step in tool_steps] == ["success", "blocked", "success"]
    assert tool_steps[1].metadata["planned"] is True
    assert tool_steps[1].metadata["required"] is True
    assert tool_steps[1].metadata["on_error"] == "continue"
    assert "该写工具被禁止" in result.answer


def test_agent_runtime_planned_tool_dependency_chain_skips_optional_then_continues_required(monkeypatch) -> None:
    calls: list[str] = []

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="company_qa",
            max_steps=6,
            execution_category="analysis",
            execution_category_source="route_fallback",
            steps=(
                AgentPlanStep(kind="tool", name="company_qa", purpose="company", metadata={}),
                AgentPlanStep(
                    kind="tool",
                    name="feishu_task_create",
                    purpose="create task",
                    metadata={},
                    required=False,
                    depends_on=("nonexistent_step",),
                ),
                AgentPlanStep(kind="tool", name="task_qa", purpose="tasks", metadata={}),
                AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
            ),
        )

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer=f"{request.tool_name} answer",
        )

    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime._preflight_write_tool_data_permission", lambda context, request: None)
    monkeypatch.setattr("app.services.agent.runtime._write_tool_policy_result", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="先看公司，再尝试创建任务（依赖缺失），再查任务",
        normalized_command="先看公司，再尝试创建任务（依赖缺失），再查任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
    )

    assert calls == ["company_qa", "task_qa"]
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == ["company_qa", "feishu_task_create", "task_qa"]
    assert [step.status for step in tool_steps] == ["success", "skipped", "success"]
    assert tool_steps[1].metadata["plan_stop_reason"] == "dependency_unsatisfied:nonexistent_step"
    assert tool_steps[1].metadata["required"] is False
    assert tool_steps[1].metadata["depends_on"] == ["nonexistent_step"]
    assert "feishu_task_create未执行（依赖未满足：nonexistent_step）" in result.answer


def test_agent_runtime_planned_tool_dependency_missing_required_stops_following_steps(monkeypatch) -> None:
    calls: list[str] = []

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="company_qa",
            max_steps=5,
            execution_category="analysis",
            execution_category_source="route_fallback",
            steps=(
                AgentPlanStep(
                    kind="tool",
                    name="feishu_task_create",
                    purpose="create task",
                    metadata={},
                    depends_on=("nonexistent_step",),
                ),
                AgentPlanStep(kind="tool", name="task_qa", purpose="tasks", metadata={}),
                AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
            ),
        )

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer=f"{request.tool_name} answer",
        )

    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime._preflight_write_tool_data_permission", lambda context, request: None)
    monkeypatch.setattr("app.services.agent.runtime._write_tool_policy_result", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="尝试创建任务，再查任务",
        normalized_command="尝试创建任务，再查任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
    )

    assert calls == []
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == ["feishu_task_create"]
    assert [step.status for step in tool_steps] == ["skipped"]
    assert tool_steps[0].metadata["required"] is True
    assert tool_steps[0].metadata["depends_on"] == ["nonexistent_step"]
    assert tool_steps[0].metadata["plan_stop_reason"] == "dependency_unsatisfied:nonexistent_step"
    assert "feishu_task_create未执行（依赖未满足：nonexistent_step）" in result.answer


def test_agent_runtime_planned_tool_error_can_continue_with_on_error_continue(monkeypatch) -> None:
    calls: list[str] = []

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="company_qa",
            max_steps=6,
            execution_category="analysis",
            execution_category_source="route_fallback",
            steps=(
                AgentPlanStep(
                    kind="tool",
                    name="feishu_contact_organization_snapshot",
                    purpose="snapshot",
                    metadata={},
                ),
                AgentPlanStep(
                    kind="tool",
                    name="feishu_bitable_table_create",
                    purpose="create table",
                    metadata={},
                ),
                AgentPlanStep(
                    kind="tool",
                    name="feishu_bitable_record_batch_create",
                    purpose="write rows",
                    metadata={},
                    on_error="continue",
                ),
                AgentPlanStep(kind="tool", name="task_qa", purpose="tasks", metadata={}),
            ),
        )

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        if request.tool_name == "feishu_bitable_record_batch_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.LOCAL,
                answer="写入失败",
                status=ToolExecutionStatus.ERROR,
                error="boom",
            )
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer=f"{request.tool_name} answer",
        )

    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)
    monkeypatch.setattr("app.services.agent.runtime._preflight_write_tool_data_permission", lambda context, request: None)
    monkeypatch.setattr("app.services.agent.runtime._write_tool_policy_result", lambda *args, **kwargs: None)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="组织快照建表写入再查任务",
        normalized_command="组织快照建表写入再查任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
        require_write_confirmation=False,
    )

    assert calls == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
        "task_qa",
    ]
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
        "task_qa",
    ]
    assert [step.status for step in tool_steps] == ["success", "success", "error", "success"]
    assert tool_steps[2].metadata["plan_stop_reason"] == "tool_error"
    assert tool_steps[2].metadata["required"] is True
    assert tool_steps[2].metadata["on_error"] == "continue"
    assert tool_steps[0].metadata["required"] is True
    assert tool_steps[1].metadata["required"] is True
    assert tool_steps[0].metadata["depends_on"] == []
    assert tool_steps[1].metadata["depends_on"] == []
    assert tool_steps[2].metadata["depends_on"] == []
    assert tool_steps[3].metadata["required"] is True
    assert tool_steps[3].metadata["depends_on"] == []
    assert tool_steps[3].metadata["on_error"] == "stop"
    assert "写入失败" in result.answer


def test_agent_runtime_planned_tool_skips_when_dependency_missing(monkeypatch) -> None:
    calls = []

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="task_qa",
            max_steps=5,
            execution_category="analysis",
            execution_category_source="route_fallback",
            steps=(
                AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                AgentPlanStep(
                    kind="tool",
                    name="task_qa",
                    purpose="tasks",
                    metadata={},
                    required=False,
                    depends_on=("company_qa",),
                ),
                AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
            ),
        )

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(tool_name=request.tool_name, provider=ToolProvider.LOCAL, answer="should not happen")

    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)
    monkeypatch.setattr("app.services.agent.runtime._preflight_write_tool_data_permission", lambda context, request: None)
    monkeypatch.setattr("app.services.agent.runtime._write_tool_policy_result", lambda *args, **kwargs: None)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="查询任务",
        normalized_command="查询任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=5,
    )

    assert calls == []
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == ["task_qa"]
    assert [step.status for step in tool_steps] == ["skipped"]
    assert tool_steps[0].metadata["plan_stop_reason"] == "dependency_unsatisfied:company_qa"
    assert "task_qa未执行（依赖未满足：company_qa）" in result.answer


def test_agent_runtime_planned_tool_skips_when_multiple_dependencies_missing(monkeypatch) -> None:
    calls = []

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="task_qa",
            max_steps=5,
            execution_category="analysis",
            execution_category_source="route_fallback",
            steps=(
                AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                AgentPlanStep(
                    kind="tool",
                    name="task_qa",
                    purpose="tasks",
                    metadata={},
                    required=False,
                    depends_on=("company_qa", "feishu_task_create"),
                ),
                AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
            ),
        )

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(tool_name=request.tool_name, provider=ToolProvider.LOCAL, answer="should not happen")

    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)
    monkeypatch.setattr("app.services.agent.runtime._preflight_write_tool_data_permission", lambda context, request: None)
    monkeypatch.setattr("app.services.agent.runtime._write_tool_policy_result", lambda *args, **kwargs: None)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="查询任务",
        normalized_command="查询任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=5,
    )

    assert calls == []
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == ["task_qa"]
    assert [step.status for step in tool_steps] == ["skipped"]
    assert tool_steps[0].metadata["plan_stop_reason"] == "dependency_unsatisfied:company_qa,feishu_task_create"
    assert "task_qa未执行（依赖未满足：company_qa,feishu_task_create）" in result.answer


def test_agent_runtime_planner_fallback_category_consistent_across_payload(monkeypatch) -> None:
    calls = []

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="company_qa",
            max_steps=4,
            steps=(
                AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                AgentPlanStep(kind="tool", name="company_qa", purpose="company", metadata={}),
                AgentPlanStep(kind="tool", name="task_qa", purpose="tasks", metadata={}),
                AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
            ),
        )

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer=f"{request.tool_name} answer",
        )

    fake_semantic = SimpleNamespace(
        route_hint="company_qa",
        module_hint="tasks",
        canonical_question="固势现在有什么风险和任务",
        confidence=0.6,
        source="test",
        execution_category="not_a_category",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="company_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="固势现在有什么风险和任务",
        normalized_command="固势现在有什么风险和任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=4,
    )

    payload = agent_runtime_result_payload(result)
    planner_step = next(step for step in result.trace.steps if step.kind == "planner")
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]

    assert calls == ["company_qa", "task_qa"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"
    assert planner_step.metadata["execution_category_source"] == "route_fallback"
    assert payload["trace"]["execution_category_source"] == "route_fallback"
    assert all(
        step.metadata["execution_category_source"] == "route_fallback"
        for step in tool_steps
    )


def test_finalize_agent_workflow_reply_with_trace_records_execution_category_source(monkeypatch) -> None:
    class Intent:
        route_hint = "approval_qa"
        module_hint = "tasks"
        canonical_question = "查询我需要审批的任务"
        confidence = 0.52
        source = "workflow"
        execution_category = "invalid"

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: Intent())

    result = finalize_agent_workflow_reply_with_trace(
        None,
        company_id=uuid4(),
        question="查询我需要审批的任务",
        normalized_command="查询我需要审批的任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        route_path="feishu_approval_task_query",
        raw_answer="原始待办内容",
        workflow_name="approval_workflow",
        workflow_status="success",
        workflow_metadata={"custom": "metadata"},
    )

    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"
    assert result.trace.reply_mode["mode_id"] == "normal"
    assert result.trace.steps[1].name == "approval_workflow"
    payload = agent_runtime_result_payload(result)
    assert payload["trace"]["execution_category"] == "action"
    assert payload["trace"]["execution_category_source"] == "route_fallback"


def test_agent_runtime_stops_planned_write_tool_at_confirmation_gate(monkeypatch) -> None:
    calls = []

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="company_qa",
            max_steps=4,
            steps=(
                AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                AgentPlanStep(kind="tool", name="company_qa", purpose="company", metadata={}),
                AgentPlanStep(kind="tool", name="feishu_task_create", purpose="create task", metadata={}),
                AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
            ),
        )

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(tool_name=request.tool_name, provider=ToolProvider.LOCAL, answer="公司摘要")

    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="先看风险，再创建任务",
        normalized_command="先看风险，再创建任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        require_write_confirmation=True,
    )

    assert calls == ["company_qa"]
    assert "必须先执行 dry-run" in result.answer
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == ["company_qa", "feishu_task_create"]
    assert [step.status for step in tool_steps] == ["success", "pending_confirmation"]
    assert tool_steps[-1].metadata["planned"] is True
    assert tool_steps[-1].metadata["policy_reason"] == "write_confirmation_required"
    assert tool_steps[-1].metadata["plan_stop_reason"] == "write_confirmation_required"
    assert tool_steps[-1].metadata["required"] is True
    assert tool_steps[-1].metadata["on_error"] == "stop"
    assert tool_steps[-1].metadata["depends_on"] == []


def test_agent_runtime_stops_planned_chain_when_tool_errors(monkeypatch) -> None:
    calls = []

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="company_qa",
            max_steps=4,
            steps=(
                AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                AgentPlanStep(kind="tool", name="company_qa", purpose="company", metadata={}),
                AgentPlanStep(kind="tool", name="task_qa", purpose="tasks", metadata={}),
                AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
            ),
        )

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="这个工具执行失败，我已经记录错误，稍后可以在后台查看。",
            status=ToolExecutionStatus.ERROR,
            error="boom",
        )

    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="先看风险，再看任务",
        normalized_command="先看风险，再看任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
    )

    assert calls == ["company_qa"]
    assert "这个工具执行失败" in result.answer
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == ["company_qa"]
    assert tool_steps[0].status == "error"
    assert tool_steps[0].metadata["planned"] is True
    assert tool_steps[0].metadata["plan_stop_reason"] == "tool_error"
    assert tool_steps[0].metadata["required"] is True
    assert tool_steps[0].metadata["on_error"] == "stop"
    assert tool_steps[0].metadata["depends_on"] == []


def test_agent_runtime_planned_organization_workflow_uses_shared_context_and_retry_once(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []
    plan_state = {"retry_done": False}

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="bitable_qa",
            max_steps=5,
            steps=(
                AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                AgentPlanStep(
                    kind="tool",
                    name="feishu_contact_organization_snapshot",
                    purpose="snapshot",
                    metadata={
                        "tool_params": {
                            "max_departments": 100,
                            "max_users": 10,
                            "response_format": "raw_json",
                        },
                        "plan_context_keys": ["app_token", "organization_rows", "users", "departments"],
                    },
                ),
                AgentPlanStep(
                    kind="tool",
                    name="feishu_bitable_table_create",
                    purpose="create table",
                    metadata={
                        "tool_params": {
                            "app_token": "${shared.app_token}",
                            "name": "最新组织快照",
                            "fields": [
                                {"name": "部门", "type": "text"},
                                {"name": "用户", "type": "text"},
                                {"name": "直属上级", "type": "text"},
                            ],
                            "response_format": "raw_json",
                        },
                        "plan_context_keys": ["table_id"],
                    },
                ),
                AgentPlanStep(
                    kind="tool",
                    name="feishu_bitable_record_batch_create",
                    purpose="write rows",
                    metadata={
                        "tool_params": {
                            "app_token": "${shared.app_token}",
                            "table_id": "${shared.table_id}",
                            "rows": "${shared.organization_rows}",
                            "fields": ["部门", "用户", "直属上级"],
                            "response_format": "raw_json",
                        },
                    },
                ),
                AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
            ),
        )

    def fake_execute_tool(context, request):
        calls.append({"name": request.tool_name, "params": dict(request.params)})
        if request.tool_name == "feishu_contact_organization_snapshot":
            payload = {
                "app_token": "bascn-ctx-demo",
                "users": [
                    {"name": "张三", "department_names": ["研发部"], "leader_name": "李四"},
                    {"name": "王五", "department_names": ["运营部"], "manager_name": "赵六"},
                ],
            }
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MCP,
                answer=json.dumps(payload),
                structured_result={"response_text": json.dumps(payload)},
            )
        if request.tool_name == "feishu_bitable_table_create":
            if not plan_state["retry_done"]:
                plan_state["retry_done"] = True
                return ToolResult(
                    tool_name=request.tool_name,
                    provider=ToolProvider.FEISHU_MCP,
                    answer="connection timeout, please retry",
                    status=ToolExecutionStatus.ERROR,
                    error="connection timeout",
                )
            payload = {"table_id": "tbl_001"}
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MCP,
                answer=json.dumps(payload),
                structured_result={"response_text": json.dumps(payload)},
            )
        if request.tool_name == "feishu_bitable_record_batch_create":
            payload = {"created": 2}
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MCP,
                answer=json.dumps(payload),
                structured_result={"response_text": json.dumps(payload)},
            )
        raise AssertionError(f"unexpected tool {request.tool_name}")

    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="创建一张表格并把最新组织放进去",
        normalized_command="创建一张表格并把最新组织放进去",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        require_write_confirmation=False,
    )

    assert [item["name"] for item in calls] == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert calls[1]["params"]["app_token"] in {"", "bascn-ctx-demo"}
    assert calls[2]["params"]["app_token"] in {"", "bascn-ctx-demo"}
    assert calls[2]["params"].get("table_id") in {None, "tbl_001"}
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert tool_steps[0].metadata["plan_retry_count"] == 0
    assert tool_steps[1].metadata["plan_retry_count"] == 1
    assert tool_steps[1].metadata["plan_step_status"] == "success"
    assert tool_steps[2].metadata["plan_retry_count"] == 0
    assert tool_steps[1].metadata["plan_context"]["shared"]["table_id"] == "tbl_001"
    assert tool_steps[1].metadata["plan_context"]["shared"]["app_token"] == "bascn-ctx-demo"
    assert tool_steps[2].metadata["plan_context"]["shared"]["organization_rows"] == [
        ["研发部", "张三", "李四"],
        ["运营部", "王五", "赵六"],
    ]


def test_agent_runtime_organization_template_populates_shared_app_token_for_downstream_steps(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_execute_tool(context, request):
        calls.append({"name": request.tool_name, "params": dict(request.params)})
        if request.tool_name == "feishu_contact_organization_snapshot":
            payload = {
                "app_token": "bascn-template",
                "users": [
                    {"name": "张三", "department_names": ["研发部"], "leader": "李四"},
                ],
            }
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps(payload),
                structured_result={"response_text": json.dumps(payload)},
            )
        if request.tool_name == "feishu_bitable_table_create":
            assert request.params["app_token"] == "bascn-template"
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"table_id": "tbl-001"}),
                structured_result={"response_text": json.dumps({"table_id": "tbl-001"})},
            )
        if request.tool_name == "feishu_bitable_record_batch_create":
            assert request.params["app_token"] == "bascn-template"
            assert request.params["table_id"] == "tbl-001"
            assert request.params["rows"] == [["研发部", "张三", "李四"]]
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"created": 1}),
                structured_result={"response_text": json.dumps({"created": 1})},
            )
        raise AssertionError(f"unexpected tool {request.tool_name}")

    monkeypatch.setattr(
        "app.services.agent.runtime.semantic_intent_for_question",
        lambda **_: SimpleNamespace(
            route_hint="bitable_qa",
            module_hint="bitable",
            canonical_question="创建一张表并把最新组织放进去",
            source="test",
            confidence=0.95,
        ),
    )
    monkeypatch.setattr(
        "app.services.agent.runtime.resolve_bot_route",
        lambda *args, **kwargs: BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope"),
    )
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="创建一张表并把最新组织放进去",
        normalized_command="创建一张表并把最新组织放进去",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        require_write_confirmation=False,
    )

    assert [item["name"] for item in calls] == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert all(
        step.metadata["execution_category_source"] == result.trace.execution_category_source
        for step in tool_steps
    )
    assert tool_steps[1].metadata["plan_context"]["shared"]["app_token"] == "bascn-template"
    assert tool_steps[2].metadata["plan_context"]["shared"]["app_token"] == "bascn-template"


def test_agent_runtime_organization_template_ji_biao_synonym_uses_planner(monkeypatch) -> None:
    captured = {}
    calls: list[dict[str, Any]] = []

    def fake_execute_tool(context, request):
        calls.append({"name": request.tool_name, "params": dict(request.params)})
        if request.tool_name == "feishu_contact_organization_snapshot":
            payload = {
                "app_token": "bascn-template2",
                "users": [
                    {"name": "小王", "department_names": ["研发部"], "leader_name": "王总"},
                ],
            }
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps(payload),
                structured_result={"response_text": json.dumps(payload)},
            )
        if request.tool_name == "feishu_bitable_table_create":
            captured["table_request"] = dict(request.params)
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"table_id": "tbl-template2"}),
                structured_result={"response_text": json.dumps({"table_id": "tbl-template2"})},
            )
        if request.tool_name == "feishu_bitable_record_batch_create":
            captured["record_request"] = dict(request.params)
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"created": 1}),
                structured_result={"response_text": json.dumps({"created": 1})},
            )
        raise AssertionError(f"unexpected tool {request.tool_name}")

    monkeypatch.setattr(
        "app.services.agent.runtime.semantic_intent_for_question",
        lambda **_: SimpleNamespace(
            route_hint="bitable_qa",
            module_hint="bitable",
            canonical_question="帮我把组织建表，并把最新组织写入",
            source="test",
            confidence=0.95,
            execution_category="query",
        ),
    )
    monkeypatch.setattr(
        "app.services.agent.runtime.resolve_bot_route",
        lambda *args, **kwargs: BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope"),
    )
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="帮我把组织建表，并把最新组织写入",
        normalized_command="帮我把组织建表，并把最新组织写入",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        require_write_confirmation=False,
    )

    assert result.trace.execution_category == "query"
    assert [item["name"] for item in calls] == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert captured["table_request"]["app_token"] == "bascn-template2"
    assert captured["table_request"]["name"] == "最新组织快照"
    assert captured["record_request"]["app_token"] == "bascn-template2"
    assert captured["record_request"]["table_id"] == "tbl-template2"
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.metadata["execution_category_source"] for step in tool_steps] == [
        result.trace.execution_category_source,
        result.trace.execution_category_source,
        result.trace.execution_category_source,
    ]


def test_agent_runtime_plan_context_falls_back_to_request_params(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_plan(**kwargs):
        return AgentPlan(
            route_path="feishu_bitable_record_batch_create",
            max_steps=3,
            execution_category="analysis",
            execution_category_source="route_fallback",
            steps=(
                AgentPlanStep(kind="guardrail", name="permission_scope", purpose="scope", metadata={}),
                AgentPlanStep(
                    kind="tool",
                    name="feishu_bitable_record_batch_create",
                    purpose="write rows",
                    metadata={
                        "tool_params": {
                            "app_token": "bascn-fallback",
                            "table_id": "tbl-request",
                            "rows": [["A", "B", "C"]],
                            "fields": ["部门", "用户", "直属上级"],
                            "response_format": "raw_json",
                        },
                        "plan_context_keys": ["app_token", "table_id", "organization_rows"],
                    },
                ),
                AgentPlanStep(kind="answer", name="finalize_answer", purpose="answer", metadata={}),
            ),
        )

    def fake_execute_tool(context, request):
        calls.append({"name": request.tool_name, "params": dict(request.params)})
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.FEISHU_MCP,
            answer="{\"status\": \"ok\"}",
            structured_result={"response_text": "{\"status\": \"ok\"}"},
        )

    monkeypatch.setattr(
        "app.services.agent.runtime.semantic_intent_for_question",
        lambda **_: SimpleNamespace(
            route_hint="bitable_qa",
            module_hint="bitable",
            canonical_question="写入记录",
            source="test",
            confidence=0.95,
        ),
    )
    monkeypatch.setattr(
        "app.services.agent.runtime.resolve_bot_route",
        lambda *args, **kwargs: BotAnswerRoute(path="feishu_bitable_record_batch_create", scope="company", reason="permission_scope"),
    )
    monkeypatch.setattr("app.services.agent.runtime.build_agent_plan", fake_plan)
    monkeypatch.setattr("app.services.agent.runtime._preflight_write_tool_data_permission", lambda context, request: None)
    monkeypatch.setattr("app.services.agent.runtime._write_tool_policy_result", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="写入记录",
        normalized_command="写入记录",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        require_write_confirmation=False,
    )

    assert [item["name"] for item in calls] == ["feishu_bitable_record_batch_create"]
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps[0].metadata["plan_context"]["shared"]["table_id"] == "tbl-request"
    assert tool_steps[0].metadata["plan_context"]["shared"]["app_token"] == "bascn-fallback"


def test_agent_runtime_organization_template_preserves_analysis_category_from_semantic(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_execute_tool(context, request):
        calls.append({"name": request.tool_name, "params": dict(request.params)})
        if request.tool_name == "feishu_contact_organization_snapshot":
            payload = {
                "app_token": "bascn-analysis",
                "users": [
                    {"name": "阿明", "department_names": ["风控部"], "leader_name": "李经理"},
                ],
            }
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps(payload),
                structured_result={"response_text": json.dumps(payload)},
            )
        if request.tool_name == "feishu_bitable_table_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"table_id": "tbl-analysis"}),
                structured_result={"response_text": json.dumps({"table_id": "tbl-analysis"})},
            )
        if request.tool_name == "feishu_bitable_record_batch_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"created": 1}),
                structured_result={"response_text": json.dumps({"created": 1})},
            )
        raise AssertionError(f"unexpected tool {request.tool_name}")

    monkeypatch.setattr(
        "app.services.agent.runtime.semantic_intent_for_question",
        lambda **_: SimpleNamespace(
            route_hint="bitable_qa",
            module_hint="bitable",
            canonical_question="帮我把组织信息做一份表并写入系统",
            source="test",
            confidence=0.96,
            execution_category="analysis",
        ),
    )
    monkeypatch.setattr(
        "app.services.agent.runtime.resolve_bot_route",
        lambda *args, **kwargs: BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope"),
    )
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="帮我把组织信息做一份表并写入系统",
        normalized_command="帮我把组织信息做一份表并写入系统",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        require_write_confirmation=False,
    )

    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "semantic"
    assert result.trace.reply_mode["mode_id"] in {"thinking", "normal"}
    assert [item["name"] for item in calls] == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert len(tool_steps) == 3
    assert all(
        step.metadata["execution_category"] == "analysis" and step.metadata["execution_category_source"] == "semantic"
        for step in tool_steps
    )


def test_agent_runtime_blocks_disabled_write_tool_before_execution(monkeypatch) -> None:
    def fake_route(**kwargs):
        return BotAnswerRoute(path="feishu_task_create", scope="company", reason="test")

    def fake_execute_tool(*args, **kwargs):
        raise AssertionError("write tool should not execute when company policy disables writes")

    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="创建一个飞书任务",
        normalized_command="创建一个飞书任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        allow_write_tools=False,
    )

    assert "禁止执行写工具" in result.answer
    assert [step.kind for step in result.trace.steps] == ["semantic", "route", "tool"]
    tool_step = result.trace.steps[-1]
    assert tool_step.name == "feishu_task_create"
    assert tool_step.status == "denied"
    assert tool_step.metadata["allow_write_tools"] is False
    assert tool_step.metadata["requires_dry_run"] is False
    assert tool_step.metadata["confirmed_execution_requires"] == []
    assert tool_step.metadata["policy_reason"] == "write_tools_disabled"


def test_agent_runtime_requires_confirmation_before_write_tool_execution(monkeypatch) -> None:
    def fake_route(**kwargs):
        return BotAnswerRoute(path="feishu_task_create", scope="company", reason="test")

    def fake_execute_tool(*args, **kwargs):
        raise AssertionError("write tool should not execute before confirmation")

    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="创建一个飞书任务",
        normalized_command="创建一个飞书任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        require_write_confirmation=True,
    )

    assert "必须先执行 dry-run" in result.answer
    tool_step = result.trace.steps[-1]
    assert tool_step.name == "feishu_task_create"
    assert tool_step.status == "pending_confirmation"
    assert tool_step.metadata["require_write_confirmation"] is True
    assert tool_step.metadata["requires_dry_run"] is True
    assert tool_step.metadata["confirmed_execution_requires"] == [
        "dry_run=true",
        "confirmed=true",
        "confirmation_token",
    ]
    assert tool_step.metadata["policy_reason"] == "write_confirmation_required"


def test_tool_result_execution_step_planned_defaults_when_no_plan_step() -> None:
    step = _tool_result_execution_step(
        ToolResult(
            tool_name="company_qa",
            provider=ToolProvider.LOCAL,
            status=ToolExecutionStatus.SUCCESS,
            answer="ok",
        ),
        planned=True,
        plan_stop_reason="tool_error",
    )

    assert step.metadata["planned"] is True
    assert step.metadata["required"] is True
    assert step.metadata["on_error"] == "stop"
    assert step.metadata["depends_on"] == []
    assert step.metadata["plan_stop_reason"] == "tool_error"


def test_agent_runtime_requires_user_identity_before_personal_calendar_write_confirmation(monkeypatch) -> None:
    def fake_route(**kwargs):
        return BotAnswerRoute(path="feishu_calendar_create_event", scope="personal", reason="test")

    class FakeDb:
        def scalar(self, query):
            return None

        def add(self, item):
            pass

    def fake_execute_tool(*args, **kwargs):
        raise AssertionError("write tool should not execute before user identity authorization")

    monkeypatch.setattr("app.services.tools.config.get_tool_config", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        FakeDb(),
        company_id=uuid4(),
        question="创建我的日程",
        normalized_command="创建我的日程",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="personal", open_id="ou_1"),
        require_write_confirmation=True,
    )

    assert "授权个人能力包" in result.answer
    assert "必须先执行 dry-run" not in result.answer
    tool_step = result.trace.steps[-1]
    assert tool_step.name == "feishu_calendar_create_event"
    assert tool_step.status == "denied"
    assert tool_step.metadata["user_identity_authorization_required"] is True
    assert tool_step.metadata["required_user_identity_resources"] == ["user_identity_bundle"]
    assert tool_step.metadata["structured_result"]["authorization_actions"][0]["resource_type"] == "user_identity_bundle"


def test_agent_runtime_allows_write_tool_when_company_policy_allows_direct_execution(monkeypatch) -> None:
    captured = {}

    def fake_route(**kwargs):
        return BotAnswerRoute(path="feishu_task_create", scope="company", reason="test")

    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_route)
    _stub_tool(monkeypatch, answer="已创建任务", capture=captured)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="创建一个飞书任务",
        normalized_command="创建一个飞书任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        require_write_confirmation=False,
    )

    assert "已创建任务" in result.answer
    assert captured["request"].tool_name == "feishu_task_create"
    assert result.trace.steps[-1].status == "success"


def test_agent_runtime_trace_records_owner_only_permission_denied(monkeypatch) -> None:
    called = False

    def fake_answer(*args, **kwargs):
        nonlocal called
        called = True
        return "should not be called"

    monkeypatch.setattr("app.services.agent.runtime.answer_advisor_question", fake_answer)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="打开驾驶舱",
        normalized_command="驾驶舱概览",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="chat"),
    )

    assert "这部分信息未向你开放" in result.answer
    assert result.trace.route_path == "deny"
    assert [step.status for step in result.trace.steps] == ["success", "denied", "success"]
    assert result.trace.steps[1].metadata["reason"] == "owner_only_intent"
    assert called is False


def test_agent_runtime_routes_approval_qa_to_domain_service(monkeypatch) -> None:
    called = {"advisor": False}
    captured = {}

    def fake_advisor(*args, **kwargs):
        called["advisor"] = True
        return "advisor"

    monkeypatch.setattr("app.services.agent.runtime.answer_advisor_question", fake_advisor)
    _stub_tool(monkeypatch, answer="审批摘要", capture=captured)

    answer = answer_agent_message(
        None,
        company_id=uuid4(),
        question="最近付款审批有什么风险",
        normalized_command="最近付款审批有什么风险",
        chat_id="oc_group",
        actor=BotActor(role="manager", access_scope="domain", domains=("finance", "approval")),
    )

    assert_thinking_answer(answer, header="范围：授权业务域｜审批问答", body="审批摘要")
    assert called == {"advisor": False}
    assert captured["request"].tool_name == "approval_qa"
    assert captured["request"].question == "最近付款审批有什么风险"


def test_agent_runtime_routes_pending_approval_to_realtime_feishu_task_query(monkeypatch) -> None:
    monkeypatch.setattr("app.services.tools.config.get_tool_config", lambda *args, **kwargs: None)

    answer = answer_agent_message(
        None,
        company_id=uuid4(),
        question="那现在有需要我批复的审批吗",
        normalized_command="那现在有需要我批复的审批吗",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", open_id="ou_owner"),
    )

    assert answer.startswith("范围：指定公司｜审批实时待办")
    assert "授权个人能力包" in answer


def test_agent_runtime_routes_pending_approval_application_to_realtime_query(monkeypatch) -> None:
    captured = {}
    _stub_tool(monkeypatch, answer="待我处理报销有 2 条", capture=captured)

    answer = answer_agent_message(
        None,
        company_id=uuid4(),
        question="帮我查下待我处理的报销申请",
        normalized_command="帮我查下待我处理的报销申请",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", open_id="ou_owner"),
    )

    assert answer.startswith("范围：指定公司｜审批实时待办")
    assert captured["request"].tool_name == "feishu_approval_task_query"
    assert captured["request"].question == "待我处理的审批"


def test_agent_runtime_routes_authorized_pending_approval_to_realtime_feishu_task_query(monkeypatch) -> None:
    captured = {}

    class FakeDb:
        def scalar(self, query):
            return SimpleNamespace(
                settings={
                    "user_identity_authorizations": {
                        "user_identity_bundle": {"status": "authorized", "owner_open_id": "ou_owner"},
                    }
                }
            )

        def add(self, item):
            pass

        def scalars(self, query):
            class Empty:
                def all(self):
                    return []

            return Empty()

    def fake_execute_feishu_mcp_tool(context, request):
        captured["context"] = context
        captured["request"] = request
        return "飞书实时待审批"

    monkeypatch.setattr("app.services.tools.config.get_tool_config", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.tools.router.execute_feishu_mcp_tool", fake_execute_feishu_mcp_tool)
    monkeypatch.setattr(
        "app.services.agent.runtime.load_bot_user_context",
        lambda *args, **kwargs: SimpleNamespace(answer_style=None, favorite_modules=[], memory_facts=[]),
    )
    monkeypatch.setattr("app.services.agent.runtime.bot_answer_style", lambda *args, **kwargs: "concise")

    answer = answer_agent_message(
        FakeDb(),
        company_id=uuid4(),
        question="那现在有需要我批复的审批吗",
        normalized_command="那现在有需要我批复的审批吗",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", open_id="ou_owner"),
    )

    assert answer == "范围：指定公司｜审批实时待办\n飞书实时待审批"
    assert captured["request"].tool_name == "feishu_approval_task_query"
    assert captured["request"].question == "待我处理的审批"


def test_agent_runtime_routes_personal_tasks_to_domain_service(monkeypatch) -> None:
    called = {"advisor": False}
    captured = {}

    def fake_advisor(*args, **kwargs):
        called["advisor"] = True
        return "advisor"

    monkeypatch.setattr("app.services.agent.runtime.answer_advisor_question", fake_advisor)
    _stub_tool(monkeypatch, answer="本人待办", capture=captured)

    answer = answer_agent_message(
        None,
        company_id=uuid4(),
        question="我的待办是什么",
        normalized_command="我的待办是什么",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="chat", display_name="王工", open_id="ou_1"),
    )

    assert_thinking_answer(answer, header="范围：当前群｜本人相关事项", body="本人待办")
    assert called == {"advisor": False}
    assert captured["request"].tool_name == "personal_tasks"
    assert captured["context"].actor.display_name == "王工"
    assert captured["context"].actor.open_id == "ou_1"


def test_agent_runtime_routes_owner_personal_task_question_to_user_identity_scope() -> None:
    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="我的待办事项",
        normalized_command="我的待办事项",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", display_name="Joon", open_id="ou_owner", domains=("all",)),
    )

    assert result.trace.route_path == "personal_tasks"
    assert "授权个人能力包" in result.answer
    assert result.trace.steps[-1].metadata["user_identity_required"] is True


def test_agent_runtime_trace_marks_personal_actor_boundary(monkeypatch) -> None:
    captured = {}
    _stub_tool(monkeypatch, answer="本人待办", capture=captured)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="我的待办是什么",
        normalized_command="我的待办是什么",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="personal", display_name="王工", open_id="ou_1"),
    )

    payload = agent_runtime_result_payload(result)
    actor_context = payload["trace"]["actor_context"]
    agent_identity = payload["trace"]["agent_identity"]
    assert agent_identity["agent_id"].endswith(":ou_1")
    assert agent_identity["agent_owner_open_id"] == "ou_1"
    assert agent_identity["agent_owner_display_name"] == "王工"
    assert agent_identity["data_access_scope"] == "personal"
    assert agent_identity["user_identity"] == "resource_owner_identity"
    assert agent_identity["user_identity_constraints"] == ["resource_owner_authorization"]
    assert actor_context["data_access_scope"] == "personal"
    assert actor_context["personal_owner_open_id"] == "ou_1"
    assert actor_context["identity_keys"] == ["display_name", "open_id"]
    assert actor_context["has_strong_identity"] is True
    assert actor_context["company_data_allowed"] is False
    assert actor_context["cross_user_data_allowed"] is False
    assert actor_context["final_answer_owner"] == "agent_runtime"


def test_agent_runtime_routes_chat_summary_to_domain_service(monkeypatch) -> None:
    called = {"advisor": False}
    captured = {}

    def fake_advisor(*args, **kwargs):
        called["advisor"] = True
        return "advisor"

    monkeypatch.setattr("app.services.agent.runtime.answer_advisor_question", fake_advisor)
    _stub_tool(monkeypatch, answer="群摘要", capture=captured)

    answer = answer_agent_message(
        None,
        company_id=uuid4(),
        question="这个群刚才说了什么",
        normalized_command="这个群刚才说了什么",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="chat"),
    )

    assert_thinking_answer(answer, header="范围：当前群｜当前群总结", body="群摘要")
    assert called == {"advisor": False}
    assert captured["request"].tool_name == "chat_summary"
    assert captured["context"].chat_id == "oc_group"


def test_agent_runtime_routes_chat_tasks_to_domain_service(monkeypatch) -> None:
    called = {"advisor": False}
    captured = {}

    def fake_advisor(*args, **kwargs):
        called["advisor"] = True
        return "advisor"

    monkeypatch.setattr("app.services.agent.runtime.answer_advisor_question", fake_advisor)
    _stub_tool(monkeypatch, answer="群待办", capture=captured)

    answer = answer_agent_message(
        None,
        company_id=uuid4(),
        question="这个群有什么待办任务",
        normalized_command="这个群有什么待办任务",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="chat"),
    )

    assert_thinking_answer(answer, header="范围：当前群｜当前群待办", body="群待办")
    assert called == {"advisor": False}
    assert captured["request"].tool_name == "chat_tasks"
    assert captured["context"].chat_id == "oc_group"


def test_agent_runtime_planner_builds_chat_tasks_query_to_bitable_plan(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer=f"{request.tool_name} answer",
        )

    fake_semantic = SimpleNamespace(
        route_hint="chat_tasks",
        module_hint="tasks",
        canonical_question="把这个群待办查一下，建一张群待办表并写入",
        confidence=0.98,
        source="test",
        execution_category="query",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="chat_tasks", scope="chat", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="把这个群待办查一下，建一张群待办表并写入",
        normalized_command="把这个群待办查一下，建一张群待办表并写入",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="chat"),
        planner_enabled=True,
        max_planner_steps=6,
        require_write_confirmation=False,
    )

    planner_step = next(step for step in result.trace.steps if step.kind == "planner")
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert planner_step.metadata["route_path"] == "chat_tasks"
    assert [step.name for step in tool_steps] == [
        "chat_tasks",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "semantic"
    assert result.trace.reply_mode["mode_id"] == "thinking"
    assert result.trace.reply_mode["show_thinking_map"] is True
    assert calls == ["chat_tasks", "feishu_bitable_table_create", "feishu_bitable_record_batch_create"]


def test_agent_runtime_planner_keeps_approval_trend_query_as_single_tool(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval risk trend",
        )

    fake_semantic = SimpleNamespace(
        route_hint="feishu_approval_task_query",
        module_hint="approvals",
        canonical_question="待我处理报销单趋势怎样",
        confidence=0.97,
        source="test",
        execution_category="query",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="待我处理报销单趋势怎样",
        normalized_command="待我处理报销单趋势怎样",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["feishu_approval_task_query"]
    assert calls == ["feishu_approval_task_query"]
    assert result.trace.execution_category_source == "semantic"
    assert result.trace.execution_category == "query"


def test_agent_runtime_planner_keeps_mail_trend_query_as_single_tool(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail trend",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="最近邮件趋势怎么看",
        confidence=0.97,
        source="test",
        execution_category="query",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="最近邮件趋势怎么看",
        normalized_command="最近邮件趋势怎么看",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category_source == "semantic"
    assert result.trace.execution_category == "query"


def test_agent_runtime_planner_keeps_chat_trend_query_as_single_tool(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="chat trend",
        )

    fake_semantic = SimpleNamespace(
        route_hint="chat_tasks",
        module_hint="tasks",
        canonical_question="查一下群里待办趋势并建一张待办表",
        confidence=0.97,
        source="test",
        execution_category="query",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="chat_tasks", scope="chat", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="查一下群里待办趋势并建一张待办表",
        normalized_command="查一下群里待办趋势并建一张待办表",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="chat"),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["chat_tasks"]
    assert calls == ["chat_tasks"]


def test_agent_runtime_planner_keeps_task_trend_query_as_single_tool(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task analysis",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="项目任务趋势怎么了，帮我创建一张任务清单表",
        confidence=0.97,
        source="test",
        execution_category="analysis",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="项目任务趋势怎么了，帮我创建一张任务清单表",
        normalized_command="项目任务趋势怎么了，帮我创建一张任务清单表",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "semantic"


def test_agent_runtime_planner_keeps_task_decision_query_as_single_tool(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task decision",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="任务风险是否可控，该建一张任务表吗",
        confidence=0.97,
        source="test",
        execution_category="decision",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="任务风险是否可控，该建一张任务表吗",
        normalized_command="任务风险是否可控，该建一张任务表吗",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "decision"
    assert result.trace.execution_category_source == "semantic"


def test_agent_runtime_planner_keeps_approval_decision_query_as_single_tool(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval decision",
        )

    fake_semantic = SimpleNamespace(
        route_hint="feishu_approval_task_query",
        module_hint="approvals",
        canonical_question="待我处理报销单该不该通过，建一张审批表",
        confidence=0.97,
        source="test",
        execution_category="decision",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="待我处理报销单该不该通过，建一张审批表",
        normalized_command="待我处理报销单该不该通过，建一张审批表",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["feishu_approval_task_query"]
    assert calls == ["feishu_approval_task_query"]
    assert result.trace.execution_category == "decision"
    assert result.trace.execution_category_source == "semantic"


def test_agent_runtime_planner_keeps_approval_query_as_single_tool_when_category_is_not_explicit(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval qa fallback",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="报销申请是否合理，帮我建一张审批清单表",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销申请是否合理，帮我建一张审批清单表",
        normalized_command="报销申请是否合理，帮我建一张审批清单表",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "decision"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_keeps_cross_domain_decision_queries_without_explicit_category_as_single_tool(monkeypatch) -> None:
    cases = (
        (
            "approval_qa",
            "报销单是否合理，建一张审批清单表",
            BotActor(role="owner", access_scope="company", domains=("all",)),
        ),
        (
            "task_qa",
            "项目任务该不该延期，建一张任务清单表",
            BotActor(role="owner", access_scope="company", domains=("all",)),
        ),
        (
            "personal_tasks",
            "我的待办要不要优先处理，建一张待办表",
            BotActor(role="owner", access_scope="company", domains=("all",)),
        ),
        (
            "mail_qa",
            "近期异常邮件是否要关注，建一张邮件清单表",
            BotActor(role="owner", access_scope="company", domains=("all",)),
        ),
        (
            "chat_tasks",
            "群里待办该不该先处理，建一张待办表",
            BotActor(role="member", access_scope="chat"),
        ),
        (
            "feishu_approval_task_query",
            "待我处理报销单该不该通过，建一张审批清单表",
            BotActor(role="owner", access_scope="company", domains=("all",)),
        ),
    )

    for route_path, question, actor in cases:
        calls: list[str] = []

        def fake_execute_tool(context, request):
            calls.append(request.tool_name)
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.LOCAL,
                answer=f"{route_path} answer",
            )

        fake_semantic = SimpleNamespace(
            route_hint=route_path,
            module_hint=(
                "tasks"
                if route_path in {"task_qa", "personal_tasks", "chat_tasks"}
                else "approvals"
                if route_path in {"approval_qa", "feishu_approval_task_query"}
                else "resources"
            ),
            canonical_question=question,
            confidence=0.97,
            source="test",
        )

        def fake_resolve_route(*args, **kwargs):
            return BotAnswerRoute(
                path=route_path,
                scope="chat" if route_path == "chat_tasks" else "company",
                reason="permission_scope",
            )

        monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
        monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
        monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

        result = answer_agent_message_with_trace(
            None,
            company_id=uuid4(),
            question=question,
            normalized_command=question,
            chat_id="oc_group",
            actor=actor,
            planner_enabled=True,
            max_planner_steps=6,
        )

        tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
        assert tool_steps == [route_path]
        assert calls == [route_path]
        assert result.trace.execution_category == "decision"
        assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_keeps_task_decision_query_as_single_tool_when_category_is_not_explicit(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task decision",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="项目任务该不该延期，帮我建一张任务清单表",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="项目任务该不该延期，帮我建一张任务清单表",
        normalized_command="项目任务该不该延期，帮我建一张任务清单表",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "decision"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_keeps_personal_task_decision_query_as_single_tool_when_category_is_not_explicit(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="personal tasks decision",
        )

    fake_semantic = SimpleNamespace(
        route_hint="personal_tasks",
        module_hint="tasks",
        canonical_question="我的待办要不要加优先级，建一张待办表",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="我的待办要不要加优先级，建一张待办表",
        normalized_command="我的待办要不要加优先级，建一张待办表",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["personal_tasks"]
    assert calls == ["personal_tasks"]
    assert result.trace.execution_category == "decision"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_keeps_mail_decision_query_as_single_tool_when_category_is_not_explicit(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail decision",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="近期异常邮件是否要关注，建一张邮件表",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="近期异常邮件是否要关注，建一张邮件表",
        normalized_command="近期异常邮件是否要关注，建一张邮件表",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category == "decision"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_keeps_chat_task_decision_query_as_single_tool_when_category_is_not_explicit(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="chat task decision",
        )

    fake_semantic = SimpleNamespace(
        route_hint="chat_tasks",
        module_hint="tasks",
        canonical_question="群里待办该不该先处理，建一张待办表",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="chat_tasks", scope="chat", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="群里待办该不该先处理，建一张待办表",
        normalized_command="群里待办该不该先处理，建一张待办表",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="chat"),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["chat_tasks"]
    assert calls == ["chat_tasks"]
    assert result.trace.execution_category == "decision"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_keeps_approval_query_analysis_as_single_tool(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval analysis",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="这笔报销该不该通过，顺便建一张表",
        confidence=0.97,
        source="test",
        execution_category="analysis",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这笔报销该不该通过，顺便建一张表",
        normalized_command="这笔报销该不该通过，顺便建一张表",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "semantic"


def test_agent_runtime_planner_keeps_approval_overdue_bitable_terms_as_single_tool_when_semantically_forced_analysis(
    monkeypatch,
) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval analysis",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="报销逾期，先帮我建一张审批清单表",
        confidence=0.97,
        source="test",
        execution_category="analysis",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销逾期，先帮我建一张审批清单表",
        normalized_command="报销逾期，先帮我建一张审批清单表",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "semantic"


def test_agent_runtime_planner_treats_approval_action_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval action",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="帮我批准这笔报销",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="帮我批准这笔报销",
        normalized_command="帮我批准这笔报销",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_action_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task action",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="帮我催办一下这条任务",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="帮我催办一下这条任务",
        normalized_command="帮我催办一下这条任务",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_claim_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task claim",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="这个任务我先认领一下",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这个任务我先认领一下",
        normalized_command="这个任务我先认领一下",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_transfer_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task transfer",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="帮我把这个任务转交给王总",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="帮我把这个任务转交给王总",
        normalized_command="帮我把这个任务转交给王总",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_delegate_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task delegate",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="这个任务先转办给王总",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这个任务先转办给王总",
        normalized_command="这个任务先转办给王总",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_reassign_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task reassign",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="任务我先转派给王总",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="任务我先转派给王总",
        normalized_command="任务我先转派给王总",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_modify_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task modify",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="这个任务我先修改一下",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这个任务我先修改一下",
        normalized_command="这个任务我先修改一下",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_remove_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task remove",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="这个任务先删除",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这个任务先删除",
        normalized_command="这个任务先删除",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_copy_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task copy",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="把这个任务先复制一份",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="把这个任务先复制一份",
        normalized_command="把这个任务先复制一份",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_comment_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task comment",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="给这个任务先加个评论",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="给这个任务先加个评论",
        normalized_command="给这个任务先加个评论",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_reopen_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task reopen",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="这个任务我先重开",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这个任务我先重开",
        normalized_command="这个任务我先重开",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_suspend_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task suspend",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="这个任务先挂起",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这个任务先挂起",
        normalized_command="这个任务先挂起",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_resume_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task resume",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="这个任务先恢复",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这个任务先恢复",
        normalized_command="这个任务先恢复",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_accept_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task accept",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="这个任务先验收",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这个任务先验收",
        normalized_command="这个任务先验收",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_cancel_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task cancel",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="这个任务先取消",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这个任务先取消",
        normalized_command="这个任务先取消",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_start_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task start",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="这个任务先开始",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这个任务先开始",
        normalized_command="这个任务先开始",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_urgent_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task urgent",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="这个任务先加急",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这个任务先加急",
        normalized_command="这个任务先加急",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_assign_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task assign",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="这个任务请你指派给王总",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这个任务请你指派给王总",
        normalized_command="这个任务请你指派给王总",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_complete_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task complete",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="把这个任务先关闭",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="把这个任务先关闭",
        normalized_command="把这个任务先关闭",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_archive_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task archive",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="这个任务先归档",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这个任务先归档",
        normalized_command="这个任务先归档",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_arrange_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task arrange",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="这件事先安排一下",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这件事先安排一下",
        normalized_command="这件事先安排一下",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_mail_distribute_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail distribute",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="请先把这封邮件下发给法务",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="请先把这封邮件下发给法务",
        normalized_command="请先把这封邮件下发给法务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_recall_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval recall",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="这笔报销我先撤回",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这笔报销我先撤回",
        normalized_command="这笔报销我先撤回",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_revoke_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval revoke",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="这笔报销先撤销",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这笔报销先撤销",
        normalized_command="这笔报销先撤销",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_pass_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval pass",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="这笔报销先审批通过",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这笔报销先审批通过",
        normalized_command="这笔报销先审批通过",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_veto_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval veto",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="这笔报销我先否决",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这笔报销我先否决",
        normalized_command="这笔报销我先否决",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_resubmit_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval resubmit",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="这笔报销先重提",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这笔报销先重提",
        normalized_command="这笔报销先重提",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_terminate_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval terminate",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="这笔报销先终止",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这笔报销先终止",
        normalized_command="这笔报销先终止",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_submit_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval submit",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="这笔报销我先提交",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这笔报销我先提交",
        normalized_command="这笔报销我先提交",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_return_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval return",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="这笔报销先退回给发起人",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这笔报销先退回给发起人",
        normalized_command="这笔报销先退回给发起人",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_agree_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval agree",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="这笔报销我先同意",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这笔报销我先同意",
        normalized_command="这笔报销我先同意",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_mail_reply_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail reply",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="给客户回复一封邮件",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="给客户回复一封邮件",
        normalized_command="给客户回复一封邮件",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_mail_send_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail send",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="先给客户发送一封邮件",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="先给客户发送一封邮件",
        normalized_command="先给客户发送一封邮件",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_approve_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval approve",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="这笔报销我先批复",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这笔报销我先批复",
        normalized_command="这笔报销我先批复",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_add_sign_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval add sign",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="这笔报销请先加签",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这笔报销请先加签",
        normalized_command="这笔报销请先加签",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_reject_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval reject",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="这笔报销我想要拒绝",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这笔报销我想要拒绝",
        normalized_command="这笔报销我想要拒绝",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_mail_action_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail action",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="请给客户发送一封回信",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="请给客户发送一封回信",
        normalized_command="请给客户发送一封回信",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_mail_forward_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail forward",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="把这封邮件转发给财务",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="把这封邮件转发给财务",
        normalized_command="把这封邮件转发给财务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_mail_cc_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail cc",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="这封邮件抄送给法务",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这封邮件抄送给法务",
        normalized_command="这封邮件抄送给法务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_mail_upload_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail upload",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="把这封邮件先上传到云盘",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="把这封邮件先上传到云盘",
        normalized_command="把这封邮件先上传到云盘",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_mail_download_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail download",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="先下载这封邮件附件",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="先下载这封邮件附件",
        normalized_command="先下载这封邮件附件",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_mail_compose_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail compose",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="先帮我起草一封邮件",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="先帮我起草一封邮件",
        normalized_command="先帮我起草一封邮件",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_mail_broadcast_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail broadcast",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="这封邮件先群发给法务",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这封邮件先群发给法务",
        normalized_command="这封邮件先群发给法务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_mail_relay_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail relay",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="请先转寄这封邮件",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="请先转寄这封邮件",
        normalized_command="请先转寄这封邮件",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_mail_archive_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail archive",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="请先把这封邮件归档",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="请先把这封邮件归档",
        normalized_command="请先把这封邮件归档",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_mail_mark_read_as_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail mark_read",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="把这封邮件标记为已读",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="把这封邮件标记为已读",
        normalized_command="把这封邮件标记为已读",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category == "action"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_start_time_query_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="请查一下这个任务开始时间",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="请查一下这个任务开始时间",
        normalized_command="请查一下这个任务开始时间",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_completion_status_query_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="请查下任务是否完成",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="请查下任务是否完成",
        normalized_command="请查下任务是否完成",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_completion_without_query_verb_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="任务是否完成",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="任务是否完成",
        normalized_command="任务是否完成",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_priority_without_query_verb_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="任务优先级",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="任务优先级",
        normalized_command="任务优先级",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_assignee_without_query_verb_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="任务负责人",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="任务负责人",
        normalized_command="任务负责人",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_requestor_without_query_verb_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="任务发起人",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="任务发起人",
        normalized_command="任务发起人",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_creator_without_query_verb_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="任务创建者",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="任务创建者",
        normalized_command="任务创建者",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_processor_without_query_verb_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="任务处理人",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="任务处理人",
        normalized_command="任务处理人",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_start_time_without_query_verb_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="任务开始时间",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="任务开始时间",
        normalized_command="任务开始时间",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_deadline_without_query_verb_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="任务截止日期",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="任务截止日期",
        normalized_command="任务截止日期",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_progress_without_query_verb_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="任务当前进度",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="任务当前进度",
        normalized_command="任务当前进度",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_expected_completion_without_query_verb_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="任务预计完成",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="任务预计完成",
        normalized_command="任务预计完成",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_reopen_without_query_verb_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="任务是否重开",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="任务是否重开",
        normalized_command="任务是否重开",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_overdue_without_query_verb_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="任务是否超期",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="任务是否超期",
        normalized_command="任务是否超期",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_task_overdue_with_different_term_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="task_qa",
        module_hint="tasks",
        canonical_question="任务是否逾期",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="task_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="任务是否逾期",
        normalized_command="任务是否逾期",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["task_qa"]
    assert calls == ["task_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_overdue_without_query_verb_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="报销是否超期",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销是否超期",
        normalized_command="报销是否超期",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_overdue_with_bitable_terms_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="报销超期，帮我建一张审批清单表",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销超期，帮我建一张审批清单表",
        normalized_command="报销超期，帮我建一张审批清单表",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_overdue_with_different_term_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="报销逾期",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销逾期",
        normalized_command="报销逾期",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_overdue_with_different_term_and_bitable_terms_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="报销逾期，先帮我建一张审批清单表",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销逾期，先帮我建一张审批清单表",
        normalized_command="报销逾期，先帮我建一张审批清单表",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_mail_read_status_query_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="查询下邮件是否已读",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="查询下邮件是否已读",
        normalized_command="查询下邮件是否已读",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_mail_read_status_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="邮件是否已读",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="邮件是否已读",
        normalized_command="邮件是否已读",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_mail_overdue_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="邮件是否超期",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="邮件是否超期",
        normalized_command="邮件是否超期",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_mail_overdue_with_different_term_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="邮件逾期",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="邮件逾期",
        normalized_command="邮件逾期",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_mail_overdue_with_bitable_terms_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        if request.tool_name == "feishu_bitable_table_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"table_id": "tbl-mail-overdue"}),
                structured_result={"response_text": json.dumps({"table_id": "tbl-mail-overdue"})},
            )
        if request.tool_name == "feishu_bitable_record_batch_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"created": 2}),
                structured_result={"response_text": json.dumps({"created": 2})},
            )
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="邮件超期，帮我建一张邮件清单表",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="邮件超期，帮我建一张邮件清单表",
        normalized_command="邮件超期，帮我建一张邮件清单表",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == [
        "mail_qa",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert calls == ["mail_qa", "feishu_bitable_table_create", "feishu_bitable_record_batch_create"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_keeps_mail_overdue_bitable_terms_as_single_tool_when_semantically_forced_analysis(
    monkeypatch,
) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="邮件超期，帮我建一张邮件清单表",
        confidence=0.97,
        source="test",
        execution_category="analysis",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="邮件超期，帮我建一张邮件清单表",
        normalized_command="邮件超期，帮我建一张邮件清单表",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "semantic"


def test_agent_runtime_planner_treats_approval_status_query_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="先查下报销状态",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="先查下报销状态",
        normalized_command="先查下报销状态",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_passed_flag_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="这笔报销是否通过",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这笔报销是否通过",
        normalized_command="这笔报销是否通过",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_status_without_query_verb_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="报销状态",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销状态",
        normalized_command="报销状态",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_current_status_without_query_verb_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="报销当前状态",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销当前状态",
        normalized_command="报销当前状态",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_result_without_query_verb_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="报销结果",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销结果",
        normalized_command="报销结果",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_completion_without_query_verb_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="报销是否完成",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销是否完成",
        normalized_command="报销是否完成",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_requestor_without_query_verb_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="报销发起人",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销发起人",
        normalized_command="报销发起人",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_submitter_without_query_verb_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="报销提交人",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销提交人",
        normalized_command="报销提交人",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_creator_without_query_verb_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="报销创建者",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销创建者",
        normalized_command="报销创建者",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_processor_without_query_verb_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="报销处理人",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销处理人",
        normalized_command="报销处理人",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_deadline_without_query_verb_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="报销截止日期",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销截止日期",
        normalized_command="报销截止日期",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_approval_progress_without_query_verb_as_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="approval_qa",
        module_hint="approvals",
        canonical_question="报销当前进度",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="approval_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销当前进度",
        normalized_command="报销当前进度",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["approval_qa"]
    assert calls == ["approval_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_personal_tasks_start_time_query_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="personal task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="personal_tasks",
        module_hint="tasks",
        canonical_question="请查一下我的待办开始时间",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="请查一下我的待办开始时间",
        normalized_command="请查一下我的待办开始时间",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["personal_tasks"]
    assert calls == ["personal_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_personal_tasks_completion_query_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="personal task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="personal_tasks",
        module_hint="tasks",
        canonical_question="请查下我的待办是否完成",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="请查下我的待办是否完成",
        normalized_command="请查下我的待办是否完成",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["personal_tasks"]
    assert calls == ["personal_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_personal_tasks_completion_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="personal task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="personal_tasks",
        module_hint="tasks",
        canonical_question="我的待办是否完成",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="我的待办是否完成",
        normalized_command="我的待办是否完成",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["personal_tasks"]
    assert calls == ["personal_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_personal_tasks_expected_completion_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="personal task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="personal_tasks",
        module_hint="tasks",
        canonical_question="我的待办预计完成",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="我的待办预计完成",
        normalized_command="我的待办预计完成",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["personal_tasks"]
    assert calls == ["personal_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_personal_tasks_reopen_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="personal task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="personal_tasks",
        module_hint="tasks",
        canonical_question="我的待办是否重开",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="我的待办是否重开",
        normalized_command="我的待办是否重开",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["personal_tasks"]
    assert calls == ["personal_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_personal_tasks_overdue_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="personal tasks query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="personal_tasks",
        module_hint="tasks",
        canonical_question="我的待办是否逾期",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="我的待办是否逾期",
        normalized_command="我的待办是否逾期",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["personal_tasks"]
    assert calls == ["personal_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_personal_tasks_start_time_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="personal task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="personal_tasks",
        module_hint="tasks",
        canonical_question="我的待办开始时间",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="我的待办开始时间",
        normalized_command="我的待办开始时间",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["personal_tasks"]
    assert calls == ["personal_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_personal_tasks_deadline_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="personal task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="personal_tasks",
        module_hint="tasks",
        canonical_question="我的待办截止日期",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="我的待办截止日期",
        normalized_command="我的待办截止日期",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["personal_tasks"]
    assert calls == ["personal_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_personal_tasks_progress_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="personal task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="personal_tasks",
        module_hint="tasks",
        canonical_question="我的待办进度",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="我的待办进度",
        normalized_command="我的待办进度",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["personal_tasks"]
    assert calls == ["personal_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_personal_tasks_priority_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="personal task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="personal_tasks",
        module_hint="tasks",
        canonical_question="我的待办优先级",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="我的待办优先级",
        normalized_command="我的待办优先级",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["personal_tasks"]
    assert calls == ["personal_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_personal_tasks_assignee_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="personal task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="personal_tasks",
        module_hint="tasks",
        canonical_question="我的待办负责人",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="我的待办负责人",
        normalized_command="我的待办负责人",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["personal_tasks"]
    assert calls == ["personal_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_personal_tasks_submitter_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="personal task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="personal_tasks",
        module_hint="tasks",
        canonical_question="我的待办提交人",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="我的待办提交人",
        normalized_command="我的待办提交人",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["personal_tasks"]
    assert calls == ["personal_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_personal_tasks_processor_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="personal task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="personal_tasks",
        module_hint="tasks",
        canonical_question="我的待办处理人",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="我的待办处理人",
        normalized_command="我的待办处理人",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["personal_tasks"]
    assert calls == ["personal_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_chat_tasks_start_time_query_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="chat tasks query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="chat_tasks",
        module_hint="tasks",
        canonical_question="请查一下这条聊天任务开始时间",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="请查一下这条聊天任务开始时间",
        normalized_command="请查一下这条聊天任务开始时间",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["chat_tasks"]
    assert calls == ["chat_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_chat_tasks_completion_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="chat tasks query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="chat_tasks",
        module_hint="tasks",
        canonical_question="这条聊天任务是否完成",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这条聊天任务是否完成",
        normalized_command="这条聊天任务是否完成",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["chat_tasks"]
    assert calls == ["chat_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_chat_tasks_expected_completion_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="chat tasks query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="chat_tasks",
        module_hint="tasks",
        canonical_question="聊天任务预计完成",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="聊天任务预计完成",
        normalized_command="聊天任务预计完成",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["chat_tasks"]
    assert calls == ["chat_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_chat_tasks_reopen_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="chat tasks query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="chat_tasks",
        module_hint="tasks",
        canonical_question="聊天任务是否重开",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="聊天任务是否重开",
        normalized_command="聊天任务是否重开",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["chat_tasks"]
    assert calls == ["chat_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_chat_tasks_overdue_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="chat tasks query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="chat_tasks",
        module_hint="tasks",
        canonical_question="这条聊天任务是否超期",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这条聊天任务是否超期",
        normalized_command="这条聊天任务是否超期",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="chat", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["chat_tasks"]
    assert calls == ["chat_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_chat_tasks_overdue_with_different_term_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="chat tasks query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="chat_tasks",
        module_hint="tasks",
        canonical_question="这条聊天任务是否逾期",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这条聊天任务是否逾期",
        normalized_command="这条聊天任务是否逾期",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["chat_tasks"]
    assert calls == ["chat_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_chat_tasks_deadline_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="chat tasks query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="chat_tasks",
        module_hint="tasks",
        canonical_question="聊天任务截止日期",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="聊天任务截止日期",
        normalized_command="聊天任务截止日期",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["chat_tasks"]
    assert calls == ["chat_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_chat_tasks_progress_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="chat tasks query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="chat_tasks",
        module_hint="tasks",
        canonical_question="聊天任务进度",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="聊天任务进度",
        normalized_command="聊天任务进度",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["chat_tasks"]
    assert calls == ["chat_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_chat_tasks_start_time_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="chat tasks query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="chat_tasks",
        module_hint="tasks",
        canonical_question="聊天任务开始时间",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="聊天任务开始时间",
        normalized_command="聊天任务开始时间",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["chat_tasks"]
    assert calls == ["chat_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_chat_tasks_priority_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="chat tasks query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="chat_tasks",
        module_hint="tasks",
        canonical_question="聊天任务优先级",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="聊天任务优先级",
        normalized_command="聊天任务优先级",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["chat_tasks"]
    assert calls == ["chat_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_chat_tasks_assignee_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="chat tasks query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="chat_tasks",
        module_hint="tasks",
        canonical_question="聊天任务负责人",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="聊天任务负责人",
        normalized_command="聊天任务负责人",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["chat_tasks"]
    assert calls == ["chat_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_chat_tasks_requestor_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="chat tasks query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="chat_tasks",
        module_hint="tasks",
        canonical_question="聊天任务发起人",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="聊天任务发起人",
        normalized_command="聊天任务发起人",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["chat_tasks"]
    assert calls == ["chat_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_chat_tasks_creator_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="chat tasks query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="chat_tasks",
        module_hint="tasks",
        canonical_question="聊天任务创建者",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="聊天任务创建者",
        normalized_command="聊天任务创建者",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["chat_tasks"]
    assert calls == ["chat_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_chat_tasks_processor_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="chat tasks query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="chat_tasks",
        module_hint="tasks",
        canonical_question="聊天任务处理人",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="聊天任务处理人",
        normalized_command="聊天任务处理人",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["chat_tasks"]
    assert calls == ["chat_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_chat_tasks_completion_query_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="chat tasks query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="chat_tasks",
        module_hint="tasks",
        canonical_question="请查下这条聊天任务是否完成",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="chat_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="请查下这条聊天任务是否完成",
        normalized_command="请查下这条聊天任务是否完成",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["chat_tasks"]
    assert calls == ["chat_tasks"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_feishu_approval_task_query_status_query_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="feishu_approval_task_query",
        module_hint="approvals",
        canonical_question="先查下这笔报销提交状态",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="先查下这笔报销提交状态",
        normalized_command="先查下这笔报销提交状态",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["feishu_approval_task_query"]
    assert calls == ["feishu_approval_task_query"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_feishu_approval_task_query_status_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="feishu_approval_task_query",
        module_hint="approvals",
        canonical_question="报销状态",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销状态",
        normalized_command="报销状态",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["feishu_approval_task_query"]
    assert calls == ["feishu_approval_task_query"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_feishu_approval_task_query_current_status_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="feishu_approval_task_query",
        module_hint="approvals",
        canonical_question="报销当前状态",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销当前状态",
        normalized_command="报销当前状态",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["feishu_approval_task_query"]
    assert calls == ["feishu_approval_task_query"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_feishu_approval_task_query_deadline_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="feishu_approval_task_query",
        module_hint="approvals",
        canonical_question="报销截止日期",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销截止日期",
        normalized_command="报销截止日期",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["feishu_approval_task_query"]
    assert calls == ["feishu_approval_task_query"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_feishu_approval_task_query_progress_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="feishu_approval_task_query",
        module_hint="approvals",
        canonical_question="报销当前进度",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销当前进度",
        normalized_command="报销当前进度",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["feishu_approval_task_query"]
    assert calls == ["feishu_approval_task_query"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_feishu_approval_task_query_overdue_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="feishu_approval_task_query",
        module_hint="approvals",
        canonical_question="报销超期",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销超期",
        normalized_command="报销超期",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["feishu_approval_task_query"]
    assert calls == ["feishu_approval_task_query"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_feishu_approval_task_query_overdue_with_different_term_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="feishu_approval_task_query",
        module_hint="approvals",
        canonical_question="报销逾期",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销逾期",
        normalized_command="报销逾期",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["feishu_approval_task_query"]
    assert calls == ["feishu_approval_task_query"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_keeps_feishu_approval_task_query_as_single_tool_when_overdue_bitable_phrase_is_semantically_forced_analysis(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="feishu_approval_task_query",
        module_hint="approvals",
        canonical_question="报销逾期，先帮我建一张审批清单表",
        confidence=0.97,
        source="test",
        execution_category="analysis",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销逾期，先帮我建一张审批清单表",
        normalized_command="报销逾期，先帮我建一张审批清单表",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["feishu_approval_task_query"]
    assert calls == ["feishu_approval_task_query"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "semantic"


def test_agent_runtime_planner_treats_feishu_approval_task_query_overdue_with_bitable_terms_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        if request.tool_name == "feishu_bitable_table_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"table_id": "tbl-approval-overdue"}),
                structured_result={"response_text": json.dumps({"table_id": "tbl-approval-overdue"})},
            )
        if request.tool_name == "feishu_bitable_record_batch_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"created": 1}),
                structured_result={"response_text": json.dumps({"created": 1})},
            )
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="feishu_approval_task_query",
        module_hint="approvals",
        canonical_question="报销逾期，帮我建一张审批清单表",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销逾期，帮我建一张审批清单表",
        normalized_command="报销逾期，帮我建一张审批清单表",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == [
        "feishu_approval_task_query",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert calls == [
        "feishu_approval_task_query",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_feishu_approval_task_query_passed_flag_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="feishu_approval_task_query",
        module_hint="approvals",
        canonical_question="这笔报销是否通过",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这笔报销是否通过",
        normalized_command="这笔报销是否通过",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["feishu_approval_task_query"]
    assert calls == ["feishu_approval_task_query"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_feishu_approval_task_query_detail_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="feishu_approval_task_query",
        module_hint="approvals",
        canonical_question="报销详情",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销详情",
        normalized_command="报销详情",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["feishu_approval_task_query"]
    assert calls == ["feishu_approval_task_query"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_feishu_approval_task_query_completion_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="feishu_approval_task_query",
        module_hint="approvals",
        canonical_question="报销是否完成",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销是否完成",
        normalized_command="报销是否完成",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["feishu_approval_task_query"]
    assert calls == ["feishu_approval_task_query"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_feishu_approval_task_query_requestor_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="feishu_approval_task_query",
        module_hint="approvals",
        canonical_question="报销发起人",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销发起人",
        normalized_command="报销发起人",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["feishu_approval_task_query"]
    assert calls == ["feishu_approval_task_query"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_feishu_approval_task_query_submitter_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="feishu_approval_task_query",
        module_hint="approvals",
        canonical_question="报销提交人",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销提交人",
        normalized_command="报销提交人",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["feishu_approval_task_query"]
    assert calls == ["feishu_approval_task_query"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_feishu_approval_task_query_creator_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="feishu_approval_task_query",
        module_hint="approvals",
        canonical_question="报销创建者",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销创建者",
        normalized_command="报销创建者",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["feishu_approval_task_query"]
    assert calls == ["feishu_approval_task_query"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_feishu_approval_task_query_processor_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="approval task query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="feishu_approval_task_query",
        module_hint="approvals",
        canonical_question="报销处理人",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="feishu_approval_task_query", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="报销处理人",
        normalized_command="报销处理人",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["feishu_approval_task_query"]
    assert calls == ["feishu_approval_task_query"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_mail_sender_query_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="这封邮件的发件人",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这封邮件的发件人",
        normalized_command="这封邮件的发件人",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_mail_subject_query_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="这封邮件主题",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这封邮件主题",
        normalized_command="这封邮件主题",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_mail_subject_alias_query_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="这封邮件标题",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这封邮件标题",
        normalized_command="这封邮件标题",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_mail_recipient_query_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="这封邮件收件人",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这封邮件收件人",
        normalized_command="这封邮件收件人",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_mail_sender_name_query_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="这封邮件发送人",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这封邮件发送人",
        normalized_command="这封邮件发送人",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_treats_mail_attachment_query_without_query_verb_as_query(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail query",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="这封邮件附件",
        confidence=0.97,
        source="test",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="这封邮件附件",
        normalized_command="这封邮件附件",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category == "query"
    assert result.trace.execution_category_source == "route_fallback"


def test_agent_runtime_planner_keeps_mail_analysis_query_as_single_tool(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="mail analysis",
        )

    fake_semantic = SimpleNamespace(
        route_hint="mail_qa",
        module_hint="resources",
        canonical_question="近期邮件趋势分析并创建邮件清单表",
        confidence=0.97,
        source="test",
        execution_category="analysis",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="mail_qa", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="近期邮件趋势分析并创建邮件清单表",
        normalized_command="近期邮件趋势分析并创建邮件清单表",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["mail_qa"]
    assert calls == ["mail_qa"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "semantic"


def test_agent_runtime_planner_keeps_personal_task_analysis_query_as_single_tool(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_tool(context, request):
        calls.append(request.tool_name)
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="personal tasks analysis",
        )

    fake_semantic = SimpleNamespace(
        route_hint="personal_tasks",
        module_hint="tasks",
        canonical_question="我的待办趋势怎样，帮我建一张个人清单表",
        confidence=0.97,
        source="test",
        execution_category="analysis",
    )

    def fake_resolve_route(*args, **kwargs):
        return BotAnswerRoute(path="personal_tasks", scope="company", reason="permission_scope")

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **_: fake_semantic)
    monkeypatch.setattr("app.services.agent.runtime.resolve_bot_route", fake_resolve_route)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="我的待办趋势怎样，帮我建一张个人清单表",
        normalized_command="我的待办趋势怎样，帮我建一张个人清单表",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        max_planner_steps=6,
    )

    tool_steps = [step.name for step in result.trace.steps if step.kind == "tool"]
    assert tool_steps == ["personal_tasks"]
    assert calls == ["personal_tasks"]
    assert result.trace.execution_category == "analysis"
    assert result.trace.execution_category_source == "semantic"


def test_agent_runtime_routes_chat_qa_to_chat_service(monkeypatch) -> None:
    called = {"advisor": False}

    def fake_advisor(*args, **kwargs):
        called["advisor"] = True
        return "advisor"

    monkeypatch.setattr("app.services.agent.runtime.answer_advisor_question", fake_advisor)

    answer = answer_agent_message(
        None,
        company_id=uuid4(),
        question="你是谁",
        normalized_command="机器人身份",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
    )

    assert answer == "我是你的企业数字助理。你是系统所有者，我会按老板权限查询已授权公司数据，并保护员工和跨权限数据边界。"
    assert called == {"advisor": False}


def test_agent_runtime_routes_greeting_to_general_chat_without_company_lookup(monkeypatch) -> None:
    called = {"advisor": False}

    def fake_advisor(*args, **kwargs):
        called["advisor"] = True
        return "advisor"

    monkeypatch.setattr("app.services.agent.runtime.answer_advisor_question", fake_advisor)

    answer = answer_agent_message(
        None,
        company_id=uuid4(),
        question="你好呀",
        normalized_command="你好呀",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
    )

    assert "老板，我在" in answer
    assert called == {"advisor": False}


def test_agent_runtime_routes_company_qa_to_company_service(monkeypatch) -> None:
    called = {"advisor": False}
    captured = {}

    def fake_advisor(*args, **kwargs):
        called["advisor"] = True
        return "advisor"

    monkeypatch.setattr("app.services.agent.runtime.answer_advisor_question", fake_advisor)
    _stub_tool(monkeypatch, answer="公司摘要", capture=captured)

    answer = answer_agent_message(
        None,
        company_id=uuid4(),
        question="固势现在有什么风险",
        normalized_command="固势现在有什么风险",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
    )

    assert_thinking_answer(answer, header="范围：指定公司｜公司级问答", body="公司摘要")
    assert called == {"advisor": False}
    assert captured["request"].tool_name == "company_qa"
    assert captured["request"].question == "固势现在有什么风险"
    assert captured["context"].actor.can_query_company is True


def test_agent_runtime_routes_owner_cockpit_to_report_tool(monkeypatch) -> None:
    called = {"advisor": False}
    captured = {}

    def fake_advisor(*args, **kwargs):
        called["advisor"] = True
        return "advisor"

    monkeypatch.setattr("app.services.agent.runtime.answer_advisor_question", fake_advisor)
    _stub_tool(monkeypatch, answer="老板驾驶舱摘要", capture=captured)

    answer = answer_agent_message(
        None,
        company_id=uuid4(),
        question="打开驾驶舱",
        normalized_command="驾驶舱概览",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
    )

    assert_thinking_answer(answer, header="范围：指定公司｜老板驾驶舱", body="老板驾驶舱摘要")
    assert called == {"advisor": False}
    assert captured["request"].tool_name == "owner_cockpit"
    assert captured["context"].actor.can_query_company is True


def test_agent_runtime_routes_owner_data_blindspot_question_to_company_service(monkeypatch) -> None:
    called = {"advisor": False}
    captured = {}

    def fake_advisor(*args, **kwargs):
        called["advisor"] = True
        return "advisor"

    monkeypatch.setattr("app.services.agent.runtime.answer_advisor_question", fake_advisor)
    _stub_tool(monkeypatch, answer="数据覆盖摘要", capture=captured)

    answer = answer_agent_message(
        None,
        company_id=uuid4(),
        question="今天系统数据有什么盲区",
        normalized_command="今天系统数据有什么盲区",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
    )

    assert_thinking_answer(answer, header="范围：指定公司｜公司级问答", body="数据覆盖摘要")
    assert called == {"advisor": False}
    assert captured["request"].tool_name == "company_qa"
    assert captured["request"].question == "系统数据覆盖和数据盲区"
    assert captured["request"].normalized_command.startswith("module:resources")


def test_agent_runtime_passes_semantic_module_hint_to_company_service(monkeypatch) -> None:
    class Intent:
        route_hint = "company_qa"
        module_hint = "resources"
        canonical_question = "系统数据覆盖和数据盲区"

    captured = {}

    monkeypatch.setattr("app.services.agent.runtime.semantic_intent_for_question", lambda **kwargs: Intent())
    _stub_tool(monkeypatch, answer="数据覆盖摘要", capture=captured)

    answer = answer_agent_message(
        None,
        company_id=uuid4(),
        question="系统有什么地方还看不到",
        normalized_command="系统有什么地方还看不到",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
    )

    assert captured["request"].question == "系统数据覆盖和数据盲区"
    assert captured["request"].normalized_command.startswith("module:resources")
    assert "数据覆盖摘要" in answer


def test_agent_runtime_routes_public_knowledge_qa_to_knowledge_service(monkeypatch) -> None:
    called = {"advisor": False}
    captured = {}

    def fake_advisor(*args, **kwargs):
        called["advisor"] = True
        return "advisor"

    monkeypatch.setattr("app.services.agent.runtime.answer_advisor_question", fake_advisor)
    _stub_tool(monkeypatch, answer="流程答案", capture=captured)

    answer = answer_agent_message(
        None,
        company_id=uuid4(),
        question="报销流程怎么做",
        normalized_command="报销流程怎么做",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="chat"),
    )

    assert answer == "范围：当前群｜公开知识问答\n流程答案"
    assert called == {"advisor": False}
    assert captured["request"].tool_name == "public_knowledge_qa"
    assert captured["request"].question == "报销流程怎么做"


def test_agent_runtime_routes_calendar_question_to_calendar_tool(monkeypatch) -> None:
    captured = {}

    _stub_tool(monkeypatch, answer="日程摘要", capture=captured)

    answer = answer_agent_message(
        None,
        company_id=uuid4(),
        question="今天有什么会议",
        normalized_command="今天有什么会议",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
    )

    assert "日程摘要" in answer
    assert captured["request"].tool_name == "calendar_qa"


def test_agent_runtime_routes_calendar_create_question_to_write_tool_before_confirmation(monkeypatch) -> None:
    class FakeDb:
        def scalar(self, query):
            return None

        def add(self, item):
            pass

    def fake_execute_tool(*args, **kwargs):
        raise AssertionError("calendar write should wait for personal Feishu authorization")

    monkeypatch.setattr("app.services.tools.config.get_tool_config", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        FakeDb(),
        company_id=uuid4(),
        question="帮我新建一个日程",
        normalized_command="帮我新建一个日程",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="personal", open_id="ou_1"),
        require_write_confirmation=True,
    )

    assert result.trace.route_path == "feishu_calendar_create_event"
    assert "范围：本人相关｜创建日程" in result.answer
    assert "授权个人能力包" in result.answer
    tool_step = result.trace.steps[-1]
    assert tool_step.name == "feishu_calendar_create_event"
    assert tool_step.status == "denied"
    assert tool_step.metadata["required_user_identity_resources"] == ["user_identity_bundle"]


def test_agent_runtime_routes_calendar_create_with_synonym_remains_single_tool(monkeypatch) -> None:
    captured = {}

    def fake_execute_tool(context, request):
        captured["request"] = request
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.FEISHU_MOBILE,
            answer="已安排日程",
            structured_result={
                "tool_name": request.tool_name,
                "response_text": "已安排日程",
                "final_answer_allowed": False,
                "final_answer_owner": "agent_runtime",
            },
            status=ToolExecutionStatus.SUCCESS,
            metadata={"final_answer_owner": "agent_runtime"},
        )

    monkeypatch.setattr(
        "app.services.agent.runtime.semantic_intent_for_question",
        lambda **_: SimpleNamespace(
            route_hint="feishu_calendar_create_event",
            module_hint="calendar",
            canonical_question="帮我安排个日程",
            source="test",
            confidence=0.98,
        ),
    )
    monkeypatch.setattr(
        "app.services.agent.runtime.resolve_bot_route",
        lambda *args, **kwargs: BotAnswerRoute(path="feishu_calendar_create_event", scope="personal", reason="permission_scope"),
    )
    monkeypatch.setattr("app.services.agent.runtime._preflight_write_tool_data_permission", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.agent.runtime._write_tool_policy_result", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="帮我安排个日程",
        normalized_command="帮我安排个日程",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="personal", open_id="ou_1"),
        require_write_confirmation=False,
    )

    assert [step.kind for step in result.trace.steps] == ["semantic", "route", "tool"]
    assert result.trace.steps[-1].name == "feishu_calendar_create_event"
    assert captured["request"].tool_name == "feishu_calendar_create_event"
    assert captured["request"].question == "帮我安排个日程"


def test_agent_runtime_routes_people_question_to_people_tool(monkeypatch) -> None:
    captured = {}

    _stub_tool(monkeypatch, answer="组织架构摘要", capture=captured)

    answer = answer_agent_message(
        None,
        company_id=uuid4(),
        question="帮我看一下组织架构",
        normalized_command="组织架构",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
    )

    assert "组织架构摘要" in answer
    assert "组织架构问答" in answer
    assert captured["request"].tool_name == "feishu_contact_organization_snapshot"


def test_agent_runtime_planner_organization_snapshot_only_query(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []

    def fake_execute_tool(context, request):
        captured.append({"tool_name": request.tool_name, "params": dict(request.params)})
        if request.tool_name != "feishu_contact_organization_snapshot":
            raise AssertionError(f"unexpected tool {request.tool_name}")
        payload = {
            "app_token": "bascn-snapshot-only",
            "users": [
                {"name": "小李", "department_names": ["研发部"], "leader_name": "刘总"},
            ],
        }
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.FEISHU_MOBILE,
            answer=json.dumps(payload),
            structured_result={"response_text": json.dumps(payload)},
        )

    monkeypatch.setattr(
        "app.services.agent.runtime.semantic_intent_for_question",
        lambda **_: SimpleNamespace(
            route_hint="feishu_contact_organization_snapshot",
            module_hint="people",
            canonical_question="看一下组织架构",
            source="test",
            confidence=0.97,
        ),
    )
    monkeypatch.setattr(
        "app.services.agent.runtime.resolve_bot_route",
        lambda *args, **kwargs: BotAnswerRoute(path="feishu_contact_organization_snapshot", scope="company", reason="permission_scope"),
    )
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="看一下组织架构",
        normalized_command="看一下组织架构",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        require_write_confirmation=False,
    )

    assert [step.kind for step in result.trace.steps] == ["semantic", "route", "planner", "tool"]
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == ["feishu_contact_organization_snapshot"]
    assert captured == [{"tool_name": "feishu_contact_organization_snapshot", "params": {
        "max_departments": 100,
        "max_users": 500,
        "response_format": "raw_json",
    }}]
    planner_step = next(step for step in result.trace.steps if step.kind == "planner")
    assert planner_step.metadata["steps"][-1]["name"] == "feishu_contact_organization_snapshot"
    assert result.trace.execution_category in {"query", "analysis"}


def test_agent_runtime_planner_organization_snapshot_route_with_build_and_import_terms(monkeypatch) -> None:
    captured: dict[str, dict[str, Any]] = {}

    def fake_execute_tool(context, request):
        captured[request.tool_name] = dict(request.params)
        if request.tool_name == "feishu_contact_organization_snapshot":
            payload = {
                "app_token": "bascn-runtime-mixed",
                "users": [
                    {"name": "阿涛", "department_names": ["研发部"], "leader_name": "王总"},
                ],
            }
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps(payload),
                structured_result={"response_text": json.dumps(payload)},
            )
        if request.tool_name == "feishu_bitable_table_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"table_id": "tbl-runtime-mixed"}),
                structured_result={"response_text": json.dumps({"table_id": "tbl-runtime-mixed"})},
            )
        if request.tool_name == "feishu_bitable_record_batch_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"created": 1}),
                structured_result={"response_text": json.dumps({"created": 1})},
            )
        raise AssertionError(f"unexpected tool {request.tool_name}")

    monkeypatch.setattr(
        "app.services.agent.runtime.semantic_intent_for_question",
        lambda **_: SimpleNamespace(
            route_hint="feishu_contact_organization_snapshot",
            module_hint="people",
            canonical_question="查看组织架构后建一张组织表并把最新组织写入",
            source="test",
            confidence=0.98,
        ),
    )
    monkeypatch.setattr(
        "app.services.agent.runtime.resolve_bot_route",
        lambda *args, **kwargs: BotAnswerRoute(path="feishu_contact_organization_snapshot", scope="company", reason="permission_scope"),
    )
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="查看组织架构后建一张组织表并把最新组织写入",
        normalized_command="查看组织架构后建一张组织表并把最新组织写入",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        require_write_confirmation=False,
    )

    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert captured["feishu_bitable_table_create"]["app_token"] == "bascn-runtime-mixed"
    assert captured["feishu_bitable_record_batch_create"]["app_token"] == "bascn-runtime-mixed"
    assert captured["feishu_bitable_record_batch_create"]["table_id"] == "tbl-runtime-mixed"
    planner_step = next(step for step in result.trace.steps if step.kind == "planner")
    assert planner_step.metadata["route_path"] == "feishu_contact_organization_snapshot"
    assert planner_step.metadata["steps"][-1]["name"] == "feishu_bitable_record_batch_create"


def test_agent_runtime_auto_enables_planner_for_organization_workflow_when_disabled_by_default(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []

    def fake_execute_tool(context, request):
        captured.append({"name": request.tool_name, "params": dict(request.params)})
        if request.tool_name == "feishu_contact_organization_snapshot":
            payload = {
                "app_token": "bascn-auto",
                "users": [
                    {"name": "阿东", "department_names": ["研发部"], "leader_name": "老王"},
                ],
            }
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps(payload),
                structured_result={"response_text": json.dumps(payload)},
            )
        if request.tool_name == "feishu_bitable_table_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"table_id": "tbl-auto-001"}),
                structured_result={"response_text": json.dumps({"table_id": "tbl-auto-001"})},
            )
        if request.tool_name == "feishu_bitable_record_batch_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"created": 1}),
                structured_result={"response_text": json.dumps({"created": 1})},
            )
        raise AssertionError(f"unexpected tool {request.tool_name}")

    monkeypatch.setattr(
        "app.services.agent.runtime.semantic_intent_for_question",
        lambda **_: SimpleNamespace(
            route_hint="feishu_contact_organization_snapshot",
            module_hint="people",
            canonical_question="建一张组织表并把组织结构写入",
            source="test",
            confidence=0.98,
        ),
    )
    monkeypatch.setattr(
        "app.services.agent.runtime.resolve_bot_route",
        lambda *args, **kwargs: BotAnswerRoute(path="feishu_contact_organization_snapshot", scope="company", reason="permission_scope"),
    )
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="建一张组织表并把组织结构写入",
        normalized_command="建一张组织表并把组织结构写入",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        require_write_confirmation=False,
    )

    assert [step.kind for step in result.trace.steps] == ["semantic", "route", "planner", "tool", "tool", "tool"]
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert captured[1]["params"]["app_token"] == "bascn-auto"
    assert captured[2]["params"]["table_id"] == "tbl-auto-001"


def test_agent_runtime_auto_enables_planner_for_organization_workflow_with_synonym_writing_term(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []

    def fake_execute_tool(context, request):
        captured.append({"name": request.tool_name, "params": dict(request.params)})
        if request.tool_name == "feishu_contact_organization_snapshot":
            payload = {
                "app_token": "bascn-syn",
                "users": [
                    {"name": "小张", "department_names": ["市场部"], "leader_name": "赵总"},
                ],
            }
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps(payload),
                structured_result={"response_text": json.dumps(payload)},
            )
        if request.tool_name == "feishu_bitable_table_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"table_id": "tbl-synonym"}),
                structured_result={"response_text": json.dumps({"table_id": "tbl-synonym"})},
            )
        if request.tool_name == "feishu_bitable_record_batch_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"created": 1}),
                structured_result={"response_text": json.dumps({"created": 1})},
            )
        raise AssertionError(f"unexpected tool {request.tool_name}")

    monkeypatch.setattr(
        "app.services.agent.runtime.semantic_intent_for_question",
        lambda **_: SimpleNamespace(
            route_hint="feishu_contact_organization_snapshot",
            module_hint="people",
            canonical_question="新建一张组织结构表，把组织结构同步并写入进去",
            source="test",
            confidence=0.98,
        ),
    )
    monkeypatch.setattr(
        "app.services.agent.runtime.resolve_bot_route",
        lambda *args, **kwargs: BotAnswerRoute(path="feishu_contact_organization_snapshot", scope="company", reason="permission_scope"),
    )
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="新建一张组织结构表，把组织结构同步并写入进去",
        normalized_command="新建一张组织结构表，把组织结构同步并写入进去",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        require_write_confirmation=False,
    )

    assert [step.kind for step in result.trace.steps] == ["semantic", "route", "planner", "tool", "tool", "tool"]
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert captured[1]["params"]["name"] == "最新组织快照"
    assert captured[1]["params"]["app_token"] == "bascn-syn"
    assert captured[2]["params"]["table_id"] == "tbl-synonym"


def test_agent_runtime_auto_enables_planner_for_organization_workflow_with_explicit_table_token_synonym(monkeypatch) -> None:
    captured: dict[str, dict[str, Any]] = {}

    def fake_execute_tool(context, request):
        captured[request.tool_name] = dict(request.params)
        if request.tool_name == "feishu_contact_organization_snapshot":
            payload = {
                "app_token": "bascn-route-syn",
                "users": [
                    {"name": "小赵", "department_names": ["财务部"], "leader_name": "吴总"},
                ],
            }
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps(payload),
                structured_result={"response_text": json.dumps(payload)},
            )
        if request.tool_name == "feishu_bitable_table_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"table_id": "tbl-syn-2"}),
                structured_result={"response_text": json.dumps({"table_id": "tbl-syn-2"})},
            )
        if request.tool_name == "feishu_bitable_record_batch_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"created": 2}),
                structured_result={"response_text": json.dumps({"created": 2})},
            )
        raise AssertionError(f"unexpected tool {request.tool_name}")

    monkeypatch.setattr(
        "app.services.agent.runtime.semantic_intent_for_question",
        lambda **_: SimpleNamespace(
            route_hint="feishu_contact_organization_snapshot",
            module_hint="people",
            canonical_question="帮我建张组织表，并把最新组织写到base=bascn-explicit-token里",
            source="test",
            confidence=0.98,
        ),
    )
    monkeypatch.setattr(
        "app.services.agent.runtime.resolve_bot_route",
        lambda *args, **kwargs: BotAnswerRoute(path="feishu_contact_organization_snapshot", scope="company", reason="permission_scope"),
    )
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="帮我建张组织表，并把最新组织写到base=bascn-explicit-token里",
        normalized_command="帮我建张组织表，并把最新组织写到base=bascn-explicit-token里",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        require_write_confirmation=False,
    )

    assert result.trace.execution_category in {"query", "analysis", "decision", "action"}
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert captured["feishu_bitable_table_create"]["app_token"] == "bascn-explicit-token"
    assert captured["feishu_bitable_record_batch_create"]["table_id"] == "tbl-syn-2"


def test_agent_runtime_auto_enables_planner_for_do_table_organization_snapshot_route(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []

    def fake_execute_tool(context, request):
        captured.append({"name": request.tool_name, "params": dict(request.params)})
        if request.tool_name == "feishu_contact_organization_snapshot":
            payload = {
                "app_token": "bascn-do",
                "users": [{"name": "小刚", "department_names": ["研发二部"], "leader_name": "吴总"}],
            }
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps(payload),
                structured_result={"response_text": json.dumps(payload)},
            )
        if request.tool_name == "feishu_bitable_table_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"table_id": "tbl-do"}),
                structured_result={"response_text": json.dumps({"table_id": "tbl-do"})},
            )
        if request.tool_name == "feishu_bitable_record_batch_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"created": 1}),
                structured_result={"response_text": json.dumps({"created": 1})},
            )
        raise AssertionError(f"unexpected tool {request.tool_name}")

    monkeypatch.setattr(
        "app.services.agent.runtime.semantic_intent_for_question",
        lambda **_: SimpleNamespace(
            route_hint="feishu_contact_organization_snapshot",
            module_hint="people",
            canonical_question="请帮我做一张组织表，把最新组织写入",
            source="test",
            confidence=0.98,
        ),
    )
    monkeypatch.setattr(
        "app.services.agent.runtime.resolve_bot_route",
        lambda *args, **kwargs: BotAnswerRoute(path="feishu_contact_organization_snapshot", scope="company", reason="permission_scope"),
    )
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="请帮我做一张组织表，把最新组织写入",
        normalized_command="请帮我做一张组织表，把最新组织写入",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        require_write_confirmation=False,
    )

    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert captured[1]["params"]["app_token"] == "bascn-do"
    assert captured[2]["params"]["table_id"] == "tbl-do"


def test_agent_runtime_auto_enables_planner_for_do_table_bitable_route(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []

    def fake_execute_tool(context, request):
        captured.append({"name": request.tool_name, "params": dict(request.params)})
        if request.tool_name == "feishu_contact_organization_snapshot":
            payload = {
                "app_token": "bascn-do-bitable",
                "users": [{"name": "小敏", "department_names": ["产品部"], "leader_name": "李总"}],
            }
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps(payload),
                structured_result={"response_text": json.dumps(payload)},
            )
        if request.tool_name == "feishu_bitable_table_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"table_id": "tbl-do-bitable"}),
                structured_result={"response_text": json.dumps({"table_id": "tbl-do-bitable"})},
            )
        if request.tool_name == "feishu_bitable_record_batch_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"created": 3}),
                structured_result={"response_text": json.dumps({"created": 3})},
            )
        raise AssertionError(f"unexpected tool {request.tool_name}")

    monkeypatch.setattr(
        "app.services.agent.runtime.semantic_intent_for_question",
        lambda **_: SimpleNamespace(
            route_hint="bitable_qa",
            module_hint="bitable",
            canonical_question="帮我弄一张组织表，把最新组织同步进来",
            source="test",
            confidence=0.97,
        ),
    )
    monkeypatch.setattr(
        "app.services.agent.runtime.resolve_bot_route",
        lambda *args, **kwargs: BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope"),
    )
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="帮我弄一张组织表，把最新组织同步进来",
        normalized_command="帮我弄一张组织表，把最新组织同步进来",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        require_write_confirmation=False,
    )

    assert [step.kind for step in result.trace.steps] == ["semantic", "route", "planner", "tool", "tool", "tool"]
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert captured[1]["params"]["app_token"] == "bascn-do-bitable"
    assert captured[2]["params"]["table_id"] == "tbl-do-bitable"


def test_agent_runtime_auto_enables_planner_for_mixed_table_term_bitable_route(monkeypatch) -> None:
    captured: dict[str, dict[str, Any]] = {}

    def fake_execute_tool(context, request):
        captured[request.tool_name] = dict(request.params)
        if request.tool_name == "feishu_contact_organization_snapshot":
            payload = {
                "app_token": "bascn-mixed",
                "users": [{"name": "小周", "department_names": ["法务部"], "leader_name": "陈总"}],
            }
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps(payload),
                structured_result={"response_text": json.dumps(payload)},
            )
        if request.tool_name == "feishu_bitable_table_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"table_id": "tbl-mixed"}),
                structured_result={"response_text": json.dumps({"table_id": "tbl-mixed"})},
            )
        if request.tool_name == "feishu_bitable_record_batch_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"created": 4}),
                structured_result={"response_text": json.dumps({"created": 4})},
            )
        raise AssertionError(f"unexpected tool {request.tool_name}")

    monkeypatch.setattr(
        "app.services.agent.runtime.semantic_intent_for_question",
        lambda **_: SimpleNamespace(
            route_hint="bitable_qa",
            module_hint="bitable",
            canonical_question="整一张组织表，给我同步最新组织写进里面",
            source="test",
            confidence=0.98,
        ),
    )
    monkeypatch.setattr(
        "app.services.agent.runtime.resolve_bot_route",
        lambda *args, **kwargs: BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope"),
    )
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="整一张组织表，给我同步最新组织写进里面",
        normalized_command="整一张组织表，给我同步最新组织写进里面",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        require_write_confirmation=False,
    )

    assert [step.kind for step in result.trace.steps] == ["semantic", "route", "planner", "tool", "tool", "tool"]
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert captured["feishu_bitable_table_create"]["app_token"] == "bascn-mixed"
    assert captured["feishu_bitable_record_batch_create"]["table_id"] == "tbl-mixed"


def test_agent_runtime_auto_enables_planner_for_set_table_term_bitable_route(monkeypatch) -> None:
    captured: dict[str, dict[str, Any]] = {}

    def fake_execute_tool(context, request):
        captured[request.tool_name] = dict(request.params)
        if request.tool_name == "feishu_contact_organization_snapshot":
            payload = {
                "app_token": "bascn-settable-runtime",
                "users": [{"name": "小林", "department_names": ["行政部"], "leader_name": "周总"}],
            }
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps(payload),
                structured_result={"response_text": json.dumps(payload)},
            )
        if request.tool_name == "feishu_bitable_table_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"table_id": "tbl-settable"}),
                structured_result={"response_text": json.dumps({"table_id": "tbl-settable"})},
            )
        if request.tool_name == "feishu_bitable_record_batch_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"created": 5}),
                structured_result={"response_text": json.dumps({"created": 5})},
            )
        raise AssertionError(f"unexpected tool {request.tool_name}")

    monkeypatch.setattr(
        "app.services.agent.runtime.semantic_intent_for_question",
        lambda **_: SimpleNamespace(
            route_hint="bitable_qa",
            module_hint="bitable",
            canonical_question="起一张组织表，base=bascn-settable，把最新组织同步过去",
            source="test",
            confidence=0.99,
        ),
    )
    monkeypatch.setattr(
        "app.services.agent.runtime.resolve_bot_route",
        lambda *args, **kwargs: BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope"),
    )
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="起一张组织表，base=bascn-settable，把最新组织同步过去",
        normalized_command="起一张组织表，base=bascn-settable，把最新组织同步过去",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        require_write_confirmation=False,
    )

    assert [step.kind for step in result.trace.steps] == ["semantic", "route", "planner", "tool", "tool", "tool"]
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == [
        "feishu_contact_organization_snapshot",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert captured["feishu_bitable_table_create"]["app_token"] == "bascn-settable"
    assert captured["feishu_bitable_record_batch_create"]["table_id"] == "tbl-settable"


def test_agent_runtime_single_snapshot_for_bitable_route_without_write_intent(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []

    def fake_execute_tool(context, request):
        captured.append({"name": request.tool_name, "params": dict(request.params)})
        if request.tool_name != "feishu_contact_organization_snapshot":
            raise AssertionError(f"unexpected tool {request.tool_name}")
        payload = {
            "app_token": "bascn-bitable-single",
            "users": [
                {"name": "小云", "department_names": ["财务部"], "leader_name": "陈总"},
            ],
        }
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.FEISHU_MOBILE,
            answer=json.dumps(payload),
            structured_result={"response_text": json.dumps(payload)},
        )

    monkeypatch.setattr(
        "app.services.agent.runtime.semantic_intent_for_question",
        lambda **_: SimpleNamespace(
            route_hint="bitable_qa",
            module_hint="people",
            canonical_question="帮我看一下组织架构对应了哪些表",
            source="test",
            confidence=0.96,
        ),
    )
    monkeypatch.setattr(
        "app.services.agent.runtime.resolve_bot_route",
        lambda *args, **kwargs: BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope"),
    )
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="帮我看一下组织架构对应了哪些表",
        normalized_command="帮我看一下组织架构对应了哪些表",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        require_write_confirmation=False,
    )

    assert [step.kind for step in result.trace.steps] == ["semantic", "route", "tool"]
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == ["feishu_contact_organization_snapshot"]
    assert captured == [{"name": "feishu_contact_organization_snapshot", "params": {
        "max_departments": 100,
        "max_users": 500,
        "response_format": "raw_json",
    }}]


def test_agent_runtime_single_snapshot_for_bitable_route_with_table_word_without_import_intent(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []

    def fake_execute_tool(context, request):
        captured.append({"name": request.tool_name, "params": dict(request.params)})
        if request.tool_name != "feishu_contact_organization_snapshot":
            raise AssertionError(f"unexpected tool {request.tool_name}")
        payload = {
            "app_token": "bascn-bitable-no-import",
            "users": [
                {"name": "小华", "department_names": ["行政部"], "leader_name": "邢总"},
            ],
        }
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.FEISHU_MOBILE,
            answer=json.dumps(payload),
            structured_result={"response_text": json.dumps(payload)},
        )

    monkeypatch.setattr(
        "app.services.agent.runtime.semantic_intent_for_question",
        lambda **_: SimpleNamespace(
            route_hint="bitable_qa",
            module_hint="people",
            canonical_question="起一张组织表看看先",
            source="test",
            confidence=0.96,
        ),
    )
    monkeypatch.setattr(
        "app.services.agent.runtime.resolve_bot_route",
        lambda *args, **kwargs: BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope"),
    )
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="起一张组织表看看先",
        normalized_command="起一张组织表看看先",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        require_write_confirmation=False,
    )

    assert [step.kind for step in result.trace.steps] == ["semantic", "route", "tool"]
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == ["feishu_contact_organization_snapshot"]
    assert captured == [{"name": "feishu_contact_organization_snapshot", "params": {
        "max_departments": 100,
        "max_users": 500,
        "response_format": "raw_json",
    }}]


def test_agent_runtime_generic_query_to_bitable_table_flow_with_query_rows_context(monkeypatch) -> None:
    captured: dict[str, dict[str, Any]] = {}

    def fake_execute_tool(context, request):
        captured[request.tool_name] = dict(request.params)
        if request.tool_name == "bitable_qa":
            payload = {
                "records": [
                    ["项目A", "已启动", "owner-01"],
                    ["项目B", "进行中", "owner-02"],
                ],
            }
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps(payload),
                structured_result={"response_text": json.dumps(payload)},
            )
        if request.tool_name == "feishu_bitable_table_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"table_id": "tbl-generic-query"}),
                structured_result={"response_text": json.dumps({"table_id": "tbl-generic-query"})},
            )
        if request.tool_name == "feishu_bitable_record_batch_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"created": 2}),
                structured_result={"response_text": json.dumps({"created": 2})},
            )
        raise AssertionError(f"unexpected tool {request.tool_name}")

    monkeypatch.setattr(
        "app.services.agent.runtime.semantic_intent_for_question",
        lambda **_: SimpleNamespace(
            route_hint="bitable_qa",
            module_hint="projects",
            canonical_question="查询一下项目清单，并创建一张项目清单表，把结果写入",
            source="test",
            confidence=0.97,
        ),
    )
    monkeypatch.setattr(
        "app.services.agent.runtime.resolve_bot_route",
        lambda *args, **kwargs: BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope"),
    )
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="查询一下项目清单，并创建一张项目清单表，把结果写入",
        normalized_command="查询一下项目清单，并创建一张项目清单表，把结果写入",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        require_write_confirmation=False,
    )

    assert [step.kind for step in result.trace.steps] == ["semantic", "route", "planner", "tool", "tool", "tool"]
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == [
        "bitable_qa",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert captured["feishu_bitable_table_create"]["fields"] == [{"name": "记录", "type": "text"}]
    assert captured["feishu_bitable_record_batch_create"]["rows"] == [
        ["项目A, 已启动, owner-01"],
        ["项目B, 进行中, owner-02"],
    ]
    assert captured["feishu_bitable_record_batch_create"]["table_id"] == "tbl-generic-query"
    assert tool_steps[-1].metadata["required"] is False


def test_agent_runtime_generic_query_to_bitable_table_flow_with_structured_records_columns(monkeypatch) -> None:
    captured: dict[str, dict[str, Any]] = {}

    def fake_execute_tool(context, request):
        captured[request.tool_name] = dict(request.params)
        if request.tool_name == "bitable_qa":
            payload = {
                "records": [
                    {"fields": {"名称": "项目A", "状态": "进行中", "负责人": "王小明"}},
                    {"fields": {"名称": "项目B", "状态": "已完成", "负责人": "张三"}},
                ],
            }
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps(payload),
                structured_result={"response_text": json.dumps(payload)},
            )
        if request.tool_name == "feishu_bitable_table_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"table_id": "tbl-generic-structured"}),
                structured_result={"response_text": json.dumps({"table_id": "tbl-generic-structured"})},
            )
        if request.tool_name == "feishu_bitable_record_batch_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"created": 2}),
                structured_result={"response_text": json.dumps({"created": 2})},
            )
        raise AssertionError(f"unexpected tool {request.tool_name}")

    monkeypatch.setattr(
        "app.services.agent.runtime.semantic_intent_for_question",
        lambda **_: SimpleNamespace(
            route_hint="bitable_qa",
            module_hint="projects",
            canonical_question="查项目并导出到表格",
            source="test",
            confidence=0.95,
        ),
    )
    monkeypatch.setattr(
        "app.services.agent.runtime.resolve_bot_route",
        lambda *args, **kwargs: BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope"),
    )
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="查项目并导出到表格",
        normalized_command="查项目并导出到表格",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        require_write_confirmation=False,
    )

    assert [step.kind for step in result.trace.steps] == ["semantic", "route", "planner", "tool", "tool", "tool"]
    assert captured["feishu_bitable_table_create"]["fields"] == [
        {"name": "名称", "type": "text"},
        {"name": "状态", "type": "text"},
        {"name": "负责人", "type": "text"},
    ]
    assert captured["feishu_bitable_record_batch_create"]["rows"] == [
        ["项目A", "进行中", "王小明"],
        ["项目B", "已完成", "张三"],
    ]


def test_agent_runtime_generic_query_to_bitable_table_flow_with_answer_payload_without_structured_rows(monkeypatch) -> None:
    captured: dict[str, dict[str, Any]] = {}

    def fake_execute_tool(context, request):
        captured[request.tool_name] = dict(request.params)
        if request.tool_name == "bitable_qa":
            payload = {
                "records": [
                    ["里程碑1", "已完成"],
                    ["里程碑2", "进行中"],
                ],
            }
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps(payload),
                structured_result={"tool_name": request.tool_name},
            )
        if request.tool_name == "feishu_bitable_table_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"table_id": "tbl-answer-only"}),
                structured_result={"response_text": json.dumps({"table_id": "tbl-answer-only"})},
            )
        if request.tool_name == "feishu_bitable_record_batch_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"created": 2}),
                structured_result={"response_text": json.dumps({"created": 2})},
            )
        raise AssertionError(f"unexpected tool {request.tool_name}")

    monkeypatch.setattr(
        "app.services.agent.runtime.semantic_intent_for_question",
        lambda **_: SimpleNamespace(
            route_hint="bitable_qa",
            module_hint="projects",
            canonical_question="查询里程碑并建表写入",
            source="test",
            confidence=0.95,
        ),
    )
    monkeypatch.setattr(
        "app.services.agent.runtime.resolve_bot_route",
        lambda *args, **kwargs: BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope"),
    )
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="查询里程碑并建表写入",
        normalized_command="查询里程碑并建表写入",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        require_write_confirmation=False,
    )

    assert [step.kind for step in result.trace.steps] == ["semantic", "route", "planner", "tool", "tool", "tool"]
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == [
        "bitable_qa",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert captured["feishu_bitable_record_batch_create"]["rows"] == [
        ["里程碑1, 已完成"],
        ["里程碑2, 进行中"],
    ]
    assert captured["feishu_bitable_record_batch_create"]["table_id"] == "tbl-answer-only"


def test_agent_runtime_generic_query_to_bitable_table_flow_with_export_term(monkeypatch) -> None:
    captured: dict[str, dict[str, Any]] = {}

    def fake_execute_tool(context, request):
        captured[request.tool_name] = dict(request.params)
        if request.tool_name == "bitable_qa":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"records": [["任务-导出", "owner-1"]]}),
                structured_result={"response_text": json.dumps({"records": [["任务-导出", "owner-1"]]})},
            )
        if request.tool_name == "feishu_bitable_table_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"table_id": "tbl-export"}),
                structured_result={"response_text": json.dumps({"table_id": "tbl-export"})},
            )
        if request.tool_name == "feishu_bitable_record_batch_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"created": 1}),
                structured_result={"response_text": json.dumps({"created": 1})},
            )
        raise AssertionError(f"unexpected tool {request.tool_name}")

    monkeypatch.setattr(
        "app.services.agent.runtime.semantic_intent_for_question",
        lambda **_: SimpleNamespace(
            route_hint="bitable_qa",
            module_hint="tasks",
            canonical_question="查询任务清单并导出到表格",
            source="test",
            confidence=0.95,
        ),
    )
    monkeypatch.setattr(
        "app.services.agent.runtime.resolve_bot_route",
        lambda *args, **kwargs: BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope"),
    )
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="查询任务清单并导出到表格",
        normalized_command="查询任务清单并导出到表格",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        require_write_confirmation=False,
    )

    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == [
        "bitable_qa",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert captured["feishu_bitable_record_batch_create"]["rows"] == [
        ["任务-导出, owner-1"],
    ]
    assert captured["feishu_bitable_record_batch_create"]["table_id"] == "tbl-export"


def test_agent_runtime_generic_query_to_bitable_table_flows_app_token_from_query_payload(monkeypatch) -> None:
    captured: dict[str, dict[str, Any]] = {}

    def fake_execute_tool(context, request):
        captured[request.tool_name] = dict(request.params)
        if request.tool_name == "bitable_qa":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"app_token": "bascn-query", "records": [["任务", "待办"]]}),
                structured_result={"response_text": json.dumps({"app_token": "bascn-query", "records": [["任务", "待办"]]})},
            )
        if request.tool_name == "feishu_bitable_table_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"table_id": "tbl-query-token"}),
                structured_result={"response_text": json.dumps({"table_id": "tbl-query-token"})},
            )
        if request.tool_name == "feishu_bitable_record_batch_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"created": 1}),
                structured_result={"response_text": json.dumps({"created": 1})},
            )
        raise AssertionError(f"unexpected tool {request.tool_name}")

    monkeypatch.setattr(
        "app.services.agent.runtime.semantic_intent_for_question",
        lambda **_: SimpleNamespace(
            route_hint="bitable_qa",
            module_hint="tasks",
            canonical_question="查询任务清单并导出到表",
            source="test",
            confidence=0.96,
        ),
    )
    monkeypatch.setattr(
        "app.services.agent.runtime.resolve_bot_route",
        lambda *args, **kwargs: BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope"),
    )
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="查询任务清单并导出到表",
        normalized_command="查询任务清单并导出到表",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        require_write_confirmation=False,
    )

    assert [step.name for step in result.trace.steps if step.kind == "tool"] == [
        "bitable_qa",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert captured["feishu_bitable_table_create"]["app_token"] == "bascn-query"
    assert captured["feishu_bitable_record_batch_create"]["table_id"] == "tbl-query-token"


def test_agent_runtime_generic_query_to_bitable_table_flow_with_empty_query_rows(monkeypatch) -> None:
    captured: dict[str, dict[str, Any]] = {}

    def fake_execute_tool(context, request):
        captured[request.tool_name] = dict(request.params)
        if request.tool_name == "bitable_qa":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"app_token": "bascn-empty", "message": "no records"}),
                structured_result={"response_text": json.dumps({"app_token": "bascn-empty", "message": "no records"})},
            )
        if request.tool_name == "feishu_bitable_table_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"table_id": "tbl-empty"}),
                structured_result={"response_text": json.dumps({"table_id": "tbl-empty"})},
            )
        if request.tool_name == "feishu_bitable_record_batch_create":
            return ToolResult(
                tool_name=request.tool_name,
                provider=ToolProvider.FEISHU_MOBILE,
                answer=json.dumps({"created": 0}),
                structured_result={"response_text": json.dumps({"created": 0})},
            )
        raise AssertionError(f"unexpected tool {request.tool_name}")

    monkeypatch.setattr(
        "app.services.agent.runtime.semantic_intent_for_question",
        lambda **_: SimpleNamespace(
            route_hint="bitable_qa",
            module_hint="tasks",
            canonical_question="查询任务清单并导出到表",
            source="test",
            confidence=0.94,
        ),
    )
    monkeypatch.setattr(
        "app.services.agent.runtime.resolve_bot_route",
        lambda *args, **kwargs: BotAnswerRoute(path="bitable_qa", scope="company", reason="permission_scope"),
    )
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="查询任务清单并导出到表",
        normalized_command="查询任务清单并导出到表",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        planner_enabled=True,
        require_write_confirmation=False,
    )

    assert [step.name for step in result.trace.steps if step.kind == "tool"] == [
        "bitable_qa",
        "feishu_bitable_table_create",
        "feishu_bitable_record_batch_create",
    ]
    assert captured["feishu_bitable_record_batch_create"]["app_token"] == "bascn-empty"
    assert captured["feishu_bitable_record_batch_create"]["rows"] == []


def test_agent_runtime_generic_query_to_bitable_table_flow_for_cross_domain_routes(monkeypatch) -> None:
    cases = (
        ("task_qa", "查询任务清单并创建任务清单表"),
        ("feishu_approval_task_query", "把待我处理的报销单查出来并创建审批表"),
        ("mail_qa", "把最近邮件查出来并创建邮件清单表"),
        ("approval_qa", "把待我处理的报销单查出来并创建审批清单表"),
        ("personal_tasks", "查一下我的待办事项并创建个人待办表"),
    )

    for route_path, question in cases:
        captured: dict[str, dict[str, Any]] = {}

        def fake_execute_tool(context, request):
            captured[request.tool_name] = dict(request.params)
            if request.tool_name == route_path:
                payload = {
                    "records": [
                        ["记录A", "OwnerA"],
                    ],
                }
                return ToolResult(
                    tool_name=request.tool_name,
                    provider=ToolProvider.FEISHU_MOBILE,
                    answer=json.dumps(payload),
                    structured_result={"response_text": json.dumps(payload)},
                )
            if request.tool_name == "feishu_bitable_table_create":
                return ToolResult(
                    tool_name=request.tool_name,
                    provider=ToolProvider.FEISHU_MOBILE,
                    answer=json.dumps({"table_id": f"tbl-{route_path}"}),
                    structured_result={"response_text": json.dumps({"table_id": f"tbl-{route_path}"})},
                )
            if request.tool_name == "feishu_bitable_record_batch_create":
                return ToolResult(
                    tool_name=request.tool_name,
                    provider=ToolProvider.FEISHU_MOBILE,
                    answer=json.dumps({"created": 1}),
                    structured_result={"response_text": json.dumps({"created": 1})},
                )
            raise AssertionError(f"unexpected tool {request.tool_name}")

        monkeypatch.setattr(
            "app.services.agent.runtime.semantic_intent_for_question",
            lambda **_: SimpleNamespace(
                route_hint=route_path,
                module_hint=(
                    "tasks" if route_path in {"task_qa", "personal_tasks"} else "approvals"
                ),
                canonical_question=question,
                source="test",
                confidence=0.95,
            ),
        )
        monkeypatch.setattr(
            "app.services.agent.runtime.resolve_bot_route",
            lambda *args, **kwargs: BotAnswerRoute(path=route_path, scope="company", reason="permission_scope"),
        )
        monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

        result = answer_agent_message_with_trace(
            None,
            company_id=uuid4(),
            question=question,
            normalized_command=question,
            chat_id="oc_group",
            actor=BotActor(role="owner", access_scope="company", domains=("all",)),
            planner_enabled=True,
            require_write_confirmation=False,
        )

        assert [step.name for step in result.trace.steps if step.kind == "tool"] == [
            route_path,
            "feishu_bitable_table_create",
            "feishu_bitable_record_batch_create",
        ]


def test_agent_runtime_routes_cross_domain_query_to_bitable_qa_without_route_stub(monkeypatch) -> None:
    cases = (
        "查一下我的待办任务，并新建一张待办清单表写进去",
        "把待我处理的报销单查出来，做一张审批表再同步进去",
        "把最近邮件查出来，创建邮件表格并填入",
        "我的待办同步到表",
    )

    for question in cases:
        def fake_execute_tool(context, request):
            if request.tool_name == "bitable_qa":
                payload = {"records": [["记录A", "OwnerA"]]}
                return ToolResult(
                    tool_name=request.tool_name,
                    provider=ToolProvider.FEISHU_MOBILE,
                    answer=json.dumps(payload),
                    structured_result={"response_text": json.dumps(payload)},
                )
            if request.tool_name == "feishu_bitable_table_create":
                return ToolResult(
                    tool_name=request.tool_name,
                    provider=ToolProvider.FEISHU_MOBILE,
                    answer=json.dumps({"table_id": "tbl-cross"}),
                    structured_result={"response_text": json.dumps({"table_id": "tbl-cross"})},
                )
            if request.tool_name == "feishu_bitable_record_batch_create":
                return ToolResult(
                    tool_name=request.tool_name,
                    provider=ToolProvider.FEISHU_MOBILE,
                    answer=json.dumps({"created": 1}),
                    structured_result={"response_text": json.dumps({"created": 1})},
                )
            raise AssertionError(f"unexpected tool {request.tool_name}")

        monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

        result = answer_agent_message_with_trace(
            None,
            company_id=uuid4(),
            question=question,
            normalized_command=question,
            chat_id="oc_group",
            actor=BotActor(role="owner", access_scope="company", domains=("all",)),
            planner_enabled=True,
            require_write_confirmation=False,
        )

        assert result.trace.route_path == "bitable_qa"
        assert [step.name for step in result.trace.steps if step.kind == "tool"] == [
            "bitable_qa",
            "feishu_bitable_table_create",
            "feishu_bitable_record_batch_create",
        ]


def test_agent_runtime_keeps_single_organization_snapshot_when_snapshot_text_lacks_write_terms(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []

    def fake_execute_tool(context, request):
        captured.append({"name": request.tool_name, "params": dict(request.params)})
        if request.tool_name != "feishu_contact_organization_snapshot":
            raise AssertionError(f"unexpected tool {request.tool_name}")
        payload = {
            "app_token": "bascn-single",
            "users": [
                {"name": "小芳", "department_names": ["运营部"], "leader_name": "刘总"},
            ],
        }
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.FEISHU_MOBILE,
            answer=json.dumps(payload),
            structured_result={"response_text": json.dumps(payload)},
        )

    monkeypatch.setattr(
        "app.services.agent.runtime.semantic_intent_for_question",
        lambda **_: SimpleNamespace(
            route_hint="feishu_contact_organization_snapshot",
            module_hint="people",
            canonical_question="请把最新组织放到表里",
            source="test",
            confidence=0.97,
        ),
    )
    monkeypatch.setattr(
        "app.services.agent.runtime.resolve_bot_route",
        lambda *args, **kwargs: BotAnswerRoute(path="feishu_contact_organization_snapshot", scope="company", reason="permission_scope"),
    )
    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="请把最新组织放到表里",
        normalized_command="请把最新组织放到表里",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
        require_write_confirmation=False,
    )

    assert [step.kind for step in result.trace.steps] == ["semantic", "route", "tool"]
    tool_steps = [step for step in result.trace.steps if step.kind == "tool"]
    assert [step.name for step in tool_steps] == ["feishu_contact_organization_snapshot"]
    assert captured == [{"name": "feishu_contact_organization_snapshot", "params": {
        "max_departments": 100,
        "max_users": 500,
        "response_format": "raw_json",
    }}]


def test_agent_runtime_routes_mail_question_to_mail_tool(monkeypatch) -> None:
    captured = {}

    _stub_tool(monkeypatch, answer="邮件摘要", capture=captured)

    answer = answer_agent_message(
        None,
        company_id=uuid4(),
        question="最近邮件有什么",
        normalized_command="最近邮件有什么",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
    )

    assert "邮件摘要" in answer
    assert captured["request"].tool_name == "mail_qa"


def test_agent_runtime_turns_user_identity_denial_into_authorization_guide(monkeypatch) -> None:
    def fake_execute_tool(context, request):
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.FEISHU_MCP,
            answer="需要授权",
            status=ToolExecutionStatus.DENIED,
            error="user_identity_authorization_required:user_identity_bundle",
            structured_result={
                "tool_name": request.tool_name,
                "response_text": "这个 Tool 是全局共享能力，但当前个人资源还没有完成本人授权。",
                "user_identity_authorization_required": True,
                "authorization_actions": [
                    {
                        "resource_type": "user_identity_bundle",
                        "label": "授权个人能力包",
                        "url": "http://127.0.0.1:8000/api/user-identity/oauth/feishu/start?company_id=c1&open_id=ou_1",
                        "instruction": "由资源所有者本人完成一次整体授权。",
                    },
                ],
                "final_answer_allowed": False,
                "final_answer_owner": "agent_runtime",
            },
            execution_source="MCP -> CLI -> Feishu",
            metadata={"tool_returns_structured_result": True, "final_answer_owner": "agent_runtime"},
        )

    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    answer = answer_agent_message(
        None,
        company_id=uuid4(),
        question="最近邮件有什么",
        normalized_command="最近邮件有什么",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="personal", open_id="ou_1"),
    )

    assert "范围：本人相关｜邮件问答" in answer
    assert "授权入口：" in answer
    assert "/api/user-identity/oauth/feishu/start?company_id=c1&open_id=ou_1" in answer
    assert "mail/gmail" not in answer
    assert "授权完成后，再问同一个问题" in answer


def test_agent_runtime_turns_personal_task_denial_into_feishu_authorization_guide(monkeypatch) -> None:
    def fake_execute_tool(context, request):
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.LOCAL,
            answer="需要授权",
            status=ToolExecutionStatus.DENIED,
            error="user_identity_authorization_required:user_identity_bundle",
            structured_result={
                "tool_name": request.tool_name,
                "response_text": "这个 Tool 是全局共享能力，但当前用户级能力包还没有完成本人整体授权。",
                "user_identity_authorization_required": True,
                "authorization_actions": [
                    {
                        "resource_type": "user_identity_bundle",
                        "label": "授权个人能力包",
                        "url": "http://127.0.0.1:8000/api/user-identity/oauth/feishu/start?company_id=c1&open_id=ou_1",
                        "instruction": "由资源所有者本人完成一次整体授权。",
                    },
                ],
                "final_answer_allowed": False,
                "final_answer_owner": "agent_runtime",
            },
            data_source="PostgreSQL",
            metadata={"tool_returns_structured_result": True, "final_answer_owner": "agent_runtime"},
        )

    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="我的待办是什么",
        normalized_command="我的待办是什么",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="personal", open_id="ou_1"),
    )

    assert "范围：本人相关｜本人相关事项" in result.answer
    assert "授权个人能力包" in result.answer
    assert "/api/user-identity/oauth/feishu/start?company_id=c1&open_id=ou_1" in result.answer
    assert "mail/gmail" not in result.answer
    actions = result.trace.steps[-1].metadata["structured_result"]["authorization_actions"]
    assert [item["resource_type"] for item in actions] == ["user_identity_bundle"]


def test_agent_runtime_turns_personal_calendar_denial_into_feishu_authorization_guide(monkeypatch) -> None:
    def fake_execute_tool(context, request):
        return ToolResult(
            tool_name=request.tool_name,
            provider=ToolProvider.FEISHU_MCP,
            answer="需要授权",
            status=ToolExecutionStatus.DENIED,
            error="user_identity_authorization_required:user_identity_bundle",
            structured_result={
                "tool_name": request.tool_name,
                "response_text": "这个 Tool 是全局共享能力，但当前用户级能力包还没有完成本人整体授权。",
                "user_identity_authorization_required": True,
                "authorization_actions": [
                    {
                        "resource_type": "user_identity_bundle",
                        "label": "授权个人能力包",
                        "url": "http://127.0.0.1:8000/api/user-identity/oauth/feishu/start?company_id=c1&open_id=ou_1",
                        "instruction": "由资源所有者本人完成一次整体授权。",
                    },
                ],
                "final_answer_allowed": False,
                "final_answer_owner": "agent_runtime",
            },
            execution_source="MCP -> CLI -> Feishu",
            metadata={"tool_returns_structured_result": True, "final_answer_owner": "agent_runtime"},
        )

    monkeypatch.setattr("app.services.agent.runtime.execute_agent_tool", fake_execute_tool)

    result = answer_agent_message_with_trace(
        None,
        company_id=uuid4(),
        question="我的日程有哪些",
        normalized_command="我的日程有哪些",
        chat_id="oc_group",
        actor=BotActor(role="member", access_scope="personal", open_id="ou_1"),
    )

    assert "范围：本人相关｜日程问答" in result.answer
    assert "授权个人能力包" in result.answer
    assert "mail/gmail" not in result.answer
    assert result.trace.route_path == "calendar_qa"
    actions = result.trace.steps[-1].metadata["structured_result"]["authorization_actions"]
    assert [item["resource_type"] for item in actions] == ["user_identity_bundle"]


def test_agent_runtime_routes_bitable_question_to_bitable_tool(monkeypatch) -> None:
    captured = {}

    _stub_tool(monkeypatch, answer="多维表格摘要", capture=captured)

    answer = answer_agent_message(
        None,
        company_id=uuid4(),
        question="多维表格里客户回款怎么样",
        normalized_command="多维表格里客户回款怎么样",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
    )

    assert "多维表格摘要" in answer
    assert captured["request"].tool_name == "bitable_qa"


def test_agent_runtime_routes_project_progress_to_bitable_tool(monkeypatch) -> None:
    captured = {}

    _stub_tool(monkeypatch, answer="项目主数据摘要", capture=captured)

    answer = answer_agent_message(
        None,
        company_id=uuid4(),
        question="项目进展怎么样",
        normalized_command="项目进展怎么样",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
    )

    assert "项目主数据摘要" in answer
    assert captured["request"].tool_name == "bitable_qa"
    assert captured["request"].normalized_command.startswith("module:projects")


def test_agent_runtime_routes_company_task_question_to_task_tool(monkeypatch) -> None:
    captured = {}

    _stub_tool(monkeypatch, answer="任务摘要", capture=captured)

    answer = answer_agent_message(
        None,
        company_id=uuid4(),
        question="公司现在有哪些任务",
        normalized_command="公司现在有哪些任务",
        chat_id="oc_group",
        actor=BotActor(role="owner", access_scope="company", domains=("all",)),
    )

    assert "任务摘要" in answer
    assert captured["request"].tool_name == "task_qa"


def test_scope_label_for_employee_answers() -> None:
    member = BotActor(role="member", access_scope="chat")
    personal = BotActor(role="member", access_scope="personal")
    domain_member = BotActor(role="member", access_scope="domain", domains=("sales",))

    assert answer_scope_label(actor=member, route_scope="chat", chat_id="oc_group") == "当前群"
    assert answer_scope_label(actor=member, route_scope="chat", chat_id=None) == "当前会话"
    assert answer_scope_label(actor=personal, route_scope="chat", chat_id="oc_group") == "本人相关"
    assert answer_scope_label(actor=domain_member, route_scope="domain", chat_id="oc_group") == "授权业务域"


def test_scope_label_for_manager_answers() -> None:
    company_admin = BotActor(role="admin", access_scope="company")
    department_manager = BotActor(role="manager", access_scope="department", domains=("rd",))
    domain_manager = BotActor(role="manager", access_scope="domain", domains=("finance",))

    assert answer_scope_label(actor=company_admin, route_scope="company") == "授权公司"
    assert answer_scope_label(actor=department_manager, route_scope="chat") == "授权部门"
    assert answer_scope_label(actor=domain_manager, route_scope="domain") == "授权业务域"


def test_scope_label_for_owner_answers() -> None:
    owner_all = BotActor(role="owner", access_scope="all")
    owner_company = BotActor(role="owner", access_scope="company")

    assert answer_scope_label(actor=owner_all, route_scope="company") == "全部公司"
    assert answer_scope_label(actor=owner_company, route_scope="company") == "指定公司"


def test_with_scope_label_is_idempotent() -> None:
    answer = "回答范围：授权业务域\n已有回答"

    assert with_scope_label(answer, "授权业务域", route_label="审批问答") == answer


def test_with_scope_label_uses_compact_format() -> None:
    assert with_scope_label("已有回答", "指定公司", route_label="公司级问答") == "范围：指定公司｜公司级问答\n已有回答"


def test_answer_route_label_for_capabilities() -> None:
    assert answer_route_label("chat_summary") == "当前群总结"
    assert answer_route_label("chat_tasks") == "当前群待办"
    assert answer_route_label("personal_tasks") == "本人相关事项"
    assert answer_route_label("domain_qa") == "授权业务域问答"
    assert answer_route_label("approval_qa") == "审批问答"
    assert answer_route_label("feishu_approval_task_query") == "审批实时待办"
    assert answer_route_label("owner_cockpit") == "老板驾驶舱"
    assert answer_route_label("feishu_task_create") == "创建任务"






