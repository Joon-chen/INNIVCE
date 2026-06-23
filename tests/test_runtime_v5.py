from uuid import uuid4

import pytest

from app.services.runtime_v5.models import (
    CommandPlan,
    ComposedAnswer,
    ExecutionIdentityContract,
    IntentResult,
    PermissionDecision,
    PlannerResult,
    ProviderRequest,
    ProviderResult,
    ResultContext,
    RuntimeResult,
    RuntimeContext,
    RuntimeIdentity,
    RuntimeScope,
)
from app.services.cognitive_foundation import append_workspace_cognitive_event
from app.services.runtime_v5.feishu_resource_providers import FeishuBaseProvider, FeishuCalendarProvider, FeishuTaskProvider
from app.services.runtime_v5.capability_router import CapabilityRouter
from app.services.runtime_v5.interaction_layer import interaction_payload_from_runtime_result, interaction_payload_payload
from app.services.runtime_v5.intent import recognize_intent
from app.services.runtime_v5.llm_intent import LLMCommandIntentCandidate, validate_llm_command_intent
from app.services.runtime_v5.permission import check_runtime_permission
from app.services.runtime_v5.runtime import run_runtime_v5
from app.services.runtime_v5.runtime_action_input import build_runtime_action_input_payload, runtime_action_input_from_payload
from app.services.runtime_v5.runtime_missing_params import (
    TEXT,
    USER,
    action_input_missing_params,
    missing_param_contract,
    pending_action_with_user_input,
    resolve_missing_param_value,
    waiting_input_still_missing,
)
from app.services.runtime_v5.runtime_pending_action import (
    pending_action_from_runtime_action_input,
    runtime_pending_action_payload,
    runtime_pending_action_with_missing_input,
)
from app.services.runtime_v5.runtime_result import build_runtime_result, runtime_result_from_payload, runtime_result_payload
from app.services.runtime_v5.runtime_state import pending_action_from_runtime_state, waiting_input_action_from_runtime_state
from app.services.tools.base import ToolExecutionStatus


def _context(
    message: str,
    *,
    result_context: ResultContext | None = None,
    session_context: dict | None = None,
    chat_id: str | None = None,
) -> RuntimeContext:
    company_id = uuid4()
    return RuntimeContext(
        identity=RuntimeIdentity(open_id="ou_test", role="owner", domains=("all",)),
        runtime_scope=RuntimeScope(company_ids=(company_id,), active_company_id=company_id),
        current_message=message,
        session_context=session_context or {},
        result_context=result_context,
        chat_id=chat_id,
    )


class _RuntimeWriteDb:
    def __init__(self) -> None:
        self.added = []

    def add(self, item) -> None:
        self.added.append(item)

    def flush(self) -> None:
        return None

    def scalars(self, query):
        return _RuntimeScalarResult(self.added)


class _RuntimeScalarResult:
    def __init__(self, items) -> None:
        self._items = list(items)

    def all(self):
        return list(self._items)


def _command_plan(
    strategy: str,
    *,
    result_type: str | None = None,
    target_ui: str = "card",
    question_type: str = "query",
    data_scope: str = "self",
    sources: tuple[str, ...] = ("approval",),
) -> CommandPlan:
    intent = result_type or strategy
    return CommandPlan(
        intent=intent,
        steps=(),
        target_ui=target_ui,  # type: ignore[arg-type]
        tool_candidates=(),
        context_scope={"company_id": "company_1", "mode": "single_company"},
        intent_result=IntentResult(
            question_type=question_type,  # type: ignore[arg-type]
            intent=intent,
            data_scope=data_scope,  # type: ignore[arg-type]
            confidence=0.9,
            canonical_question=intent,
        ),
        planner_result=PlannerResult(strategy=strategy, sources=sources),
    )


def _assert_runtime_state_company_id(runtime_state: dict, company_id: str = "company_1") -> None:
    assert runtime_state["metadata"]["company_id"] == company_id
    assert runtime_state["actions"][0]["metadata"]["company_id"] == company_id
    pending_action = runtime_state["actions"][0]["metadata"].get("pending_action")
    if isinstance(pending_action, dict):
        assert pending_action["company_id"] == company_id


def test_runtime_v5_approval_query_runs_strategy_sources() -> None:
    seen_company_ids = []

    class ApprovalProvider:
        source = "approval"
        _OPERATIONS = {"list_pending": ("approval.list_pending", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            assert request.operation == "list_pending"
            assert request.context.runtime_scope.active_company_id is not None
            seen_company_ids.append(str(request.context.runtime_scope.active_company_id))
            return ProviderResult(
                source="approval",
                status="success",
                result_type="approval_list",
                count=1,
                items=({"title": "付款审批", "applicant": "张三"},),
                answer="你有 1 条待审批。",
            )

    result = run_runtime_v5(
        context=_context("待我审批有哪些"),
        providers={"approval": ApprovalProvider()},
    )

    assert result.intent.intent == "approval_query"
    assert result.plan.strategy == "approval_query"
    assert result.execution is not None
    assert result.execution.status == "success"
    assert result.composed.result_context is not None
    assert result.composed.result_context.result_type == "approval_list"
    assert result.composed.result_context.metadata["company_id"] == seen_company_ids[0]
    assert result.composed.result_context.items[0]["company_id"] == seen_company_ids[0]
    runtime_result = result.composed.metadata["runtime_result"]
    assert runtime_result["target_ui"] == "card"
    assert runtime_result["metadata"]["company_id"] == seen_company_ids[0]
    assert runtime_result["actions"][0]["action"] == "open_detail"
    assert runtime_result["actions"][0]["target_ui"] == "sidepanel"
    runtime_state = result.composed.result_context.metadata["runtime_state"]
    assert runtime_state["status"] == "done"
    assert runtime_state["intent"] == "approval_query"
    assert runtime_state["actions"] == ()


def test_runtime_v5_explicit_task_create_wins_over_meeting_words() -> None:
    intent = recognize_intent("创建一个任务：明天4点开会", _context("创建一个任务：明天4点开会"))

    assert intent.intent == "task_create"
    assert intent.question_type == "action"
    assert intent.entities["summary"] == "明天4点开会"


def test_runtime_v5_calendar_create_still_handles_explicit_meeting() -> None:
    intent = recognize_intent("创建一个会议：明天4点开会", _context("创建一个会议：明天4点开会"))

    assert intent.intent == "calendar_create"
    assert intent.question_type == "action"
    assert intent.entities["summary"] == "会议"
    assert intent.entities["start"]
    assert intent.entities["end"]


def test_runtime_v5_command_llm_candidate_can_resolve_generic_company_task_query(monkeypatch) -> None:
    def fake_candidate(**kwargs):
        return LLMCommandIntentCandidate(
            question_type="query",
            intent="task_query",
            data_scope="company",
            entities={"topic": "任务负荷"},
            missing_params=(),
            confidence=0.91,
            canonical_question="查看公司任务负荷",
        )

    monkeypatch.setattr("app.services.runtime_v5.llm_intent.llm_command_intent_candidate", fake_candidate)

    intent = recognize_intent("帮我看看企业工作负荷", _context("帮我看看企业工作负荷"))

    assert intent.intent == "task_query"
    assert intent.question_type == "query"
    assert intent.data_scope == "company"
    assert intent.entities == {"topic": "任务负荷"}
    assert intent.canonical_question == "查看公司任务负荷"


def test_runtime_v5_command_llm_candidate_does_not_override_confident_action(monkeypatch) -> None:
    called = False

    def fake_candidate(**kwargs):
        nonlocal called
        called = True
        return LLMCommandIntentCandidate(
            question_type="query",
            intent="calendar_query",
            data_scope="company",
            confidence=0.95,
            canonical_question="查看公司日程",
        )

    monkeypatch.setattr("app.services.runtime_v5.llm_intent.llm_command_intent_candidate", fake_candidate)

    intent = recognize_intent("创建一个任务：明天4点开会", _context("创建一个任务：明天4点开会"))

    assert called is False
    assert intent.intent == "task_create"
    assert intent.question_type == "action"


def test_runtime_v5_command_llm_enriches_confident_business_query(monkeypatch) -> None:
    called = False

    def fake_candidate(**kwargs):
        nonlocal called
        called = True
        return LLMCommandIntentCandidate(
            question_type="query",
            intent="task_query",
            data_scope="company",
            entities={"status": "open"},
            missing_params=(),
            confidence=0.93,
            canonical_question="查看公司当前未完成任务负荷",
            business_domain="Workspace",
            capability="task_query",
            objective="查看公司任务负荷和风险",
            constraints={"status": "open"},
            time_range={"preset": "current"},
            output_preferences={"detail_level": "summary", "group_by": "owner"},
            semantic_tags=("workload", "risk"),
        )

    monkeypatch.setattr("app.services.runtime_v5.llm_intent.llm_command_intent_candidate", fake_candidate)

    intent = recognize_intent("全公司任务", _context("全公司任务"))

    assert called is True
    assert intent.intent == "task_query"
    assert intent.data_scope == "company"
    assert intent.entities["status"] == "open"
    enrichment = intent.entities["command_enrichment"]
    assert enrichment["business_domain"] == "Workspace"
    assert enrichment["objective"] == "查看公司任务负荷和风险"
    assert enrichment["constraints"] == {"status": "open"}
    assert enrichment["output_preferences"]["group_by"] == "owner"
    assert enrichment["semantic_tags"] == ["workload", "risk"]


def test_runtime_v5_command_llm_cannot_reroute_confident_business_query(monkeypatch) -> None:
    def fake_candidate(**kwargs):
        return LLMCommandIntentCandidate(
            question_type="query",
            intent="calendar_query",
            data_scope="company",
            confidence=0.95,
            canonical_question="查看公司日程",
        )

    monkeypatch.setattr("app.services.runtime_v5.llm_intent.llm_command_intent_candidate", fake_candidate)

    intent = recognize_intent("全公司任务", _context("全公司任务"))

    assert intent.intent == "task_query"
    assert "command_enrichment" not in intent.entities


def test_runtime_v5_command_llm_validator_rejects_unknown_or_low_confidence_candidates() -> None:
    rule_intent = IntentResult(
        question_type="query",
        intent="general_query",
        data_scope="company",
        confidence=0.55,
        canonical_question="看看公司情况",
    )

    unknown = validate_llm_command_intent(
        LLMCommandIntentCandidate(
            question_type="query",
            intent="provider_direct_execute",
            data_scope="company",
            confidence=0.99,
            canonical_question="非法候选",
        ),
        rule_intent=rule_intent,
    )
    low_confidence = validate_llm_command_intent(
        LLMCommandIntentCandidate(
            question_type="query",
            intent="task_query",
            data_scope="company",
            confidence=0.5,
            canonical_question="低置信候选",
        ),
        rule_intent=rule_intent,
    )

    assert unknown is None
    assert low_confidence is None


def test_runtime_v5_command_llm_low_confidence_with_missing_params_guides_clarification() -> None:
    rule_intent = IntentResult(
        question_type="analysis",
        intent="general_analysis",
        data_scope="company",
        confidence=0.78,
        canonical_question="看看情况",
    )

    validated = validate_llm_command_intent(
        LLMCommandIntentCandidate(
            question_type="query",
            intent="task_query",
            data_scope="company",
            missing_params=("scope", "time_range"),
            clarification="你想看哪个范围、哪个时间段的任务？",
            confidence=0.52,
            canonical_question="查看任务",
        ),
        rule_intent=rule_intent,
    )

    assert validated is not None
    assert validated.intent == "task_query"
    assert validated.needs_clarification is True
    assert validated.missing_params == ("scope", "time_range")
    assert validated.entities["clarification_prompt"] == "你想看哪个范围、哪个时间段的任务？"


def test_runtime_v5_command_llm_clarification_does_not_execute_provider(monkeypatch) -> None:
    provider_called = False

    def fake_candidate(**kwargs):
        return LLMCommandIntentCandidate(
            question_type="query",
            intent="task_query",
            data_scope="company",
            missing_params=("scope", "time_range"),
            clarification="你想看哪个范围、哪个时间段的任务？",
            confidence=0.52,
            canonical_question="查看任务",
        )

    class TaskProvider:
        source = "task"

        def execute(self, request: ProviderRequest) -> ProviderResult:
            nonlocal provider_called
            provider_called = True
            return ProviderResult(source="task", status="success", result_type="task_list", count=1)

    monkeypatch.setattr("app.services.runtime_v5.llm_intent.llm_command_intent_candidate", fake_candidate)

    result = run_runtime_v5(
        context=_context("帮我看看工作安排"),
        providers={"task": TaskProvider()},
    )

    assert provider_called is False
    assert result.execution is None
    assert result.intent.intent == "task_query"
    assert result.intent.needs_clarification is True
    assert result.composed.answer == "你想看哪个范围、哪个时间段的任务？"
    assert result.composed.result_context is not None
    assert result.composed.result_context.metadata["execution_status"] == "clarification"
    assert result.composed.result_context.metadata["missing_params"] == ["scope", "time_range"]
    assert result.composed.result_context.metadata["clarification_prompt"] == "你想看哪个范围、哪个时间段的任务？"
    assert result.composed.result_context.items[0]["clarification_prompt"] == "你想看哪个范围、哪个时间段的任务？"
    option_values = {item["value"] for item in result.composed.result_context.metadata["clarification_options"]}
    assert {"self", "department", "company", "today", "this_week", "this_month"}.issubset(option_values)


def test_runtime_v5_command_llm_validator_does_not_escalate_query_to_action() -> None:
    rule_intent = IntentResult(
        question_type="query",
        intent="general_query",
        data_scope="company",
        confidence=0.55,
        canonical_question="帮我看看工作安排",
    )

    validated = validate_llm_command_intent(
        LLMCommandIntentCandidate(
            question_type="action",
            intent="task_create",
            data_scope="self",
            confidence=0.95,
            canonical_question="创建任务",
        ),
        rule_intent=rule_intent,
    )

    assert validated is None


def test_runtime_v5_company_task_query_recognizes_company_scope() -> None:
    intent = recognize_intent("查看全公司任务", _context("查看全公司任务"))

    assert intent.intent == "task_query"
    assert intent.question_type == "query"
    assert intent.data_scope == "company"


def test_runtime_v5_company_calendar_query_recognizes_company_scope() -> None:
    intent = recognize_intent("查看全公司日程", _context("查看全公司日程"))

    assert intent.intent == "calendar_query"
    assert intent.question_type == "query"
    assert intent.data_scope == "company"


def test_runtime_v5_provider_request_carries_execution_identity_contract() -> None:
    seen_contracts = []

    class TaskProvider:
        source = "task"
        _OPERATIONS = {"complete_task": ("task.complete_task", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            seen_contracts.append(request.execution_identity_contract.payload())
            assert request.params["execution_identity_contract"]["credential_mode"] == "USER_TOKEN"
            return ProviderResult(source="task", status="success", result_type="task_complete", answer="done")

    context = _context("完成任务")
    intent = IntentResult(
        question_type="action",
        intent="task_complete",
        data_scope="self",
        confidence=0.9,
        canonical_question="完成任务",
    )
    plan = PlannerResult(strategy="task_complete", sources=("task",))
    permission = PermissionDecision(allowed=True, requires_confirmation=True, execution_identity="user")

    result = CapabilityRouter({"task": TaskProvider()}).execute(
        context=context,
        intent=intent,
        plan=plan,
        permission=permission,
    )

    assert result.status == "success"
    assert seen_contracts == [
        {
            "actor_identity": "USER",
            "credential_mode": "USER_TOKEN",
            "credential_owner": {
                "company_id": str(context.runtime_scope.active_company_id),
                "open_id": "ou_test",
                "user_id": "",
                "cli_profile": "",
            },
            "resource_scope": "SELF",
            "requires_authorization": True,
            "allows_cli_fallback": True,
            "authorization_status": "UNKNOWN",
            "fallback_used": False,
            "reason": "task_complete:task.complete_task represents a user-owned resource or user action.",
        }
    ]


def test_runtime_query_identity_policy_is_bot_first_for_query_strategies() -> None:
    query_strategies = (
        ("task_query", ("task",), True),
        ("calendar_query", ("calendar",), True),
        ("mail_query", ("mail",), True),
        ("attendance_query", ("attendance",), True),
        ("slides_read", ("slides",), False),
        ("whiteboard_read", ("whiteboard",), False),
    )
    context = _context("查询")

    for strategy, sources, user_fallback_allowed in query_strategies:
        intent = IntentResult(
            question_type="query",
            intent=strategy,
            data_scope="self",
            confidence=0.9,
            canonical_question=strategy,
        )
        plan = PlannerResult(strategy=strategy, sources=sources)

        permission = check_runtime_permission(context=context, intent=intent, plan=plan)

        assert permission.execution_identity == "bot"
        assert permission.metadata["query_identity_policy"] == "bot_first"
        assert permission.metadata["execution_identity_source"] == "bot_first_query_policy"
        assert permission.metadata["user_fallback_allowed"] is user_fallback_allowed


def test_workspace_policy_preflight_outputs_subject_scope_and_identity_metadata() -> None:
    company_id = uuid4()
    context = RuntimeContext(
        identity=RuntimeIdentity(
            user_id="user_1",
            open_id="ou_workspace",
            role="owner",
            department_id="dept_1",
            domains=("workspace",),
        ),
        runtime_scope=RuntimeScope(company_ids=(company_id,), active_company_id=company_id),
        current_message="查看我的任务",
    )
    intent = IntentResult(
        question_type="query",
        intent="task_query",
        data_scope="self",
        confidence=0.9,
        canonical_question="查看我的任务",
    )
    plan = PlannerResult(strategy="task_query", sources=("task",))

    permission = check_runtime_permission(context=context, intent=intent, plan=plan)

    assert permission.allowed is True
    assert permission.execution_identity == "bot"
    assert permission.metadata["policy_subject"] == {
        "actor_user_id": "user_1",
        "actor_open_id": "ou_workspace",
        "company_id": str(company_id),
        "role": "owner",
        "departments": ["dept_1"],
        "managed_departments": [],
        "is_owner": True,
        "is_admin": False,
    }
    assert permission.metadata["policy_scope"] == {
        "requested_scope": "self",
        "resolved_scope": "self",
        "target_user_id": "",
        "target_department_id": "",
        "target_company_id": str(company_id),
        "target_group_id": "",
    }
    assert permission.metadata["identity_decision"] == {
        "actor_identity": "BOT",
        "credential_mode": "TENANT_TOKEN",
        "allows_fallback": True,
        "requires_authorization": False,
        "authorization_status": "AUTHORIZED",
    }
    assert permission.metadata["allowed_resource_types"] == ["task"]
    assert permission.metadata["denied_resource_types"] == []


def test_workspace_company_query_preflight_blocks_current_user_fallback_for_member() -> None:
    company_id = uuid4()
    context = RuntimeContext(
        identity=RuntimeIdentity(open_id="ou_member", role="member"),
        runtime_scope=RuntimeScope(company_ids=(company_id,), active_company_id=company_id),
        current_message="查看全公司任务",
    )
    intent = IntentResult(
        question_type="query",
        intent="task_query",
        data_scope="company",
        confidence=0.9,
        canonical_question="查看全公司任务",
    )
    plan = PlannerResult(strategy="task_query", sources=("task",))

    permission = check_runtime_permission(context=context, intent=intent, plan=plan)

    assert permission.allowed is False
    assert permission.reason == "permission_denied"
    assert permission.metadata["policy_scope"]["requested_scope"] == "company"
    assert permission.metadata["identity_decision"]["actor_identity"] == "BOT"
    assert permission.metadata["identity_decision"]["allows_fallback"] is False
    assert permission.metadata["denied_resource_types"] == ["task"]


def test_workspace_company_task_query_returns_enterprise_realtime_provider_gap() -> None:
    company_id = uuid4()
    context = RuntimeContext(
        identity=RuntimeIdentity(open_id="ou_owner", role="owner"),
        runtime_scope=RuntimeScope(company_ids=(company_id,), active_company_id=company_id),
        current_message="查看全公司任务",
    )
    intent = IntentResult(
        question_type="query",
        intent="task_query",
        data_scope="company",
        confidence=0.9,
        canonical_question="查看全公司任务",
    )
    request = ProviderRequest(
        source="task",
        operation="list_my_tasks",
        intent=intent,
        planner=PlannerResult(strategy="task_query", sources=("task",)),
        context=context,
        execution_identity="bot",
        execution_identity_contract=ExecutionIdentityContract(
            actor_identity="BOT",
            credential_mode="TENANT_TOKEN",
            resource_scope="COMPANY",
            authorization_status="AUTHORIZED",
        ),
    )

    result = FeishuTaskProvider(db=None).execute(request)  # type: ignore[arg-type]

    assert result.status == "denied"
    assert result.error == "enterprise_realtime_not_integrated"
    assert result.metadata["error_type"] == "enterprise_realtime_not_integrated"
    assert result.metadata["workevent_as_realtime_source"] is False
    assert result.metadata["extracted_item_as_realtime_source"] is False
    assert result.metadata["legacy_cli_fallback_used"] is False
    assert result.metadata["user_fallback_allowed"] is False
    assert "不会改用本地认知数据或当前用户本机身份代查" in result.answer


def test_workspace_company_task_query_returns_cognitive_aggregation_when_projection_exists() -> None:
    company_id = uuid4()
    db = _RuntimeWriteDb()
    append_workspace_cognitive_event(
        db,
        company_id=company_id,
        object_type="task",
        object_id="task-1",
        source="feishu_user_observation",
        actor="ou_owner",
        raw_payload={"task_guid": "task-1", "title": "逾期任务", "status": "todo", "due_at": "2026-06-20T10:00:00+00:00"},
        owner_user_id="user-1",
        owner_open_id="ou_1",
        owner_department_id="dept-1",
    )
    append_workspace_cognitive_event(
        db,
        company_id=company_id,
        object_type="calendar",
        object_id="event-1",
        source="feishu_user_observation",
        actor="ou_owner",
        raw_payload={
            "event_id": "event-1",
            "title": "冲突会议",
            "start_at": "2026-06-23T02:00:00+00:00",
            "end_at": "2026-06-23T03:00:00+00:00",
            "is_conflict": True,
        },
        owner_user_id="user-1",
        owner_open_id="ou_1",
        owner_department_id="dept-1",
    )
    context = RuntimeContext(
        identity=RuntimeIdentity(open_id="ou_owner", role="owner"),
        runtime_scope=RuntimeScope(company_ids=(company_id,), active_company_id=company_id),
        current_message="查看全公司任务",
    )
    intent = IntentResult(
        question_type="query",
        intent="task_query",
        data_scope="company",
        confidence=0.9,
        canonical_question="查看全公司任务",
    )
    request = ProviderRequest(
        source="task",
        operation="list_my_tasks",
        intent=intent,
        planner=PlannerResult(strategy="task_query", sources=("task",)),
        context=context,
        execution_identity="bot",
        execution_identity_contract=ExecutionIdentityContract(
            actor_identity="BOT",
            credential_mode="TENANT_TOKEN",
            resource_scope="COMPANY",
            authorization_status="AUTHORIZED",
        ),
    )

    result = FeishuTaskProvider(db=db).execute(request)  # type: ignore[arg-type]

    assert result.status == "success"
    assert result.result_type == "workspace_aggregation_summary"
    assert result.metadata["provider_boundary"] == "workspace_cognitive_aggregation"
    assert result.metadata["realtime_provider_boundary"] == "enterprise_realtime_not_integrated"
    assert result.metadata["operational_source"] == "workspace_cognitive_projection"
    assert result.metadata["workevent_as_realtime_source"] is False
    assert result.metadata["user_fallback_allowed"] is False
    item = result.items[0]
    assert item["resource_plane"] == "cognitive"
    assert item["detail_available"] is False
    assert item["operational_detail_available"] is False
    assert item["metrics"]["task_total"] == 1
    assert item["metrics"]["overdue_task_count"] == 1
    assert item["metrics"]["calendar_conflict_count"] == 1
    assert "这不是飞书实时明细" in result.answer


def test_workspace_company_calendar_query_returns_enterprise_realtime_provider_gap() -> None:
    company_id = uuid4()
    context = RuntimeContext(
        identity=RuntimeIdentity(open_id="ou_owner", role="owner"),
        runtime_scope=RuntimeScope(company_ids=(company_id,), active_company_id=company_id),
        current_message="查看全公司日程",
    )
    intent = IntentResult(
        question_type="query",
        intent="calendar_query",
        data_scope="company",
        confidence=0.9,
        canonical_question="查看全公司日程",
    )
    request = ProviderRequest(
        source="calendar",
        operation="list_events",
        intent=intent,
        planner=PlannerResult(strategy="calendar_query", sources=("calendar",)),
        context=context,
        execution_identity="bot",
        execution_identity_contract=ExecutionIdentityContract(
            actor_identity="BOT",
            credential_mode="TENANT_TOKEN",
            resource_scope="COMPANY",
            authorization_status="AUTHORIZED",
        ),
    )

    result = FeishuCalendarProvider(db=None).execute(request)  # type: ignore[arg-type]

    assert result.status == "denied"
    assert result.error == "enterprise_realtime_not_integrated"
    assert result.metadata["error_type"] == "enterprise_realtime_not_integrated"
    assert result.metadata["workevent_as_realtime_source"] is False
    assert result.metadata["legacy_cli_fallback_used"] is False
    assert result.metadata["user_fallback_allowed"] is False


def test_runtime_query_identity_contract_ignores_user_requested_identity() -> None:
    seen_contracts = []
    seen_identities = []

    class MailProvider:
        source = "mail"
        _OPERATIONS = {"list_recent": ("mail.list_recent", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            seen_identities.append(request.execution_identity)
            seen_contracts.append(request.execution_identity_contract.payload())
            return ProviderResult(source="mail", status="success", result_type="mail_list", answer="ok")

    context = _context("查邮件")
    intent = IntentResult(
        question_type="query",
        intent="mail_query",
        data_scope="self",
        confidence=0.9,
        canonical_question="查邮件",
        entities={"execution_identity": "user"},
    )
    plan = PlannerResult(strategy="mail_query", sources=("mail",))
    permission = check_runtime_permission(context=context, intent=intent, plan=plan)

    result = CapabilityRouter({"mail": MailProvider()}).execute(
        context=context,
        intent=intent,
        plan=plan,
        permission=permission,
    )

    assert result.status == "success"
    assert permission.execution_identity == "bot"
    assert seen_identities == ["bot"]
    assert seen_contracts[0]["actor_identity"] == "BOT"
    assert seen_contracts[0]["credential_mode"] == "TENANT_TOKEN"
    assert seen_contracts[0]["requires_authorization"] is False
    assert seen_contracts[0]["allows_cli_fallback"] is False


def test_runtime_self_query_enterprise_realtime_gap_returns_user_fallback_authorization() -> None:
    class TaskProvider:
        source = "task"
        _OPERATIONS = {"list_my_tasks": ("task.list_my_tasks", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            return ProviderResult(
                source="task",
                status="denied",
                result_type="task_list",
                count=0,
                metadata={
                    "operation": request.operation,
                    "credential_mode": "TENANT_TOKEN",
                    "actor_identity": "BOT",
                    "execution_identity_contract": request.execution_identity_contract.payload(),
                    "error_type": "enterprise_realtime_not_integrated",
                    "provider_boundary": "enterprise_realtime_not_integrated",
                    "user_fallback_allowed": True,
                    "legacy_cli_fallback_used": False,
                },
                answer="企业实时读取能力未接入。",
                error="enterprise_realtime_not_integrated",
            )

    result = run_runtime_v5(
        context=_context("我的任务"),
        providers={"task": TaskProvider()},
    )

    assert result.execution is not None
    assert result.execution.status == "denied"
    assert result.composed.result_context is not None
    assert result.composed.result_context.result_type == "waiting_authorization"
    provider = result.composed.result_context.metadata["provider_results"][0]
    assert provider["waiting_authorization"] is True
    assert provider["credential_mode"] == "USER_TOKEN"
    assert provider["provider_boundary"] == "user_token_fallback_required"
    runtime_result = result.composed.metadata["runtime_result"]
    assert runtime_result["result_type"] == "waiting_authorization"
    assert runtime_result["status"] == "waiting_authorization"
    assert runtime_result["metadata"]["authorization"]["credential_mode"] == "USER_TOKEN"
    assert runtime_result["metadata"]["authorization"]["can_escalate_original_permissions"] is False
    assert runtime_result["actions"][0]["action"] == "authorize_user_identity"


def test_runtime_company_query_enterprise_realtime_gap_does_not_use_user_fallback() -> None:
    class TaskProvider:
        source = "task"
        _OPERATIONS = {"list_my_tasks": ("task.list_my_tasks", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            return ProviderResult(
                source="task",
                status="denied",
                result_type="task_list",
                count=0,
                metadata={
                    "operation": request.operation,
                    "credential_mode": "TENANT_TOKEN",
                    "actor_identity": "BOT",
                    "execution_identity_contract": request.execution_identity_contract.payload(),
                    "error_type": "enterprise_realtime_not_integrated",
                    "provider_boundary": "enterprise_realtime_not_integrated",
                    "user_fallback_allowed": True,
                    "legacy_cli_fallback_used": False,
                },
                answer="企业实时读取能力未接入。",
                error="enterprise_realtime_not_integrated",
            )

    context = _context("所有任务")
    intent = IntentResult(
        question_type="query",
        intent="task_query",
        data_scope="company",
        confidence=0.9,
        canonical_question="所有任务",
    )
    plan = PlannerResult(strategy="task_query", sources=("task",))
    permission = PermissionDecision(allowed=True, requires_confirmation=False, execution_identity="bot")

    execution = CapabilityRouter({"task": TaskProvider()}).execute(
        context=context,
        intent=intent,
        plan=plan,
        permission=permission,
    )

    assert execution.status == "denied"
    assert execution.result_context is not None
    assert execution.result_context.result_type == "task_list"
    provider = execution.result_context.metadata["provider_results"][0]
    assert provider["waiting_authorization"] is False
    assert provider["credential_mode"] == "TENANT_TOKEN"
    assert provider["provider_boundary"] == "enterprise_realtime_not_integrated"


def test_runtime_v5_task_query_outputs_enterprise_scope_context() -> None:
    class TaskProvider:
        source = "task"
        _OPERATIONS = {"list_my_tasks": ("task.list_my_tasks", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            assert request.operation == "list_my_tasks"
            return ProviderResult(
                source="task",
                status="success",
                result_type="task_list",
                count=1,
                items=({"title": "跟进客户", "task_guid": "task/1"},),
                answer="你有 1 条任务。",
            )

    result = run_runtime_v5(
        context=_context("我的任务"),
        providers={"task": TaskProvider()},
    )

    assert result.intent.intent == "task_query"
    assert result.composed.result_context is not None
    scope_context = result.composed.result_context.metadata["scope_context"]
    assert scope_context["scope"] == "SELF"
    assert scope_context["company_id"]
    runtime_scope_context = result.composed.metadata["runtime_result"]["metadata"]["scope_context"]
    assert runtime_scope_context["scope"] == "SELF"
    assert runtime_scope_context["company_id"] == scope_context["company_id"]
    item = result.composed.metadata["runtime_result"]["items"][0]
    assert item["resource_plane"] == "operational"
    assert item["resource_type"] == "task"
    assert item["source_system"] == "feishu"
    assert item["source_object_type"] == "task"
    assert item["source_object_id"] == "task/1"
    assert item["visibility_scope"] == "SELF"
    assert item["company_id"] == runtime_scope_context["company_id"]
    assert item["owner_open_id"] == "ou_test"
    assert item["allowed_user_ids"] == ["ou_test"]


def test_runtime_v5_calendar_query_outputs_policy_resource_metadata() -> None:
    class CalendarProvider:
        source = "calendar"
        _OPERATIONS = {"list_events": ("calendar.list_events", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            assert request.operation == "list_events"
            return ProviderResult(
                source="calendar",
                status="success",
                result_type="calendar_event_list",
                count=1,
                items=({"title": "销售会", "event_id": "event_1"},),
                answer="查询到 1 条日程。",
            )

    result = run_runtime_v5(
        context=_context("我的日程"),
        providers={"calendar": CalendarProvider()},
    )

    assert result.composed.result_context is not None
    runtime_result = result.composed.metadata["runtime_result"]
    item = runtime_result["items"][0]
    assert item["resource_plane"] == "operational"
    assert item["resource_type"] == "calendar"
    assert item["source_system"] == "feishu"
    assert item["source_object_type"] == "calendar"
    assert item["source_object_id"] == "event_1"
    assert item["visibility_scope"] == "SELF"
    assert item["company_id"] == runtime_result["metadata"]["company_id"]
    assert item["owner_open_id"] == "ou_test"


def test_runtime_v5_cognitive_query_outputs_policy_resource_metadata() -> None:
    class WorkEventProvider:
        source = "workevent"
        _OPERATIONS = {"risk_events": ("workevent.risk_events", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            assert request.operation == "risk_events"
            return ProviderResult(
                source="workevent",
                status="success",
                result_type="risk_event_list",
                count=1,
                items=(
                    {
                        "summary": "审批高金额风险",
                        "object_type": "approval",
                        "object_id": "approval_1",
                        "source_event_ids": ["event_1"],
                    },
                ),
                answer="发现 1 条风险事件。",
            )

    execution = CapabilityRouter({"workevent": WorkEventProvider()}).execute(
        context=_context("公司风险"),
        intent=IntentResult(
            question_type="insight",
            intent="risk_analysis",
            data_scope="company",
            confidence=0.9,
            canonical_question="公司风险",
        ),
        plan=PlannerResult(strategy="risk_analysis", sources=("workevent",)),
        permission=PermissionDecision(allowed=True, requires_confirmation=False, execution_identity="bot"),
    )

    assert execution.result_context is not None
    item = execution.result_context.items[0]
    assert item["resource_plane"] == "cognitive"
    assert item["resource_type"] == "workevent"
    assert item["source_system"] == "digital_advisor"
    assert item["source_object_type"] == "approval"
    assert item["source_object_id"] == "approval_1"
    assert item["source_event_ids"] == ["event_1"]
    assert item["visibility_scope"] == "COMPANY"
    assert item["inherited_visibility_scope"] == "COMPANY"


def test_runtime_v5_task_query_to_complete_closes_runtime_interaction_loop() -> None:
    calls: list[tuple[str, str]] = []

    class TaskProvider:
        source = "task"
        _OPERATIONS = {
            "list_my_tasks": ("task.list_my_tasks", False),
            "complete_task": ("task.complete_task", True),
        }

        def execute(self, request: ProviderRequest) -> ProviderResult:
            if request.operation == "list_my_tasks":
                return ProviderResult(
                    source="task",
                    status="success",
                    result_type="task_list",
                    count=1,
                    items=(
                        {
                            "summary": "明天提醒我跟进客户合同",
                            "guid": "26c359e1-8e45-4faa-b3b6-34a87fdff1a0",
                            "status": "todo",
                            "url": "https://applink.feishu.cn/client/todo/detail?guid=26c359e1-8e45-4faa-b3b6-34a87fdff1a0",
                        },
                    ),
                    answer="你有 1 条任务。",
                )
            calls.append((request.operation, request.params["task_guid"]))
            return ProviderResult(
                source="task",
                status="success",
                result_type="task_complete",
                count=1,
                items=({"title": "跟进客户", "task_guid": request.params["task_guid"]},),
                answer="任务已完成。",
            )

    queried = run_runtime_v5(
        context=_context("我的任务", chat_id="chat_task_loop"),
        providers={"task": TaskProvider()},
    )

    runtime_result = queried.composed.metadata["runtime_result"]
    action_input = runtime_result["actions"][0]["runtime_action_input"]
    assert runtime_result["result_type"] == "task_list"
    assert runtime_result["actions"][0]["action"] == "task_complete"
    assert action_input["intent"] == "task_complete"
    assert action_input["target"]["task_guid"] == "26c359e1-8e45-4faa-b3b6-34a87fdff1a0"
    assert action_input["context"]["company_id"] == runtime_result["metadata"]["company_id"]
    interaction_payload = interaction_payload_from_runtime_result(runtime_result_from_payload(runtime_result))
    assert interaction_payload.actions[0]["runtime_action_input"] == action_input

    waiting_confirmation = run_runtime_v5(
        context=_context(
            "card action",
            chat_id="chat_task_loop",
            session_context={"runtime_v5_action_input": action_input},
        ),
        providers={"task": TaskProvider()},
    )

    assert waiting_confirmation.execution is None
    assert waiting_confirmation.composed.result_context is not None
    assert waiting_confirmation.composed.result_context.result_type == "runtime_pending_confirmation"
    runtime_state = waiting_confirmation.composed.result_context.metadata["runtime_state"]
    assert runtime_state["actions"][0]["status"] == "waiting_confirmation"

    completed = run_runtime_v5(
        context=_context(
            "确认执行",
            chat_id="chat_task_loop",
            session_context={"runtime_v5_state": runtime_state},
        ),
        providers={"task": TaskProvider()},
    )

    assert calls == [("complete_task", "26c359e1-8e45-4faa-b3b6-34a87fdff1a0")]
    assert completed.composed.result_context is not None
    assert completed.composed.result_context.result_type == "task_complete"
    runtime_result = completed.composed.metadata["runtime_result"]
    assert runtime_result["result_type"] == "task_complete"
    assert runtime_result["status"] == "success"
    assert interaction_payload_from_runtime_result(runtime_result_from_payload(runtime_result)).payload_type == "feedback"


def test_runtime_v5_approval_detail_runtime_result_exposes_sidepanel_actions() -> None:
    class ApprovalProvider:
        source = "approval"
        _OPERATIONS = {"get_detail": ("approval.get_detail", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            assert request.operation == "get_detail"
            return ProviderResult(
                source="approval",
                status="success",
                result_type="approval_detail",
                count=1,
                items=(
                    {
                        "title": "付款审批",
                        "approval_code": "approval_1",
                        "instance_code": "instance_1",
                        "task_id": "task_1",
                    },
                ),
                answer="付款审批详情。",
            )

    result = run_runtime_v5(
        context=_context(
            "看审批详情",
            result_context=ResultContext(
                result_type="approval_list",
                count=1,
                items=({"title": "付款审批", "instance_code": "instance_1", "task_id": "task_1"},),
            ),
        ),
        providers={"approval": ApprovalProvider()},
    )

    runtime_result = result.composed.metadata["runtime_result"]
    assert runtime_result["result_type"] == "approval_detail"
    assert runtime_result["target_ui"] == "sidepanel"
    assert [action["action"] for action in runtime_result["actions"]] == ["approve", "reject"]
    assert all(action["requires_confirmation"] for action in runtime_result["actions"])


def test_runtime_v5_approval_sample_chain_freezes_runtime_result_and_interaction_payload() -> None:
    calls: list[tuple[str, str]] = []

    class ApprovalProvider:
        source = "approval"
        _OPERATIONS = {
            "list_pending": ("approval.list_pending", False),
            "get_detail": ("approval.get_detail", False),
            "reject": ("approval.reject", True),
        }

        def execute(self, request: ProviderRequest) -> ProviderResult:
            calls.append((request.operation, str(request.params.get("comment") or "")))
            if request.operation == "list_pending":
                return ProviderResult(
                    source="approval",
                    status="success",
                    result_type="approval_list",
                    count=1,
                    items=(
                        {
                            "title": "付款审批",
                            "approval_code": "approval_1",
                            "instance_code": "instance_1",
                            "task_id": "task_1",
                        },
                    ),
                    answer="你有 1 条待审批。",
                )
            if request.operation == "get_detail":
                return ProviderResult(
                    source="approval",
                    status="success",
                    result_type="approval_detail",
                    count=1,
                    items=(
                        {
                            "title": "付款审批",
                            "approval_code": "approval_1",
                            "instance_code": "instance_1",
                            "task_id": "task_1",
                        },
                    ),
                    answer="付款审批详情。",
                )
            assert request.operation == "reject"
            return ProviderResult(
                source="approval",
                status="success",
                result_type="approval_reject",
                count=1,
                items=({"instance_code": "instance_1", "task_id": "task_1", "status": "rejected"},),
                answer="审批已拒绝。",
            )

    query = run_runtime_v5(
        context=_context("待我审批有哪些", chat_id="chat_approval_sample"),
        providers={"approval": ApprovalProvider()},
    )
    query_payload = interaction_payload_from_runtime_result(runtime_result_from_payload(query.composed.metadata["runtime_result"]))
    assert query.composed.result_context is not None
    assert query.composed.result_context.result_type == "approval_list"
    assert query_payload.payload_type == "summary"
    assert query_payload.metadata["target_ui"] == "card"
    assert [action["action"] for action in query_payload.actions] == ["open_detail"]

    detail = run_runtime_v5(
        context=_context(
            "看审批详情",
            chat_id="chat_approval_sample",
            result_context=query.composed.result_context,
        ),
        providers={"approval": ApprovalProvider()},
    )
    detail_payload = interaction_payload_from_runtime_result(runtime_result_from_payload(detail.composed.metadata["runtime_result"]))
    assert detail.composed.result_context is not None
    assert detail.composed.result_context.result_type == "approval_detail"
    assert detail_payload.payload_type == "summary"
    assert detail_payload.metadata["target_ui"] == "sidepanel"
    assert [action["action"] for action in detail_payload.actions] == ["approve", "reject"]

    waiting_input = run_runtime_v5(
        context=_context(
            "card action",
            chat_id="chat_approval_sample",
            result_context=detail.composed.result_context,
            session_context={
                "runtime_v5_action_input": {
                    "action_id": "reject_sample_1",
                    "action_type": "reject",
                    "intent": "approval_reject",
                    "strategy": "approval_reject",
                    "target": {"instance_code": "instance_1", "task_id": "task_1"},
                    "confirmation": {"confirmed": False, "token": "reject_sample_1"},
                    "context": {
                        "company_id": "company_1",
                        "chat_id": "chat_approval_sample",
                        "user_id": "ou_test",
                        "open_id": "ou_test",
                        "source_ui": "card",
                    },
                    "message": "拒绝这个审批",
                    "sources": ["approval"],
                    "metadata": {"missing_params": ["comment"]},
                }
            },
        ),
        providers={"approval": ApprovalProvider()},
    )
    waiting_payload = interaction_payload_from_runtime_result(runtime_result_from_payload(waiting_input.composed.metadata["runtime_result"]))
    assert waiting_input.execution is None
    assert waiting_input.composed.result_context is not None
    assert waiting_input.composed.result_context.result_type == "runtime_waiting_input"
    assert waiting_payload.payload_type == "action"
    assert waiting_payload.status == "waiting"
    runtime_state = waiting_input.composed.result_context.metadata["runtime_state"]
    assert runtime_state["actions"][0]["status"] == "waiting_input"
    _assert_runtime_state_company_id(runtime_state)

    waiting_confirmation = run_runtime_v5(
        context=_context(
            "原因：资料不完整",
            chat_id="chat_approval_sample",
            result_context=detail.composed.result_context,
            session_context={"runtime_v5_state": runtime_state},
        ),
        providers={"approval": ApprovalProvider()},
    )
    confirmation_payload = interaction_payload_from_runtime_result(runtime_result_from_payload(waiting_confirmation.composed.metadata["runtime_result"]))
    assert waiting_confirmation.execution is None
    assert waiting_confirmation.composed.result_context is not None
    assert waiting_confirmation.composed.result_context.result_type == "runtime_pending_confirmation"
    assert confirmation_payload.payload_type == "action"
    assert [action["action"] for action in confirmation_payload.actions] == ["confirm", "cancel"]
    runtime_state = waiting_confirmation.composed.result_context.metadata["runtime_state"]
    assert runtime_state["actions"][0]["status"] == "waiting_confirmation"
    _assert_runtime_state_company_id(runtime_state)

    done = run_runtime_v5(
        context=_context(
            "确认执行",
            chat_id="chat_approval_sample",
            result_context=detail.composed.result_context,
            session_context={"runtime_v5_state": runtime_state},
        ),
        providers={"approval": ApprovalProvider()},
    )
    done_payload = interaction_payload_from_runtime_result(runtime_result_from_payload(done.composed.metadata["runtime_result"]))
    assert done.execution is not None
    assert done.execution.status == "success"
    assert done.composed.result_context is not None
    assert done.composed.result_context.result_type == "runtime_action"
    assert done_payload.payload_type == "feedback"
    runtime_state = done.composed.result_context.metadata["runtime_state"]
    assert runtime_state["status"] == "done"
    assert runtime_state["actions"][0]["status"] == "done"
    _assert_runtime_state_company_id(runtime_state)
    assert runtime_state["metadata"]["transitions"] == ["waiting", "waiting_input", "waiting_confirmation", "confirmed", "executing", "done"]
    assert calls == [("list_pending", ""), ("get_detail", ""), ("reject", "资料不完整")]


def test_runtime_v5_blocks_single_company_execution_without_company_id() -> None:
    class ApprovalProvider:
        source = "approval"
        _OPERATIONS = {"list_pending": ("approval.list_pending", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            raise AssertionError("provider should not execute without active company")

    result = run_runtime_v5(
        context=RuntimeContext(
            identity=RuntimeIdentity(open_id="ou_test", role="owner", domains=("all",)),
            runtime_scope=RuntimeScope(scope_type="single_company", company_ids=(), active_company_id=None),
            current_message="待我审批有哪些",
        ),
        providers={"approval": ApprovalProvider()},
    )

    assert result.execution is None
    assert result.permission.allowed is False
    assert result.permission.reason == "missing_company_id"
    assert result.composed.result_context is not None
    assert result.composed.result_context.metadata["empty_reason"] == "missing_company_id"


def test_runtime_v5_blocks_runtime_action_input_without_company_id() -> None:
    class ApprovalProvider:
        source = "approval"
        _OPERATIONS = {"approve": ("approval.approve", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            raise AssertionError("runtime action input without company_id must not execute provider")

    result = run_runtime_v5(
        context=_context(
            "card action",
            chat_id="chat_missing_action_company",
            result_context=ResultContext(
                result_type="approval_detail",
                count=1,
                items=({"instance_code": "instance_1", "task_id": "task_1"},),
            ),
            session_context={
                "runtime_v5_action_input": {
                    "action_id": "approve_missing_company",
                    "action_type": "approve",
                    "intent": "approval_approve",
                    "strategy": "approval_approve",
                    "target": {"instance_code": "instance_1", "task_id": "task_1"},
                    "confirmation": {"confirmed": True, "token": "approve_missing_company"},
                    "context": {"source_ui": "card"},
                    "message": "同意这个审批",
                    "sources": ["approval"],
                }
            },
        ),
        providers={"approval": ApprovalProvider()},
    )

    assert result.execution is None
    assert result.permission.allowed is False
    assert result.permission.reason == "missing_action_company_id"
    assert result.composed.result_context is not None
    assert result.composed.result_context.result_type == "runtime_action"
    assert result.composed.result_context.metadata["empty_reason"] == "missing_action_company_id"
    assert result.composed.result_context.metadata["runtime_action_input_contract"]["company_id_required"] is True
    assert "runtime_v5_action_input" not in result.context.session_context
    runtime_result = result.composed.metadata["runtime_result"]
    assert runtime_result["status"] == "skipped"
    assert runtime_result["metadata"]["company_id"] == result.composed.result_context.metadata["company_id"]


def test_runtime_action_input_builder_requires_company_id() -> None:
    payload = build_runtime_action_input_payload(
        action_id="approve_1",
        action_type="approve",
        intent="approval_approve",
        strategy="approval_approve",
        company_id="company_1",
        target={"instance_code": "instance_1", "task_id": "task_1"},
        confirmed=True,
        confirmation_token="approve_1",
        chat_id="chat_1",
        open_id="ou_1",
        source_ui="card",
        message="同意这个审批",
        sources=["approval"],
    )

    assert payload["context"]["company_id"] == "company_1"
    assert payload["context"]["source_ui"] == "card"
    assert payload["confirmation"]["confirmed"] is True

    with pytest.raises(ValueError, match="context.company_id"):
        build_runtime_action_input_payload(
            action_id="approve_2",
            action_type="approve",
            intent="approval_approve",
            strategy="approval_approve",
            company_id="",
        )


def test_runtime_action_input_preserves_enterprise_scope_context() -> None:
    payload = build_runtime_action_input_payload(
        action_id="task_complete_1",
        action_type="execute",
        intent="task_complete",
        strategy="task_complete",
        company_id="company_1",
        target={"task_guid": "task/1"},
        confirmed=True,
        confirmation_token="task_complete_1",
        source_ui="card",
        sources=["task"],
        metadata={
            "scope_context": {
                "scope": "SELF",
                "company_id": "company_1",
                "filters": {},
            },
        },
    )

    assert payload["metadata"]["scope_context"] == {
        "scope": "SELF",
        "company_id": "company_1",
        "filters": {},
    }


def test_runtime_pending_action_contract_preserves_action_input_context() -> None:
    action_input = runtime_action_input_from_payload(
        build_runtime_action_input_payload(
            action_id="reject_1",
            action_type="reject",
            intent="approval_reject",
            strategy="approval_reject",
            company_id="company_1",
            target={"instance_code": "instance_1", "task_id": "task_1"},
            confirmed=False,
            confirmation_token="reject_1",
            chat_id="chat_1",
            open_id="ou_1",
            source_ui="card",
            message="拒绝这个审批",
            sources=["approval"],
            metadata={"missing_params": ["comment"]},
        )
    )

    pending_action = runtime_pending_action_with_missing_input(
        pending_action_from_runtime_action_input(action_input, message="拒绝这个审批"),
        missing_params=["comment"],
    )
    payload = runtime_pending_action_payload(pending_action)

    assert payload["id"] == "reject_1"
    assert payload["company_id"] == "company_1"
    assert payload["confirmation_token"] == "reject_1"
    assert payload["missing_params"] == ["comment"]
    assert payload["input_contract"] == {"status": "waiting_input", "missing_params": ["comment"]}
    assert payload["runtime_action_input"]["context"]["company_id"] == "company_1"
    assert payload["runtime_action_input"]["context"]["source_ui"] == "card"
    assert payload["runtime_action_input"]["target"]["instance_code"] == "instance_1"


def test_runtime_state_restores_waiting_input_company_id_from_state_metadata() -> None:
    restored = waiting_input_action_from_runtime_state(
        {
            "runtime_v5_state": {
                "task_id": "task_state_1",
                "status": "waiting",
                "intent": "approval_reject",
                "strategy": "approval_reject",
                "metadata": {"company_id": "company_1"},
                "actions": [
                    {
                        "action_id": "reject_1",
                        "task_id": "task_state_1",
                        "status": "waiting_input",
                        "intent": "approval_reject",
                        "strategy": "approval_reject",
                        "message": "拒绝这个审批",
                        "sources": ["approval"],
                        "confirmation_token": "reject_1",
                        "metadata": {
                            "company_id": "company_1",
                            "pending_action": {
                                "id": "reject_1",
                                "message": "拒绝这个审批",
                                "runtime_action_input": {
                                    "context": {"company_id": "company_1", "source_ui": "card"},
                                },
                            },
                        },
                    }
                ],
            }
        }
    )

    assert restored is not None
    assert restored["company_id"] == "company_1"
    assert restored["runtime_action_input"]["context"]["company_id"] == "company_1"
    assert restored["runtime_action_input"]["context"]["source_ui"] == "card"


def test_runtime_state_restores_waiting_confirmation_company_id_from_action_metadata() -> None:
    restored = pending_action_from_runtime_state(
        {
            "runtime_v5_state": {
                "task_id": "task_state_2",
                "status": "waiting",
                "intent": "approval_approve",
                "strategy": "approval_approve",
                "metadata": {},
                "actions": [
                    {
                        "action_id": "approve_1",
                        "task_id": "task_state_2",
                        "status": "waiting_confirmation",
                        "intent": "approval_approve",
                        "strategy": "approval_approve",
                        "message": "同意这个审批",
                        "sources": ["approval"],
                        "confirmation_token": "approve_1",
                        "metadata": {
                            "company_id": "company_1",
                            "pending_action": {
                                "id": "approve_1",
                                "message": "同意这个审批",
                                "runtime_action_input": {
                                    "context": {"company_id": "company_1", "source_ui": "portal"},
                                },
                            },
                        },
                    }
                ],
            }
        }
    )

    assert restored is not None
    assert restored["company_id"] == "company_1"
    assert restored["action_state_status"] == "waiting_confirmation"
    assert restored["runtime_action_input"]["context"]["company_id"] == "company_1"
    assert restored["runtime_action_input"]["context"]["source_ui"] == "portal"


def test_runtime_v5_organization_export_executes_people_base_in_order() -> None:
    calls: list[tuple[str, str]] = []

    class PeopleProvider:
        source = "people"
        _OPERATIONS = {"get_org_snapshot": ("people.get_org_snapshot", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            calls.append((request.source, request.operation))
            return ProviderResult(
                source="people",
                status="success",
                result_type="organization_snapshot",
                count=1,
                items=({"name": "张三", "department": "研发", "leader": "李四"},),
                answer="已读取组织架构。",
            )

    class BaseProvider:
        source = "base"
        _OPERATIONS = {"write_records": ("base.write_records", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            calls.append((request.source, request.operation))
            assert request.params["app_token"] == "bascn-demo"
            assert request.params["previous_results"][0].source == "people"
            return ProviderResult(
                source="base",
                status="success",
                result_type="base_export",
                count=1,
                items=({"name": "张三", "department": "研发"},),
                metadata={"app_token": "bascn-demo", "table_id": "tbl_1"},
                answer="已创建数据表并写入 1 行。",
            )

    result = run_runtime_v5(
        context=_context("帮我创建一个表然后把组织架构放进去，使用 bascn-demo，把文件发给我"),
        providers={"people": PeopleProvider(), "base": BaseProvider()},
    )

    assert calls == [
        ("people", "get_org_snapshot"),
        ("base", "write_records"),
    ]
    assert result.execution is not None
    assert result.execution.status == "success"


def test_runtime_v5_organization_export_without_app_token_targets_new_base() -> None:
    class PeopleProvider:
        source = "people"
        _OPERATIONS = {"get_org_snapshot": ("people.get_org_snapshot", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            return ProviderResult(
                source="people",
                status="success",
                result_type="organization_snapshot",
                count=1,
                items=({"name": "张三", "department": "研发", "leader": "李四"},),
                answer="已读取组织架构。",
            )

    class BaseProvider:
        source = "base"
        _OPERATIONS = {"write_records": ("base.write_records", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            assert request.params["target"] == "new_base"
            assert "app_token" not in request.params
            return ProviderResult(
                source="base",
                status="success",
                result_type="base_export",
                count=1,
                items=({"name": "张三", "department": "研发"},),
                metadata={"app_token": "bascn-created", "table_id": "tbl_1", "created_base": True},
                answer="已创建组织架构表并写入 1 行。",
            )

    result = run_runtime_v5(
        context=_context("帮我创建一个表然后把组织架构放进去，把文件发给我"),
        providers={"people": PeopleProvider(), "base": BaseProvider()},
    )

    assert result.intent.intent == "organization_export"
    assert result.intent.missing_params == ()
    assert result.execution is not None
    assert result.execution.status == "success"


def test_runtime_v5_action_requires_confirmation_in_chat_session() -> None:
    class PeopleProvider:
        source = "people"
        _OPERATIONS = {"get_org_snapshot": ("people.get_org_snapshot", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            raise AssertionError("action should wait for confirmation")

    result = run_runtime_v5(
        context=_context("帮我创建一个表然后把组织架构放进去，把文件发给我", chat_id="chat_1"),
        providers={"people": PeopleProvider()},
    )

    assert result.execution is None
    assert result.composed.metadata["requires_confirmation"] is True
    assert "确认" in result.composed.answer


def test_runtime_v5_confirmed_action_resumes_pending_message() -> None:
    calls: list[tuple[str, str]] = []

    class PeopleProvider:
        source = "people"
        _OPERATIONS = {"get_org_snapshot": ("people.get_org_snapshot", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            calls.append((request.source, request.operation))
            return ProviderResult(
                source="people",
                status="success",
                result_type="organization_snapshot",
                count=1,
                items=({"name": "张三", "department": "研发", "leader": "李四"},),
                answer="已读取组织架构。",
            )

    class BaseProvider:
        source = "base"
        _OPERATIONS = {"write_records": ("base.write_records", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            calls.append((request.source, request.operation))
            return ProviderResult(
                source="base",
                status="success",
                result_type="base_export",
                count=1,
                items=({"name": "张三", "department": "研发"},),
                metadata={"app_token": "bascn-created", "table_id": "tbl_1", "created_base": True},
                answer="已创建组织架构表并写入 1 行。",
            )

    result = run_runtime_v5(
        context=_context(
            "确认执行",
            chat_id="chat_1",
            session_context={
                "runtime_v5_pending_action": {
                    "message": "帮我创建一个表然后把组织架构放进去，把文件发给我",
                    "intent": "organization_export",
                    "strategy": "organization_export",
                    "sources": ["people", "base"],
                }
            },
        ),
        providers={"people": PeopleProvider(), "base": BaseProvider()},
    )

    assert calls == [
        ("people", "get_org_snapshot"),
        ("base", "write_records"),
    ]
    assert result.execution is not None
    assert result.execution.status == "success"


def test_runtime_v5_legacy_action_request_is_contained_before_confirmation() -> None:
    class ApprovalProvider:
        source = "approval"
        _OPERATIONS = {"approve": ("approval.approve", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            raise AssertionError("legacy action request must not execute provider")

    result = run_runtime_v5(
        context=_context(
            "portal action",
            chat_id="chat_approval_1",
            result_context=ResultContext(
                result_type="approval_detail",
                count=1,
                items=({"instance_code": "instance_1", "task_id": "task_1"},),
            ),
            session_context={
                "runtime_v5_action_request": {
                    "message": "同意这个审批",
                    "intent": "approval_approve",
                    "strategy": "approval_approve",
                    "sources": ["approval"],
                    "company_id": "company_1",
                    "confirmed": False,
                }
            },
        ),
        providers={"approval": ApprovalProvider()},
    )

    assert result.execution is None
    assert result.permission.allowed is False
    assert result.permission.reason == "legacy_runtime_action_request_unsupported"
    assert result.composed.result_context is not None
    assert result.composed.result_context.result_type == "runtime_action"
    assert result.composed.result_context.metadata["empty_reason"] == "legacy_runtime_action_request_unsupported"
    assert result.composed.result_context.metadata["runtime_action_input_contract"]["legacy_runtime_v5_action_request"] is True
    assert "runtime_v5_action_request" not in result.context.session_context
    runtime_result = result.composed.metadata["runtime_result"]
    assert runtime_result["result_type"] == "runtime_action"
    assert runtime_result["target_ui"] == "card"
    assert runtime_result["actions"] == []


def test_runtime_v5_confirmed_legacy_action_request_is_contained_before_execution() -> None:
    class ApprovalProvider:
        source = "approval"
        _OPERATIONS = {"approve": ("approval.approve", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            raise AssertionError("confirmed legacy action request must not execute provider")

    result = run_runtime_v5(
        context=_context(
            "portal action",
            chat_id="chat_approval_2",
            result_context=ResultContext(
                result_type="approval_detail",
                count=1,
                items=({"instance_code": "instance_1", "task_id": "task_1"},),
            ),
            session_context={
                "runtime_v5_action_request": {
                    "confirmation_token": "confirm_1",
                    "message": "同意这个审批",
                    "intent": "approval_approve",
                    "strategy": "approval_approve",
                    "sources": ["approval"],
                    "company_id": "company_1",
                    "confirmed": True,
                }
            },
        ),
        providers={"approval": ApprovalProvider()},
    )

    assert result.execution is None
    assert result.permission.allowed is False
    assert result.permission.reason == "legacy_runtime_action_request_unsupported"
    assert result.composed.result_context is not None
    assert result.composed.result_context.result_type == "runtime_action"
    assert result.composed.result_context.metadata["empty_reason"] == "legacy_runtime_action_request_unsupported"
    assert result.composed.result_context.metadata["runtime_action_input_contract"]["legacy_runtime_v5_action_request"] is True
    assert "runtime_v5_action_request" not in result.context.session_context
    runtime_result = result.composed.metadata["runtime_result"]
    assert runtime_result["result_type"] == "runtime_action"
    assert runtime_result["target_ui"] == "card"
    assert runtime_result["actions"] == []


def test_runtime_v5_failed_action_uses_runtime_action_input_contract() -> None:
    class ApprovalProvider:
        source = "approval"
        _OPERATIONS = {"approve": ("approval.approve", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            return ProviderResult(
                source="approval",
                status="error",
                result_type="approval_approve",
                count=0,
                error="provider_failed",
                answer="审批通过失败。",
            )

    result = run_runtime_v5(
        context=_context(
            "portal action",
            chat_id="chat_action_input_failed",
            result_context=ResultContext(
                result_type="approval_detail",
                count=1,
                items=({"instance_code": "instance_1", "task_id": "task_1"},),
            ),
            session_context={
                "runtime_v5_action_input": {
                    "action_id": "action_input_1",
                    "action_type": "approve",
                    "intent": "approval_approve",
                    "strategy": "approval_approve",
                    "target": {"instance_code": "instance_1", "task_id": "task_1"},
                    "confirmation": {"confirmed": True, "token": "confirm_input_1"},
                    "context": {
                        "company_id": "company_1",
                        "chat_id": "chat_action_input_failed",
                        "user_id": "ou_test",
                        "open_id": "ou_test",
                        "source_ui": "portal",
                    },
                    "message": "同意这个审批",
                    "sources": ["approval"],
                }
            },
        ),
        providers={"approval": ApprovalProvider()},
    )

    assert result.execution is not None
    assert result.execution.status == "error"
    assert result.composed.result_context is not None
    assert result.composed.result_context.result_type == "runtime_action"
    runtime_state = result.composed.result_context.metadata["runtime_state"]
    assert runtime_state["status"] == "failed"
    assert runtime_state["error"] == "provider_failed"
    assert runtime_state["actions"][0]["status"] == "failed"
    assert runtime_state["actions"][0]["error"] == "provider_failed"
    assert runtime_state["metadata"]["transitions"] == ["waiting", "waiting_confirmation", "confirmed", "executing", "failed"]
    _assert_runtime_state_company_id(runtime_state)
    action_input = runtime_state["actions"][0]["metadata"]["pending_action"]["runtime_action_input"]
    assert action_input["action_type"] == "approve"
    assert action_input["confirmation"]["confirmed"] is True
    assert action_input["context"]["source_ui"] == "portal"
    payload = interaction_payload_from_runtime_result(runtime_result_from_payload(result.composed.metadata["runtime_result"]))
    assert payload.payload_type == "feedback"
    assert payload.status == "error"
    assert payload.metadata["result_type"] == "runtime_action"
    assert payload.metadata["result_context"]["runtime_state"]["error"] == "provider_failed"


def test_runtime_v5_cancel_action_uses_runtime_action_input_contract() -> None:
    class ApprovalProvider:
        source = "approval"
        _OPERATIONS = {"approve": ("approval.approve", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            raise AssertionError("cancelled RuntimeActionInput must not execute provider")

    result = run_runtime_v5(
        context=_context(
            "card action",
            chat_id="chat_action_input_cancel",
            result_context=ResultContext(
                result_type="approval_detail",
                count=1,
                items=({"instance_code": "instance_1", "task_id": "task_1"},),
            ),
            session_context={
                "runtime_v5_action_input": {
                    "action_id": "action_input_cancel_1",
                    "action_type": "cancel",
                    "intent": "approval_approve",
                    "strategy": "approval_approve",
                    "target": {"instance_code": "instance_1", "task_id": "task_1"},
                    "confirmation": {"confirmed": False, "token": "confirm_cancel_1"},
                    "context": {
                        "company_id": "company_1",
                        "chat_id": "chat_action_input_cancel",
                        "user_id": "ou_test",
                        "open_id": "ou_test",
                        "source_ui": "card",
                    },
                    "message": "取消",
                    "sources": ["approval"],
                }
            },
        ),
        providers={"approval": ApprovalProvider()},
    )

    assert result.execution is None
    assert result.composed.result_context is not None
    assert result.composed.result_context.result_type == "runtime_action"
    runtime_state = result.composed.result_context.metadata["runtime_state"]
    assert runtime_state["status"] == "failed"
    assert runtime_state["actions"][0]["status"] == "cancelled"
    assert runtime_state["metadata"]["transitions"] == ["waiting", "waiting_confirmation", "cancelled"]
    assert runtime_state["actions"][0]["metadata"]["transitions"] == ["waiting_confirmation", "cancelled"]
    action_input = runtime_state["actions"][0]["metadata"]["pending_action"]["runtime_action_input"]
    assert action_input["action_type"] == "cancel"
    assert action_input["confirmation"]["confirmed"] is False
    assert action_input["context"]["source_ui"] == "card"


def test_runtime_v5_task_complete_waiting_confirmation_executes_and_returns_task_complete() -> None:
    calls: list[tuple[str, str]] = []

    class TaskProvider:
        source = "task"
        _OPERATIONS = {"complete_task": ("task.complete_task", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            calls.append((request.operation, request.params["task_guid"]))
            return ProviderResult(
                source="task",
                status="success",
                result_type="task_complete",
                count=1,
                items=({"title": "跟进客户", "task_guid": request.params["task_guid"]},),
                answer="任务已完成。",
            )

    waiting_confirmation = run_runtime_v5(
        context=_context(
            "card action",
            chat_id="chat_task_complete",
            session_context={
                "runtime_v5_action_input": {
                    "action_id": "task_complete_1",
                    "action_type": "execute",
                    "intent": "task_complete",
                    "strategy": "task_complete",
                    "target": {"task_guid": "task/1"},
                    "confirmation": {"confirmed": False, "token": "task_complete_1"},
                    "context": {
                        "company_id": "company_1",
                        "chat_id": "chat_task_complete",
                        "user_id": "ou_test",
                        "open_id": "ou_test",
                        "source_ui": "card",
                    },
                    "message": "完成任务",
                    "sources": ["task"],
                    "metadata": {"scope_context": {"scope": "SELF", "company_id": "company_1", "filters": {}}},
                }
            },
        ),
        providers={"task": TaskProvider()},
    )

    assert waiting_confirmation.execution is None
    assert waiting_confirmation.composed.result_context is not None
    assert waiting_confirmation.composed.result_context.result_type == "runtime_pending_confirmation"
    runtime_state = waiting_confirmation.composed.result_context.metadata["runtime_state"]
    assert runtime_state["status"] == "waiting"
    assert runtime_state["actions"][0]["status"] == "waiting_confirmation"
    assert runtime_state["metadata"]["transitions"] == ["waiting", "waiting_confirmation"]
    assert calls == []

    executed = run_runtime_v5(
        context=_context(
            "确认执行",
            chat_id="chat_task_complete",
            session_context={"runtime_v5_state": runtime_state},
        ),
        providers={"task": TaskProvider()},
    )

    assert calls == [("complete_task", "task/1")]
    assert executed.execution is not None
    assert executed.execution.status == "success"
    assert executed.execution.provider_results[0].result_type == "task_complete"
    assert executed.composed.result_context is not None
    assert executed.composed.result_context.result_type == "task_complete"
    runtime_state = executed.composed.result_context.metadata["runtime_state"]
    assert runtime_state["status"] == "done"
    assert runtime_state["actions"][0]["status"] == "done"
    assert runtime_state["metadata"]["transitions"] == ["waiting", "waiting_confirmation", "confirmed", "executing", "done"]
    assert runtime_state["actions"][0]["metadata"]["transitions"] == ["waiting_confirmation", "confirmed", "executing", "done"]
    runtime_result = executed.composed.metadata["runtime_result"]
    assert runtime_result["result_type"] == "task_complete"
    assert interaction_payload_from_runtime_result(runtime_result_from_payload(runtime_result)).payload_type == "feedback"


def test_runtime_v5_task_complete_failed_provider_returns_failed_task_complete() -> None:
    class TaskProvider:
        source = "task"
        _OPERATIONS = {"complete_task": ("task.complete_task", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            return ProviderResult(
                source="task",
                status="error",
                result_type="task_complete",
                count=0,
                error="task_provider_failed",
                answer="任务完成失败。",
            )

    result = run_runtime_v5(
        context=_context(
            "card action",
            chat_id="chat_task_complete_failed",
            session_context={
                "runtime_v5_action_input": {
                    "action_id": "task_complete_failed_1",
                    "action_type": "execute",
                    "intent": "task_complete",
                    "strategy": "task_complete",
                    "target": {"task_guid": "task/1"},
                    "confirmation": {"confirmed": True, "token": "task_complete_failed_1"},
                    "context": {
                        "company_id": "company_1",
                        "chat_id": "chat_task_complete_failed",
                        "user_id": "ou_test",
                        "open_id": "ou_test",
                        "source_ui": "card",
                    },
                    "message": "完成任务",
                    "sources": ["task"],
                }
            },
        ),
        providers={"task": TaskProvider()},
    )

    assert result.execution is not None
    assert result.execution.status == "error"
    assert result.execution.provider_results[0].result_type == "task_complete"
    assert result.composed.result_context is not None
    assert result.composed.result_context.result_type == "task_complete"
    runtime_state = result.composed.result_context.metadata["runtime_state"]
    assert runtime_state["status"] == "failed"
    assert runtime_state["actions"][0]["status"] == "failed"
    assert runtime_state["metadata"]["transitions"] == ["waiting", "waiting_confirmation", "confirmed", "executing", "failed"]
    assert runtime_state["actions"][0]["metadata"]["transitions"] == ["waiting_confirmation", "confirmed", "executing", "failed"]
    runtime_result = result.composed.metadata["runtime_result"]
    assert runtime_result["result_type"] == "task_complete"
    assert runtime_result["status"] == "error"


def test_runtime_v5_waiting_authorization_builds_authorization_interaction_payload() -> None:
    class TaskProvider:
        source = "task"
        _OPERATIONS = {"complete_task": ("task.complete_task", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            return ProviderResult(
                source="task",
                status="denied",
                result_type="waiting_authorization",
                error="missing_feishu_user_account",
                answer="完成任务需要本人飞书授权。",
                metadata={
                    "operation": "complete_task",
                    "credential_mode": "USER_TOKEN",
                    "authorization_status": "MISSING_AUTHORIZATION",
                    "authorization_error": "missing_feishu_user_account",
                    "waiting_authorization": True,
                    "provider_boundary": "user_token_required",
                    "execution_identity_contract": request.execution_identity_contract.payload(),
                },
            )

    result = run_runtime_v5(
        context=_context(
            "card action",
            chat_id="chat_task_complete_auth",
            session_context={
                "runtime_v5_action_input": {
                    "action_id": "task_complete_auth_1",
                    "action_type": "execute",
                    "intent": "task_complete",
                    "strategy": "task_complete",
                    "target": {"task_guid": "task/1"},
                    "confirmation": {"confirmed": True, "token": "task_complete_auth_1"},
                    "context": {
                        "company_id": "company_1",
                        "chat_id": "chat_task_complete_auth",
                        "user_id": "ou_test",
                        "open_id": "ou_test",
                        "source_ui": "card",
                    },
                    "message": "完成任务",
                    "sources": ["task"],
                }
            },
        ),
        providers={"task": TaskProvider()},
    )

    runtime_result = result.composed.metadata["runtime_result"]
    assert runtime_result["result_type"] == "waiting_authorization"
    assert runtime_result["status"] == "waiting_authorization"
    assert runtime_result["metadata"]["authorization"]["authorization_status"] == "MISSING_AUTHORIZATION"
    assert runtime_result["actions"] == [
        {
            "action": "authorize_user_identity",
            "label": "授权个人能力包",
            "target_ui": "card",
            "requires_confirmation": False,
            "resource_type": "user_identity_bundle",
            "channel": "feishu_oauth",
            "authorization_status": "MISSING_AUTHORIZATION",
            "authorization_flow": "feishu_in_app_oauth",
            "url": runtime_result["metadata"]["authorization"]["url"],
        }
    ]
    assert "/api/user-identity/oauth/feishu/start?" in runtime_result["actions"][0]["url"]
    assert "open_id=ou_test" in runtime_result["actions"][0]["url"]
    payload = interaction_payload_from_runtime_result(runtime_result_from_payload(runtime_result))
    assert payload.payload_type == "authorization"
    assert payload.actions == tuple(runtime_result["actions"])
    assert payload.metadata["authorization"]["provider_boundary"] == "user_token_required"


def test_runtime_v5_reject_missing_params_waits_for_input_then_confirmation() -> None:
    calls: list[tuple[str, str, str]] = []
    approval_detail = ResultContext(
        result_type="approval_detail",
        count=1,
        items=({"instance_code": "instance_1", "task_id": "task_1"},),
    )

    class ApprovalProvider:
        source = "approval"
        _OPERATIONS = {"reject": ("approval.reject", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            item = request.params["item"]
            calls.append((request.operation, item["task_id"], request.params["comment"]))
            return ProviderResult(
                source="approval",
                status="success",
                result_type="approval_reject",
                count=1,
                items=({"instance_code": item["instance_code"], "task_id": item["task_id"], "status": "rejected"},),
                answer="审批已拒绝。",
            )

    waiting_input = run_runtime_v5(
        context=_context(
            "card action",
            chat_id="chat_reject_waiting_input",
            result_context=approval_detail,
            session_context={
                "runtime_v5_action_input": {
                    "action_id": "reject_input_1",
                    "action_type": "reject",
                    "intent": "approval_reject",
                    "strategy": "approval_reject",
                    "target": {"instance_code": "instance_1", "task_id": "task_1"},
                    "confirmation": {"confirmed": False, "token": "reject_token_1"},
                    "context": {
                        "company_id": "company_1",
                        "chat_id": "chat_reject_waiting_input",
                        "user_id": "ou_test",
                        "open_id": "ou_test",
                        "source_ui": "card",
                    },
                    "message": "拒绝这个审批",
                    "sources": ["approval"],
                    "metadata": {"missing_params": ["comment"]},
                }
            },
        ),
        providers={"approval": ApprovalProvider()},
    )

    assert waiting_input.execution is None
    assert waiting_input.composed.result_context is not None
    assert waiting_input.composed.result_context.result_type == "runtime_waiting_input"
    assert waiting_input.composed.result_context.metadata["actionable"] is True
    assert waiting_input.composed.result_context.metadata["missing_params"] == ["comment"]
    assert waiting_input.composed.result_context.metadata["input_contract"] == {
        "status": "waiting_input",
        "missing_params": ["comment"],
        "next_state": "waiting_confirmation",
    }
    assert waiting_input.composed.metadata["runtime_result"]["target_ui"] == "card"
    runtime_state = waiting_input.composed.result_context.metadata["runtime_state"]
    assert runtime_state["status"] == "waiting"
    assert runtime_state["actions"][0]["status"] == "waiting_input"
    assert runtime_state["actions"][0]["metadata"]["missing_params"] == ["comment"]
    assert calls == []

    waiting_confirmation = run_runtime_v5(
        context=_context(
            "原因：资料不完整",
            chat_id="chat_reject_waiting_input",
            result_context=approval_detail,
            session_context={"runtime_v5_state": runtime_state},
        ),
        providers={"approval": ApprovalProvider()},
    )

    assert waiting_confirmation.execution is None
    assert waiting_confirmation.composed.result_context is not None
    assert waiting_confirmation.composed.result_context.result_type == "runtime_pending_confirmation"
    runtime_state = waiting_confirmation.composed.result_context.metadata["runtime_state"]
    assert runtime_state["actions"][0]["status"] == "waiting_confirmation"
    assert runtime_state["metadata"]["transitions"] == ["waiting", "waiting_input", "waiting_confirmation"]
    assert runtime_state["actions"][0]["metadata"]["transitions"] == ["waiting_input", "waiting_confirmation"]
    assert calls == []

    executed = run_runtime_v5(
        context=_context(
            "确认执行",
            chat_id="chat_reject_waiting_input",
            result_context=approval_detail,
            session_context={"runtime_v5_state": runtime_state},
        ),
        providers={"approval": ApprovalProvider()},
    )

    assert calls == [("reject", "task_1", "资料不完整")]
    assert executed.execution is not None
    assert executed.execution.status == "success"
    assert executed.composed.result_context is not None
    runtime_state = executed.composed.result_context.metadata["runtime_state"]
    assert runtime_state["status"] == "done"
    assert runtime_state["actions"][0]["status"] == "done"
    assert runtime_state["metadata"]["transitions"] == ["waiting", "waiting_input", "waiting_confirmation", "confirmed", "executing", "done"]
    assert runtime_state["actions"][0]["metadata"]["transitions"] == ["waiting_input", "waiting_confirmation", "confirmed", "executing", "done"]


def test_runtime_v5_waiting_input_cancel_does_not_execute_provider() -> None:
    class ApprovalProvider:
        source = "approval"
        _OPERATIONS = {"reject": ("approval.reject", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            raise AssertionError("waiting input cancel must not execute provider")

    waiting_input = run_runtime_v5(
        context=_context(
            "card action",
            chat_id="chat_waiting_input_cancel",
            result_context=ResultContext(
                result_type="approval_detail",
                count=1,
                items=({"instance_code": "instance_1", "task_id": "task_1"},),
            ),
            session_context={
                "runtime_v5_action_input": {
                    "action_id": "reject_input_cancel",
                    "action_type": "reject",
                    "intent": "approval_reject",
                    "strategy": "approval_reject",
                    "target": {"instance_code": "instance_1", "task_id": "task_1"},
                    "confirmation": {"confirmed": False, "token": "reject_token_cancel"},
                    "context": {"company_id": "company_1", "source_ui": "card"},
                    "message": "拒绝这个审批",
                    "sources": ["approval"],
                    "metadata": {"missing_params": ["comment"]},
                }
            },
        ),
        providers={"approval": ApprovalProvider()},
    )
    assert waiting_input.composed.result_context is not None
    runtime_state = waiting_input.composed.result_context.metadata["runtime_state"]

    cancelled = run_runtime_v5(
        context=_context(
            "取消",
            chat_id="chat_waiting_input_cancel",
            session_context={"runtime_v5_state": runtime_state},
        ),
        providers={"approval": ApprovalProvider()},
    )

    assert cancelled.execution is None
    assert cancelled.composed.result_context is not None
    assert cancelled.composed.result_context.result_type == "runtime_action"
    runtime_state = cancelled.composed.result_context.metadata["runtime_state"]
    assert runtime_state["status"] == "failed"
    assert runtime_state["actions"][0]["status"] == "cancelled"
    assert runtime_state["metadata"]["transitions"] == ["waiting", "waiting_input", "cancelled"]
    assert runtime_state["actions"][0]["metadata"]["transitions"] == ["waiting_input", "cancelled"]


def test_runtime_v5_waiting_input_empty_comment_stays_waiting() -> None:
    class ApprovalProvider:
        source = "approval"
        _OPERATIONS = {"reject": ("approval.reject", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            raise AssertionError("empty waiting input must not execute provider")

    waiting_input = run_runtime_v5(
        context=_context(
            "card action",
            chat_id="chat_waiting_input_empty",
            result_context=ResultContext(
                result_type="approval_detail",
                count=1,
                items=({"instance_code": "instance_1", "task_id": "task_1"},),
            ),
            session_context={
                "runtime_v5_action_input": {
                    "action_id": "reject_input_empty",
                    "action_type": "reject",
                    "intent": "approval_reject",
                    "strategy": "approval_reject",
                    "target": {"instance_code": "instance_1", "task_id": "task_1"},
                    "confirmation": {"confirmed": False, "token": "reject_token_empty"},
                    "context": {"company_id": "company_1", "source_ui": "card"},
                    "message": "拒绝这个审批",
                    "sources": ["approval"],
                    "metadata": {"missing_params": ["comment"]},
                }
            },
        ),
        providers={"approval": ApprovalProvider()},
    )
    assert waiting_input.composed.result_context is not None
    runtime_state = waiting_input.composed.result_context.metadata["runtime_state"]

    still_waiting = run_runtime_v5(
        context=_context(
            "原因：",
            chat_id="chat_waiting_input_empty",
            result_context=ResultContext(
                result_type="approval_detail",
                count=1,
                items=({"instance_code": "instance_1", "task_id": "task_1"},),
            ),
            session_context={"runtime_v5_state": runtime_state},
        ),
        providers={"approval": ApprovalProvider()},
    )

    assert still_waiting.execution is None
    assert still_waiting.composed.result_context is not None
    assert still_waiting.composed.result_context.result_type == "runtime_waiting_input"
    runtime_state = still_waiting.composed.result_context.metadata["runtime_state"]
    assert runtime_state["status"] == "waiting"
    assert runtime_state["actions"][0]["status"] == "waiting_input"
    assert runtime_state["metadata"]["transitions"] == ["waiting", "waiting_input"]
    assert runtime_state["actions"][0]["metadata"]["transitions"] == ["waiting_input"]


def test_runtime_missing_params_registry_keeps_text_comment_contract() -> None:
    action_input = runtime_action_input_from_payload(
        {
            "action_id": "reject_input_helper",
            "action_type": "reject",
            "intent": "approval_reject",
            "strategy": "approval_reject",
            "target": {"instance_code": "instance_1", "task_id": "task_1"},
            "confirmation": {"confirmed": False, "token": "reject_token_helper"},
            "context": {"company_id": "company_1", "source_ui": "card"},
            "message": "拒绝这个审批",
            "sources": ["approval"],
            "metadata": {"missing_params": ["comment", ""]},
        }
    )
    pending_action = {
        "id": "reject_token_helper",
        "message": "拒绝这个审批",
        "intent": "approval_reject",
        "strategy": "approval_reject",
        "sources": ["approval"],
        "entities": {"instance_code": "instance_1", "task_id": "task_1"},
        "missing_params": ["comment"],
        "runtime_action_input": {
            "target": {"instance_code": "instance_1", "task_id": "task_1"},
            "metadata": {"missing_params": ["comment"]},
        },
    }

    assert action_input_missing_params(action_input) == ["comment"]
    contract = missing_param_contract(pending_action)
    assert contract is not None
    assert contract.name == "comment"
    assert contract.param_type == TEXT
    assert contract.target_key == "comment"
    assert resolve_missing_param_value(contract, "原因：资料不完整") == "资料不完整"
    assert waiting_input_still_missing(pending_action, "原因：") is True
    assert waiting_input_still_missing(pending_action, "原因：资料不完整") is False

    filled = pending_action_with_user_input(pending_action, "原因：资料不完整")

    assert filled["missing_params"] == []
    assert filled["entities"]["comment"] == "资料不完整"
    assert filled["input_contract"] == {
        "status": "waiting_confirmation",
        "missing_params": [],
        "filled_params": ["comment"],
    }
    assert filled["runtime_action_input"]["target"]["comment"] == "资料不完整"
    assert filled["runtime_action_input"]["metadata"]["filled_params"] == {"comment": "资料不完整"}


def test_runtime_missing_params_registry_supports_user_contract_without_lookup() -> None:
    pending_action = {
        "id": "transfer_1",
        "message": "转交这个审批",
        "entities": {},
        "missing_params": ["target_user"],
        "runtime_action_input": {
            "target": {},
            "metadata": {"missing_params": ["target_user"]},
        },
    }

    contract = missing_param_contract(pending_action)

    assert contract is not None
    assert contract.name == "target_user"
    assert contract.param_type == USER
    assert contract.target_key == "target_user"
    assert resolve_missing_param_value(contract, "转交给张三") == "张三"
    assert resolve_missing_param_value(contract, "@李四") == "李四"
    assert waiting_input_still_missing(pending_action, "转交给") is True
    assert waiting_input_still_missing(pending_action, "转交给张三") is False

    filled = pending_action_with_user_input(pending_action, "转交给张三")

    assert filled["entities"]["target_user"] == "张三"
    assert filled["input_contract"] == {
        "status": "waiting_confirmation",
        "missing_params": [],
        "filled_params": ["target_user"],
    }
    assert filled["runtime_action_input"]["target"]["target_user"] == "张三"
    assert filled["runtime_action_input"]["metadata"]["filled_params"] == {"target_user": "张三"}
    assert "open_id" not in filled["runtime_action_input"]["target"]


def test_runtime_v5_transfer_target_user_waits_for_input_then_confirmation_without_lookup_or_execution() -> None:
    class ApprovalProvider:
        source = "approval"
        _OPERATIONS = {"transfer": ("approval.transfer", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            raise AssertionError("transfer missing-input contract must not execute provider")

    approval_detail = ResultContext(
        result_type="approval_detail",
        count=1,
        items=({"instance_code": "instance_1", "task_id": "task_1"},),
    )
    waiting_input = run_runtime_v5(
        context=_context(
            "card action",
            chat_id="chat_transfer_target_user",
            result_context=approval_detail,
            session_context={
                "runtime_v5_action_input": {
                    "action_id": "transfer_input_1",
                    "action_type": "transfer",
                    "intent": "approval_transfer",
                    "strategy": "approval_transfer",
                    "target": {"instance_code": "instance_1", "task_id": "task_1"},
                    "confirmation": {"confirmed": False, "token": "transfer_input_1"},
                    "context": {
                        "company_id": "company_1",
                        "chat_id": "chat_transfer_target_user",
                        "user_id": "ou_test",
                        "open_id": "ou_test",
                        "source_ui": "card",
                    },
                    "message": "转交这个审批",
                    "sources": ["approval"],
                    "metadata": {"missing_params": ["target_user"]},
                }
            },
        ),
        providers={"approval": ApprovalProvider()},
    )

    assert waiting_input.execution is None
    assert waiting_input.composed.result_context is not None
    assert waiting_input.composed.result_context.result_type == "runtime_waiting_input"
    assert waiting_input.composed.result_context.metadata["missing_params"] == ["target_user"]
    assert waiting_input.composed.result_context.metadata["input_contract"] == {
        "status": "waiting_input",
        "missing_params": ["target_user"],
        "next_state": "waiting_confirmation",
    }
    runtime_state = waiting_input.composed.result_context.metadata["runtime_state"]
    assert runtime_state["actions"][0]["status"] == "waiting_input"
    assert runtime_state["actions"][0]["metadata"]["missing_params"] == ["target_user"]

    waiting_confirmation = run_runtime_v5(
        context=_context(
            "转交给张三",
            chat_id="chat_transfer_target_user",
            result_context=approval_detail,
            session_context={"runtime_v5_state": runtime_state},
        ),
        providers={"approval": ApprovalProvider()},
    )

    assert waiting_confirmation.execution is None
    assert waiting_confirmation.composed.result_context is not None
    assert waiting_confirmation.composed.result_context.result_type == "runtime_pending_confirmation"
    runtime_state = waiting_confirmation.composed.result_context.metadata["runtime_state"]
    assert runtime_state["actions"][0]["status"] == "waiting_confirmation"
    pending_action = runtime_state["actions"][0]["metadata"]["pending_action"]
    assert pending_action["runtime_action_input"]["action_type"] == "transfer"
    assert pending_action["runtime_action_input"]["target"]["target_user"] == "张三"
    assert "open_id" not in pending_action["runtime_action_input"]["target"]


def test_runtime_v5_transfer_confirmation_is_guarded_before_provider_execution() -> None:
    class ApprovalProvider:
        source = "approval"
        _OPERATIONS = {"transfer": ("approval.transfer", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            raise AssertionError("guarded transfer confirmation must not execute provider")

    approval_detail = ResultContext(
        result_type="approval_detail",
        count=1,
        items=({"instance_code": "instance_1", "task_id": "task_1"},),
    )
    waiting_input = run_runtime_v5(
        context=_context(
            "card action",
            chat_id="chat_transfer_guard",
            result_context=approval_detail,
            session_context={
                "runtime_v5_action_input": {
                    "action_id": "transfer_guard_1",
                    "action_type": "transfer",
                    "intent": "approval_transfer",
                    "strategy": "approval_transfer",
                    "target": {"instance_code": "instance_1", "task_id": "task_1"},
                    "confirmation": {"confirmed": False, "token": "transfer_guard_1"},
                    "context": {"company_id": "company_1", "source_ui": "card"},
                    "message": "转交这个审批",
                    "sources": ["approval"],
                    "metadata": {"missing_params": ["target_user"]},
                }
            },
        ),
        providers={"approval": ApprovalProvider()},
    )
    assert waiting_input.composed.result_context is not None
    runtime_state = waiting_input.composed.result_context.metadata["runtime_state"]

    waiting_confirmation = run_runtime_v5(
        context=_context(
            "转交给张三",
            chat_id="chat_transfer_guard",
            result_context=approval_detail,
            session_context={"runtime_v5_state": runtime_state},
        ),
        providers={"approval": ApprovalProvider()},
    )
    assert waiting_confirmation.composed.result_context is not None
    runtime_state = waiting_confirmation.composed.result_context.metadata["runtime_state"]
    assert runtime_state["actions"][0]["status"] == "waiting_confirmation"

    guarded = run_runtime_v5(
        context=_context(
            "确认执行",
            chat_id="chat_transfer_guard",
            result_context=approval_detail,
            session_context={"runtime_v5_state": runtime_state},
        ),
        providers={"approval": ApprovalProvider()},
    )

    assert guarded.execution is not None
    assert guarded.execution.status == "error"
    assert guarded.composed.result_context is not None
    assert guarded.composed.result_context.result_type == "runtime_action"
    runtime_state = guarded.composed.result_context.metadata["runtime_state"]
    assert runtime_state["status"] == "failed"
    assert runtime_state["error"] == "guarded_pending_user_resolution"
    assert runtime_state["actions"][0]["status"] == "failed"
    assert runtime_state["actions"][0]["metadata"]["transitions"] == ["waiting_input", "waiting_confirmation", "failed"]
    payload = interaction_payload_from_runtime_result(runtime_result_from_payload(guarded.composed.metadata["runtime_result"]))
    assert payload.payload_type == "feedback"
    assert payload.status == "error"
    assert payload.metadata["result_context"]["runtime_state"]["error"] == "guarded_pending_user_resolution"


def test_runtime_result_builder_freezes_target_ui_and_actions() -> None:
    permission = PermissionDecision(allowed=True, requires_confirmation=False, execution_identity="user")

    approval_list = build_runtime_result(
        command_plan=_command_plan("approval_query"),
        permission=permission,
        execution=None,
        composed=ComposedAnswer(
            answer="你有 1 条待审批。",
            result_context=ResultContext(
                result_type="approval_list",
                count=1,
                items=({"approval_code": "approval_1", "instance_code": "instance_1", "task_id": "task_1"},),
            ),
        ),
    )
    assert approval_list.target_ui == "card"
    assert approval_list.metadata["company_id"] == "company_1"
    assert [action["action"] for action in approval_list.actions] == ["open_detail"]
    assert approval_list.actions[0]["target_ui"] == "sidepanel"

    approval_detail = build_runtime_result(
        command_plan=_command_plan("approval_detail", target_ui="sidepanel"),
        permission=permission,
        execution=None,
        composed=ComposedAnswer(
            answer="付款审批详情。",
            result_context=ResultContext(
                result_type="approval_detail",
                count=1,
                items=({"approval_code": "approval_1", "instance_code": "instance_1", "task_id": "task_1"},),
            ),
        ),
    )
    assert approval_detail.target_ui == "sidepanel"
    assert [action["action"] for action in approval_detail.actions] == ["approve", "reject"]
    assert all(action["target_ui"] == "card" for action in approval_detail.actions)

    waiting_input = build_runtime_result(
        command_plan=_command_plan("approval_reject", question_type="action"),
        permission=permission,
        execution=None,
        composed=ComposedAnswer(
            answer="请补充拒绝原因。",
            result_context=ResultContext(result_type="runtime_waiting_input", count=1),
            metadata={"waiting_input": True},
        ),
    )
    assert waiting_input.status == "waiting"
    assert waiting_input.target_ui == "card"
    assert waiting_input.actions == ()

    pending_confirmation = build_runtime_result(
        command_plan=_command_plan("approval_reject", question_type="action"),
        permission=PermissionDecision(allowed=True, requires_confirmation=True, execution_identity="user"),
        execution=None,
        composed=ComposedAnswer(
            answer="审批拒绝等待确认。",
            result_context=ResultContext(
                result_type="runtime_pending_confirmation",
                count=1,
                metadata={"confirmation_token": "confirm_1"},
            ),
            metadata={"requires_confirmation": True},
        ),
    )
    assert pending_confirmation.status == "waiting"
    assert pending_confirmation.target_ui == "card"
    assert [action["action"] for action in pending_confirmation.actions] == ["confirm", "cancel"]
    assert [action["confirmation_token"] for action in pending_confirmation.actions] == ["confirm_1", "confirm_1"]

    runtime_action = build_runtime_result(
        command_plan=_command_plan("approval_reject", question_type="action"),
        permission=permission,
        execution=None,
        composed=ComposedAnswer(
            answer="审批已拒绝。",
            result_context=ResultContext(result_type="runtime_action", count=1),
        ),
    )
    assert runtime_action.target_ui == "card"
    assert runtime_action.actions == ()


def test_runtime_result_payload_serializes_builder_output() -> None:
    permission = PermissionDecision(allowed=True, requires_confirmation=False, execution_identity="user")
    result = build_runtime_result(
        command_plan=_command_plan("approval_detail", target_ui="sidepanel"),
        permission=permission,
        execution=None,
        composed=ComposedAnswer(
            answer="付款审批详情。",
            result_context=ResultContext(
                result_type="approval_detail",
                count=1,
                items=({"approval_code": "approval_1", "instance_code": "instance_1", "task_id": "task_1"},),
            ),
        ),
    )

    payload = runtime_result_payload(result)

    assert payload["result_type"] == "approval_detail"
    assert payload["status"] == "skipped"
    assert payload["title"] == "审批详情"
    assert payload["summary"] == "付款审批详情。"
    assert payload["target_ui"] == "sidepanel"
    assert payload["item_count"] == 1
    assert [action["action"] for action in payload["actions"]] == ["approve", "reject"]
    assert payload["metadata"]["company_id"] == "company_1"
    assert payload["metadata"]["strategy"] == "approval_detail"


def test_runtime_result_includes_enterprise_scope_context_for_task_query() -> None:
    permission = PermissionDecision(
        allowed=True,
        requires_confirmation=False,
        execution_identity="bot",
        metadata={
            "policy_subject": {"actor_open_id": "ou_workspace", "company_id": "company_1"},
            "policy_scope": {"requested_scope": "self", "resolved_scope": "self"},
            "identity_decision": {
                "actor_identity": "BOT",
                "credential_mode": "TENANT_TOKEN",
                "allows_fallback": True,
                "requires_authorization": False,
                "authorization_status": "AUTHORIZED",
            },
            "allowed_resource_types": ["task"],
            "denied_resource_types": [],
        },
    )
    result = build_runtime_result(
        command_plan=_command_plan("task_query", result_type="task_query", sources=("task",)),
        permission=permission,
        execution=None,
        composed=ComposedAnswer(
            answer="你有 1 条任务。",
            result_context=ResultContext(
                result_type="task_list",
                count=1,
                items=({"title": "跟进客户", "task_guid": "task/1"},),
            ),
        ),
    )

    payload = runtime_result_payload(result)

    assert payload["result_type"] == "task_list"
    assert payload["metadata"]["scope_context"] == {
        "scope": "SELF",
        "company_id": "company_1",
        "filters": {},
    }
    policy_filter = payload["metadata"]["policy_result_filter"]
    assert policy_filter["status"] == "applied"
    assert policy_filter["filter_version"] == "policy_result_filter_v0"
    assert policy_filter["scope"] == "SELF"
    assert policy_filter["allowed_resource_types"] == ["task"]
    assert policy_filter["denied_resource_types"] == []
    assert policy_filter["identity_decision"]["actor_identity"] == "BOT"
    assert policy_filter["resource_filters"] == [
        {
            "index": 0,
            "resource_type": "operational",
            "visible": True,
            "redacted_fields": [],
            "hidden_sections": [],
            "aggregation_only": False,
            "reason_hidden": False,
            "source_reference_visible": True,
        }
    ]
    assert policy_filter["section_filters"]["task"]["visible"] is True


def test_runtime_result_filter_hides_cognitive_source_references_for_company_scope() -> None:
    result = build_runtime_result(
        command_plan=_command_plan("approval_query", data_scope="company", sources=("approval", "insight")),
        permission=PermissionDecision(
            allowed=True,
            requires_confirmation=False,
            execution_identity="bot",
            metadata={
                "policy_scope": {"requested_scope": "company", "resolved_scope": "company"},
                "identity_decision": {"actor_identity": "BOT", "credential_mode": "TENANT_TOKEN"},
                "allowed_resource_types": ["approval", "insight"],
            },
        ),
        execution=None,
        composed=ComposedAnswer(
            answer="公司审批风险趋势。",
            result_context=ResultContext(
                result_type="approval_list",
                count=1,
                items=(
                    {
                        "resource_type": "insight",
                        "summary": "高金额审批增多",
                        "source_event_ids": ["event_1"],
                        "source_object_id": "approval_1",
                        "raw": {"source_object_id": "approval_1", "internal_note": "kept"},
                    },
                ),
            ),
        ),
    )

    payload = runtime_result_payload(result)
    policy_filter = payload["metadata"]["policy_result_filter"]

    assert policy_filter["scope"] == "COMPANY"
    assert policy_filter["aggregation_only"] is True
    assert policy_filter["source_reference_visible"] is False
    assert policy_filter["redaction_applied"] is True
    assert policy_filter["resource_filters"][0]["aggregation_only"] is True
    assert policy_filter["resource_filters"][0]["source_reference_visible"] is False
    assert policy_filter["section_filters"]["approval"]["source_reference_visible"] is True
    assert policy_filter["section_filters"]["insight"]["source_reference_visible"] is False
    assert payload["items"] == [{"resource_type": "insight", "summary": "高金额审批增多"}]


def test_runtime_result_filter_uses_resource_plane_for_custom_cognitive_type() -> None:
    result = build_runtime_result(
        command_plan=_command_plan("approval_query", data_scope="company", sources=("approval_snapshot",)),
        permission=PermissionDecision(
            allowed=True,
            requires_confirmation=False,
            execution_identity="bot",
            metadata={"allowed_resource_types": ["approval_snapshot"]},
        ),
        execution=None,
        composed=ComposedAnswer(
            answer="审批当前认知。",
            result_context=ResultContext(
                result_type="approval_snapshot",
                count=1,
                items=(
                    {
                        "resource_plane": "cognitive",
                        "resource_type": "approval_snapshot",
                        "summary": "需关注",
                        "source_event_ids": ["event_1"],
                        "source_object_id": "approval_1",
                    },
                ),
            ),
        ),
    )

    payload = runtime_result_payload(result)
    policy_filter = payload["metadata"]["policy_result_filter"]

    assert policy_filter["aggregation_only"] is True
    assert policy_filter["source_reference_visible"] is False
    assert policy_filter["resource_filters"][0]["aggregation_only"] is True
    assert payload["items"] == [
        {
            "resource_plane": "cognitive",
            "resource_type": "approval_snapshot",
            "summary": "需关注",
        }
    ]


def test_runtime_result_filter_removes_denied_mixed_resource_items() -> None:
    result = build_runtime_result(
        command_plan=_command_plan("task_query", result_type="task_query", sources=("task", "insight")),
        permission=PermissionDecision(
            allowed=True,
            requires_confirmation=False,
            execution_identity="bot",
            metadata={
                "allowed_resource_types": ["task"],
                "denied_resource_types": ["insight"],
            },
        ),
        execution=None,
        composed=ComposedAnswer(
            answer="你有 1 条任务。",
            result_context=ResultContext(
                result_type="task_list",
                count=2,
                items=(
                    {"resource_type": "task", "title": "跟进客户", "task_guid": "task/1"},
                    {"resource_type": "insight", "summary": "负荷偏高"},
                ),
            ),
        ),
    )

    payload = runtime_result_payload(result)
    policy_filter = payload["metadata"]["policy_result_filter"]

    assert payload["items"] == [{"resource_type": "task", "title": "跟进客户", "task_guid": "task/1"}]
    assert policy_filter["redaction_applied"] is True
    assert policy_filter["resource_filters"][1]["visible"] is False
    assert policy_filter["section_filters"]["insight"]["visible"] is False


def test_runtime_result_filter_builds_actions_from_filtered_items() -> None:
    result = build_runtime_result(
        command_plan=_command_plan("approval_query", data_scope="company", sources=("approval", "insight")),
        permission=PermissionDecision(
            allowed=True,
            requires_confirmation=False,
            execution_identity="bot",
            metadata={
                "allowed_resource_types": ["approval"],
                "denied_resource_types": ["insight"],
            },
        ),
        execution=None,
        composed=ComposedAnswer(
            answer="公司审批风险。",
            result_context=ResultContext(
                result_type="approval_list",
                count=2,
                items=(
                    {"resource_type": "approval", "approval_code": "approval_1", "instance_code": "instance_1", "task_id": "task_1"},
                    {"resource_type": "insight", "summary": "隐藏的风险来源"},
                ),
            ),
        ),
    )

    payload = runtime_result_payload(result)

    assert [item["resource_type"] for item in payload["items"]] == ["approval"]
    assert len(payload["actions"]) == 1
    assert payload["actions"][0]["approval_code"] == "approval_1"


def test_runtime_permission_denies_company_scope_for_ordinary_employee() -> None:
    company_id = uuid4()
    context = RuntimeContext(
        identity=RuntimeIdentity(open_id="ou_member", role="member"),
        runtime_scope=RuntimeScope(company_ids=(company_id,), active_company_id=company_id),
        current_message="所有延期任务",
    )
    intent = IntentResult(
        question_type="query",
        intent="task_query",
        data_scope="company",
        confidence=0.9,
        canonical_question="所有延期任务",
    )
    plan = PlannerResult(strategy="task_query", sources=("task",))

    permission = check_runtime_permission(context=context, intent=intent, plan=plan)

    assert permission.allowed is False
    assert permission.reason == "permission_denied"


def test_runtime_v5_interaction_payload_preserves_runtime_result_actions() -> None:
    payload = interaction_payload_from_runtime_result(
        RuntimeResult(
            result_type="approval_detail",
            status="success",
            title="审批详情",
            summary="付款审批详情。",
            actions=(
                {
                    "action": "approve",
                    "label": "同意",
                    "target_ui": "card",
                    "requires_confirmation": True,
                },
            ),
            target_ui="sidepanel",
            metadata={"company_id": "company_1", "strategy": "approval_detail"},
        )
    )

    assert payload.payload_type == "summary"
    assert payload.metadata["company_id"] == "company_1"
    assert payload.metadata["target_ui"] == "sidepanel"
    assert payload.actions[0]["action"] == "approve"


def test_interaction_payload_builder_freezes_payload_types_and_passthrough() -> None:
    pending_actions = (
        {"action": "confirm", "target_ui": "card", "confirmation_token": "confirm_1"},
        {"action": "cancel", "target_ui": "card", "confirmation_token": "confirm_1"},
    )
    pending_payload = interaction_payload_from_runtime_result(
        RuntimeResult(
            result_type="runtime_pending_confirmation",
            status="waiting",
            title="等待确认",
            summary="审批拒绝等待确认。",
            actions=pending_actions,
            target_ui="card",
            metadata={"strategy": "approval_reject"},
        )
    )
    assert pending_payload.payload_type == "action"
    assert pending_payload.actions == pending_actions
    assert pending_payload.metadata["target_ui"] == "card"
    assert pending_payload.metadata["result_type"] == "runtime_pending_confirmation"

    waiting_input_payload = interaction_payload_from_runtime_result(
        RuntimeResult(
            result_type="runtime_waiting_input",
            status="waiting",
            title="缺少参数",
            summary="请补充拒绝原因。",
            target_ui="card",
            metadata={"input_contract": {"status": "waiting_input"}},
        )
    )
    assert waiting_input_payload.payload_type == "action"
    assert waiting_input_payload.metadata["input_contract"] == {"status": "waiting_input"}

    feedback_payload = interaction_payload_from_runtime_result(
        RuntimeResult(
            result_type="runtime_action",
            status="success",
            title="审批拒绝",
            summary="审批已拒绝。",
            target_ui="card",
        )
    )
    assert feedback_payload.payload_type == "feedback"
    assert feedback_payload.actions == ()


def test_interaction_payload_payload_serializes_runtime_result_payload() -> None:
    runtime_result = runtime_result_from_payload(
        {
            "result_type": "runtime_pending_confirmation",
            "status": "waiting",
            "title": "等待确认",
            "summary": "审批拒绝等待确认。",
            "items": [{"source": "runtime", "status": "waiting"}],
            "actions": [{"action": "confirm", "target_ui": "card", "confirmation_token": "confirm_1"}],
            "target_ui": "card",
            "metadata": {
                "requires_confirmation": True,
                "result_context": {"error": ""},
            },
        }
    )
    payload = interaction_payload_payload(interaction_payload_from_runtime_result(runtime_result))

    assert payload["payload_type"] == "action"
    assert payload["status"] == "waiting"
    assert payload["items"] == [{"source": "runtime", "status": "waiting"}]
    assert payload["actions"] == [{"action": "confirm", "target_ui": "card", "confirmation_token": "confirm_1"}]
    assert payload["metadata"]["target_ui"] == "card"
    assert payload["metadata"]["result_type"] == "runtime_pending_confirmation"
    assert payload["metadata"]["result_context"] == {"error": ""}


def test_feishu_base_provider_creates_base_when_app_token_absent() -> None:
    calls: list[tuple[str, dict]] = []

    class ToolResult:
        def __init__(self, payload: dict) -> None:
            self.status = ToolExecutionStatus.SUCCESS
            self.structured_result = {"response_payload": payload}
            self.answer = ""
            self.error = ""

    class Provider(FeishuBaseProvider):
        def __init__(self) -> None:
            pass

        def _execute_tool(
            self,
            request: ProviderRequest,
            *,
            tool_name: str,
            params: dict | None = None,
            confirm_write: bool = False,
        ):
            calls.append((tool_name, params or {}))
            if tool_name == "feishu_bitable_base_create":
                return ToolResult({"app_token": "bascn-created", "table_id": "tbl_created", "url": "https://base.example"})
            if tool_name == "feishu_bitable_record_batch_create":
                return ToolResult({"records": [{"record_id": "rec_1"}]})
            raise AssertionError(tool_name)

    context = _context("帮我创建一个表然后把组织架构放进去，把文件发给我")
    runtime_result = run_runtime_v5(context=context, providers={})
    request = ProviderRequest(
        source="base",
        operation="write_records",
        intent=runtime_result.intent,
        planner=runtime_result.plan,
        context=context,
        execution_identity="user",
        params={
            "previous_results": (
                ProviderResult(
                    source="people",
                    status="success",
                    result_type="organization_snapshot",
                    items=({"name": "张三", "department": "研发", "leader": "李四"},),
                ),
            )
        },
    )

    result = Provider().execute(request)

    assert [name for name, _ in calls] == ["feishu_bitable_base_create", "feishu_bitable_record_batch_create"]
    assert result.status == "success"
    assert result.metadata["created_base"] is True
    assert result.metadata["url"] == "https://base.example"


def test_runtime_v5_result_followup_reads_structured_items_first() -> None:
    result = run_runtime_v5(
        context=_context(
            "第一个是谁",
            result_context=ResultContext(
                result_type="people_list",
                count=2,
                items=(
                    {"name": "张三", "department": "研发"},
                    {"name": "李四", "department": "销售"},
                ),
                answer="上一轮查到两个人。",
            ),
        ),
        providers={},
    )

    assert "张三" in result.composed.answer
    assert "上一轮查到两个人" not in result.composed.answer
