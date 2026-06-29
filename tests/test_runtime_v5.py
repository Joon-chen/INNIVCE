from types import SimpleNamespace
from uuid import uuid4
import json
import time

import pytest

from app.services.runtime_v5.models import (
    CommandPlan,
    ComposedAnswer,
    ExecutionResult,
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
from app.services.llm.call_trace import record_llm_call_trace
from app.services.llm.prompt_audit import prompt_audit_payload
from app.services.runtime_v5.clarification import build_clarification_guide
from app.services.runtime_v5.clarification_reply import resolve_clarification_reply
from app.services.runtime_v5.feishu_resource_providers import FeishuBaseProvider, FeishuCalendarProvider, FeishuIMProvider, FeishuPeopleProvider, FeishuTaskProvider, KnowledgeProvider
from app.services.runtime_v5.feishu_resource_providers import _apply_people_domain_filters
from app.services.runtime_v5.feishu_resource_providers import _knowledge_event_item, _memory_item, _read_knowledge_document_candidate, _registered_knowledge_resource_candidates, _workevent_item
from app.services.runtime_v5.feishu_resource_providers import WebProvider
from app.services.runtime_v5.capability_router import CapabilityRouter
from app.services.runtime_v5.composer import compose_answer
from app.services.runtime_v5.interaction_layer import interaction_payload_from_runtime_result, interaction_payload_payload
from app.services.gateway.card_renderer import build_interactive_card, build_runtime_result_card, should_use_interactive_card
from app.services.runtime_v5.command_frame import build_command_frame
from app.services.runtime_v5.intent_layers import should_start_new_question_over_result_context
from app.services.runtime_v5.command_layer import build_command_plan
from app.services.runtime_v5.command_route_observer import observe_command_route
from app.services.runtime_v5.planner import plan_task
from app.services.runtime_v5.result_followup import detect_result_followup
from app.services.runtime_v5.people_resolver import filter_people_by_department, filter_people_by_title, normalize_people_item, resolve_people_from_items
from app.services.runtime_v5.llm_intent import LLMCommandIntentCandidate, _prompt, llm_command_intent_candidate, validate_llm_command_intent
from app.services.runtime_v5.action_observer import route_observation_summary
from app.services.runtime_v5.diagnostics import runtime_trace_summary
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
from app.services.runtime_v5.runtime import _is_standalone_confirmation_message
from app.services.tools.base import ToolExecutionStatus, ToolRequest
from app.services.tools.providers import feishu_mcp
from app.services.feishu import bot_runtime


def recognize_intent(question: str, context: RuntimeContext) -> IntentResult:
    """Compatibility helper for old tests.

    The production `runtime_v5.intent` entry was removed. Tests that still need
    an IntentResult must obtain it through the Command Layer.
    """

    if context.current_message != question:
        context = RuntimeContext(
            identity=context.identity,
            runtime_scope=context.runtime_scope,
            current_message=question,
            session_context=context.session_context,
            result_context=context.result_context,
            profile=context.profile,
            chat_id=context.chat_id,
        )
    return build_command_plan(context=context).intent_result


def _context(
    message: str,
    *,
    display_name: str = "",
    department_names: tuple[str, ...] = (),
    job_title: str = "",
    result_context: ResultContext | None = None,
    session_context: dict | None = None,
    chat_id: str | None = None,
) -> RuntimeContext:
    company_id = uuid4()
    return RuntimeContext(
        identity=RuntimeIdentity(
            open_id="ou_test",
            role="owner",
            display_name=display_name,
            department_names=department_names,
            job_title=job_title,
            domains=("all",),
        ),
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


def test_runtime_v5_identity_smalltalk_does_not_route_to_people_lookup() -> None:
    for question in (
        "你好呀",
        "你好，现在几点了。",
        "你是谁",
        "我是谁",
        "我是谁呀",
        "你知道我吗",
        "你知道我是谁吗",
        "你知道这个表情是什么情绪吗",
        "你知道我现在这个公司的职位吗",
        "我是什么性格",
        "你太机械了",
        "你想联网让你变得更强大一点吗",
        "我跟你聊天，怎么什么都是要联网了呢",
    ):
        intent = recognize_intent(question, _context(question))

        assert intent.intent == "smalltalk"
        assert intent.data_scope == "self"


def test_runtime_v5_open_ended_company_question_does_not_route_to_people_lookup() -> None:
    for question in (
        "这个公司谁是老板",
        "这个公司的老板是谁",
        "我刚才不是告诉你我是老板吗",
    ):
        intent = recognize_intent(question, _context(question))

        assert intent.intent != "people_lookup"


def test_runtime_v5_company_questions_route_to_general_knowledge_query(monkeypatch) -> None:

    for question in (
        "主营业务",
        "公司的主营业务是什么",
        "公司是做什么的你知道吗",
        "能躬行科技公司是做什么的，你知道吗",
    ):
        intent = recognize_intent(question, _context(question))

        assert intent.intent == "general_query"
        assert intent.data_scope == "company"
        assert intent.entities.get("knowledge_context") == "company_profile"


def test_runtime_v5_company_questions_use_knowledge_source_not_people_or_workevent(monkeypatch) -> None:

    intent = recognize_intent("能躬行科技公司是做什么的，你知道吗", _context("能躬行科技公司是做什么的，你知道吗"))
    plan = plan_task(intent)

    assert plan.strategy == "general_query"
    assert plan.sources == ("knowledge",)
    assert "people" not in plan.sources
    assert "workevent" not in plan.sources
    assert "web" not in plan.sources


def test_runtime_v5_people_aggregate_questions_route_to_people_not_workspace(monkeypatch) -> None:

    for question in (
        "公司有多少个人",
        "公司有多少个男生",
        "全公司人员构成怎么样",
    ):
        intent = recognize_intent(question, _context(question))
        plan = plan_task(intent)

        assert intent.intent == "organization_snapshot"
        assert intent.data_scope == "organization"
        assert intent.entities["view"] == "people_aggregate"
        assert plan.sources == ("people",)


def test_runtime_v5_department_people_questions_route_to_department_members() -> None:
    cases = (
        ("半导体事业部有多少人，分别是谁", "半导体事业部"),
        ("公司财务部门有多少人？", "财务部门"),
        ("商务部有多少人", "商务部"),
        ("半导体事业部都有多少人", "半导体事业部"),
        ("业务部有多少人", "业务部"),
        ("商务组有多少人", "商务组"),
    )
    for question, keyword in cases:
        intent = recognize_intent(question, _context(question))
        plan = plan_task(intent)

        assert intent.intent == "department_members"
        assert intent.data_scope == "department"
        assert intent.entities["keyword"] == keyword
        assert plan.sources == ("people",)


def test_conversation_first_department_count_preserves_organization_scope_and_keyword() -> None:
    cases = (
        ("商务部有多少人", "商务部"),
        ("商务部多少人", "商务部"),
        ("商务组有哪些人", "商务组"),
        ("半导体事业部多少人", "半导体事业部"),
        ("公司财务部门有多少人？", "财务部门"),
    )
    for question, keyword in cases:
        plan = build_command_plan(context=_context(question))

        assert plan.intent == "department_members"
        assert plan.intent_result.data_scope == "department"
        assert plan.intent_result.entities["keyword"] == keyword
        assert plan.command_frame is not None
        assert plan.command_frame.domain == "People"
        assert plan.command_frame.params["domain_query"]["subject"] == {"type": "group", "department": keyword}
        assert plan.planner_result.sources == ("people",)


def test_runtime_v5_people_provider_uses_organization_foundation_for_department_members(monkeypatch) -> None:
    def fake_resolve_department_members(db, *, company_id, query):
        return SimpleNamespace(
            resolution=SimpleNamespace(
                query=query,
                normalized_query="商务部",
                resolved_type="department",
                resolved_id="dept_business",
                resolved_name="商务组",
                resolved_department_id="dept_business",
                confidence=0.96,
                reason="unit_suffix_match",
                needs_clarification=False,
                candidates=(),
            ),
            items=(
                {
                    "name": "张三",
                    "open_id": "ou_zhang",
                    "title": "商务经理",
                    "department": "商务组",
                    "department_names": ["商务组"],
                },
            ),
        )

    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers.resolve_department_members", fake_resolve_department_members)

    class NoSnapshotPeopleProvider(FeishuPeopleProvider):
        def _execute_tool(self, *args, **kwargs):  # pragma: no cover - should not be called
            raise AssertionError("Organization Foundation path must not call Feishu snapshot.")

    provider = NoSnapshotPeopleProvider(db=None)
    result = provider.execute(
        ProviderRequest(
            source="people",
            operation="list_department_members",
            intent=IntentResult(
                question_type="query",
                intent="department_members",
                data_scope="department",
                entities={"keyword": "商务部"},
                canonical_question="商务部有多少人",
            ),
            planner=_command_plan("department_members", sources=("people",)),
            context=_context("商务部有多少人"),
            execution_identity="bot",
            params={"keyword": "商务部"},
        )
    )

    assert result.status == "success"
    assert result.result_type == "department_members"
    assert result.count == 1
    assert result.items[0]["name"] == "张三"
    assert result.metadata["organization_foundation"] is True
    assert result.metadata["organization_resolution"]["resolved_id"] == "dept_business"
    assert "商务部" in result.answer
    assert "商务组" in result.answer
    assert "张三" in result.answer


def test_runtime_v5_people_provider_does_not_fallback_to_snapshot_when_org_resolution_is_ambiguous(monkeypatch) -> None:
    def fake_resolve_department_members(db, *, company_id, query):
        return SimpleNamespace(
            resolution=SimpleNamespace(
                query=query,
                normalized_query="商务",
                resolved_type="",
                resolved_id="",
                resolved_name="",
                resolved_department_id="",
                confidence=0.0,
                reason="not_resolved",
                needs_clarification=True,
                candidates=(
                    SimpleNamespace(target_type="department", target_id="dept_business", name="商务部", confidence=0.72, reason="name_near"),
                    SimpleNamespace(target_type="group", target_id="group_business", name="商务组", confidence=0.72, reason="name_near"),
                ),
            ),
            items=(),
        )

    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers.resolve_department_members", fake_resolve_department_members)

    class NoSnapshotPeopleProvider(FeishuPeopleProvider):
        def _execute_tool(self, *args, **kwargs):  # pragma: no cover - should not be called
            raise AssertionError("Ambiguous Organization Resolver result must not fall back to full snapshot.")

    provider = NoSnapshotPeopleProvider(db=None)
    result = provider.execute(
        ProviderRequest(
            source="people",
            operation="list_department_members",
            intent=IntentResult(
                question_type="query",
                intent="department_members",
                data_scope="department",
                entities={"keyword": "商务"},
                canonical_question="商务多少人",
            ),
            planner=_command_plan("department_members", sources=("people",)),
            context=_context("商务多少人"),
            execution_identity="bot",
            params={"keyword": "商务"},
        )
    )

    assert result.status == "success"
    assert result.count == 0
    assert result.error == "organization_resolution_not_resolved"
    assert "商务部" in result.answer
    assert "商务组" in result.answer


def test_runtime_v5_person_phone_question_after_people_result_starts_new_lookup() -> None:
    calls: list[dict] = []

    class PeopleProvider:
        source = "people"
        _OPERATIONS = {"search_person": ("feishu_contact_user_search", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            calls.append({"operation": request.operation, "params": request.params})
            return ProviderResult(
                source="people",
                status="success",
                result_type="people_search",
                count=1,
                items=({"name": "王悦", "mobile": "+8613800000000"},),
                answer="我在通讯录里找到王悦。",
            )

    result_context = ResultContext(
        result_type="organization_snapshot",
        count=2,
        items=(
            {"name": "张三", "title": "后端工程师"},
            {"name": "李四", "title": "测试工程师"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="上一轮通讯录结果。",
    )

    result = run_runtime_v5(
        context=_context("王悦的电话号码是多少", result_context=result_context),
        providers={"people": PeopleProvider()},
    )

    assert calls[0]["operation"] == "search_person"
    assert calls[0]["params"]["keyword"] == "王悦"
    assert result.intent.intent == "people_lookup"
    assert result.composed.result_context is not None
    assert result.composed.result_context.result_type == "people_search"


def test_runtime_v5_preserves_provider_membership_count_basis_in_result_context() -> None:
    class PeopleProvider:
        source = "people"
        _OPERATIONS = {"list_department_members": ("feishu_contact_organization_snapshot", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            return ProviderResult(
                source="people",
                status="success",
                result_type="department_members",
                count=7,
                items=tuple({"name": name} for name in ("戴留兴", "缪瀛", "余莲莲", "张盛", "卢敏阳", "张瑞云", "王悦")),
                metadata={
                    "entity_domain": "People",
                    "organization_foundation": True,
                    "organization_resolution": {"query": "半导体事业部", "resolved_name": "半导体事业部"},
                    "display_member_count": 9,
                    "unique_member_count": 7,
                    "count_basis": "direct_members_plus_child_department_member_counts",
                },
                answer="半导体事业部目前 7 人。",
            )

    result = run_runtime_v5(
        context=_context("半导体事业部有多少人"),
        providers={"people": PeopleProvider()},
    )

    assert result.composed.answer == "按组织架构展示口径是 9 人；去重后是 7 位同事。"
    assert result.composed.result_context is not None
    assert result.composed.result_context.metadata["display_member_count"] == 9
    assert result.composed.result_context.metadata["unique_member_count"] == 7


def test_runtime_v5_department_children_relation_uses_organization_foundation(monkeypatch) -> None:
    def fake_resolve_department_members(db, *, company_id, query):
        return SimpleNamespace(
            resolution=SimpleNamespace(
                resolved_department_id="dept_power",
                resolved_name="半导体事业部",
                query=query,
                normalized_query="半导体事业部",
                resolved_type="department",
                resolved_id="dept_power",
                confidence=0.96,
                reason="name_exact",
                needs_clarification=False,
                candidates=(),
            ),
            items=({"name": "戴留兴"},),
            metadata={
                "resolved_department_name": "半导体事业部",
                "child_member_counts": [
                    {"name": "产品部", "source_department_id": "dept_product", "member_count": 2},
                    {"name": "销售部", "source_department_id": "dept_sales", "member_count": 2},
                    {"name": "项目部", "source_department_id": "dept_project", "member_count": 4},
                ],
            },
        )

    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers.resolve_department_members", fake_resolve_department_members)
    context = _context("半导体事业部下面有几个部门")
    plan = build_command_plan(context=context)
    result = FeishuPeopleProvider(db=None).execute(
        ProviderRequest(
            source="people",
            operation="list_department_members",
            intent=plan.intent_result,
            planner=plan.planner_result,
            context=context,
            execution_identity="bot",
            params={"keyword": plan.intent_result.entities["keyword"]},
        )
    )

    assert result.result_type == "department_members"
    assert result.count == 3
    assert [item["name"] for item in result.items] == ["产品部", "销售部", "项目部"]
    assert result.metadata["organization_relation"] == "children"
    assert result.answer == "半导体事业部下面有 3 个直属子部门：产品部、销售部、项目部。"


def test_runtime_v5_department_children_relation_survives_composer(monkeypatch) -> None:
    def fake_resolve_department_members(db, *, company_id, query):
        return SimpleNamespace(
            resolution=SimpleNamespace(
                resolved_department_id="dept_power",
                resolved_name="半导体事业部",
                query=query,
                normalized_query="半导体事业部",
                resolved_type="department",
                resolved_id="dept_power",
                confidence=0.96,
                reason="name_exact",
                needs_clarification=False,
                candidates=(),
            ),
            items=({"name": "戴留兴"},),
            metadata={
                "resolved_department_name": "半导体事业部",
                "child_member_counts": [
                    {"name": "产品部", "source_department_id": "dept_product", "member_count": 2},
                    {"name": "销售部", "source_department_id": "dept_sales", "member_count": 2},
                    {"name": "项目部", "source_department_id": "dept_project", "member_count": 4},
                ],
            },
        )

    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers.resolve_department_members", fake_resolve_department_members)

    result = run_runtime_v5(
        context=_context("半导体事业部下面有几个部门"),
        providers={"people": FeishuPeopleProvider(db=None)},
    )

    assert result.composed.answer == "半导体事业部下面有 3 个直属子部门：产品部、销售部、项目部。"
    assert "展示口径" not in result.composed.answer


def test_runtime_v5_department_leader_relation_uses_organization_foundation(monkeypatch) -> None:
    def fake_resolve_department_members(db, *, company_id, query):
        return SimpleNamespace(
            resolution=SimpleNamespace(
                resolved_department_id="dept_power",
                resolved_name="半导体事业部",
                query=query,
                normalized_query="半导体事业部",
                resolved_type="department",
                resolved_id="dept_power",
                confidence=0.96,
                reason="name_exact",
                needs_clarification=False,
                candidates=(),
            ),
            items=({"name": "戴留兴"},),
            metadata={
                "resolved_department_name": "半导体事业部",
                "leader_items": [{"name": "戴留兴", "title": "总经理", "open_id": "ou_max"}],
            },
        )

    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers.resolve_department_members", fake_resolve_department_members)
    context = _context("半导体事业部的负责人是谁")
    plan = build_command_plan(context=context)
    result = FeishuPeopleProvider(db=None).execute(
        ProviderRequest(
            source="people",
            operation="list_department_members",
            intent=plan.intent_result,
            planner=plan.planner_result,
            context=context,
            execution_identity="bot",
            params={"keyword": plan.intent_result.entities["keyword"]},
        )
    )

    assert result.count == 1
    assert result.items == ({"name": "戴留兴", "title": "总经理", "open_id": "ou_max"},)
    assert result.metadata["organization_relation"] == "leader"
    assert result.answer == "半导体事业部的负责人是 戴留兴。"


def test_runtime_v5_department_leader_relation_survives_composer(monkeypatch) -> None:
    def fake_resolve_department_members(db, *, company_id, query):
        return SimpleNamespace(
            resolution=SimpleNamespace(
                resolved_department_id="dept_power",
                resolved_name="半导体事业部",
                query=query,
                normalized_query="半导体事业部",
                resolved_type="department",
                resolved_id="dept_power",
                confidence=0.96,
                reason="name_exact",
                needs_clarification=False,
                candidates=(),
            ),
            items=({"name": "戴留兴"},),
            metadata={
                "resolved_department_name": "半导体事业部",
                "leader_items": [{"name": "戴留兴", "title": "总经理", "open_id": "ou_max"}],
            },
        )

    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers.resolve_department_members", fake_resolve_department_members)

    result = run_runtime_v5(
        context=_context("半导体事业部的负责人是谁"),
        providers={"people": FeishuPeopleProvider(db=None)},
    )

    assert result.composed.answer == "半导体事业部的负责人是 戴留兴。"
    assert "展示口径" not in result.composed.answer


def test_runtime_v5_new_question_interrupts_stale_waiting_input_action() -> None:
    class PeopleProvider:
        source = "people"
        _OPERATIONS = {"get_org_snapshot": ("people.get_org_snapshot", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            assert request.operation == "get_org_snapshot"
            return ProviderResult(
                source="people",
                status="success",
                result_type="organization_snapshot",
                count=47,
                items=({"name": "王悦"},),
                metadata={"entity_domain": "People"},
                answer="47人。",
            )

    runtime_state = {
        "task_id": "task_waiting",
        "status": "waiting",
        "intent": "message_send",
        "strategy": "message_send",
        "actions": [
            {
                "action_id": "action_waiting",
                "task_id": "task_waiting",
                "status": "waiting_input",
                "intent": "message_send",
                "strategy": "message_send",
                "message": "发消息",
                "sources": ["im"],
                "confirmation_token": "action_waiting",
                "created_at": "",
                "updated_at": "",
                "error": "",
                "metadata": {
                    "pending_action": {
                        "intent": "message_send",
                        "strategy": "message_send",
                        "message": "发消息",
                        "sources": ["im"],
                        "missing_params": ["target_type"],
                    },
                    "missing_params": ["target_type"],
                },
            }
        ],
        "metadata": {"storage": "session"},
    }

    result = run_runtime_v5(
        context=_context(
            "公司有多少人",
            chat_id="chat_interrupt_waiting_input",
            session_context={"runtime_v5_state": runtime_state},
        ),
        providers={"people": PeopleProvider()},
    )

    assert result.intent.intent == "organization_snapshot"
    assert result.composed.result_context is not None
    assert result.composed.result_context.result_type == "organization_snapshot"
    assert "要发给谁" not in result.composed.answer


def test_runtime_v5_action_receipt_does_not_hijack_new_department_relation_query() -> None:
    class PeopleProvider:
        source = "people"
        _OPERATIONS = {"list_department_members": ("people.list_department_members", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            assert request.operation == "list_department_members"
            assert request.params["keyword"] == "半导体事业部"
            return ProviderResult(
                source="people",
                status="success",
                result_type="department_members",
                count=3,
                items=({"name": "产品部"}, {"name": "销售部"}, {"name": "项目部"}),
                metadata={
                    "entity_domain": "People",
                    "organization_relation": "children",
                    "direct_child_count": 3,
                },
                answer="半导体事业部下面有 3 个直属子部门：产品部、销售部、项目部。",
            )

    action_receipt = ResultContext(
        result_type="runtime_action",
        count=1,
        items=(
            {
                "source": "runtime",
                "operation": "message_send",
                "status": "success",
                "summary": "动作已完成，结果类型：message_send，数量：1。",
            },
        ),
        metadata={
            "context_kind": "action_receipt",
            "question_type": "action",
            "operation": "message_send",
            "item_count": 1,
        },
        answer="动作已完成，结果类型：message_send，数量：1。",
    )

    result = run_runtime_v5(
        context=_context(
            "半导体事业部下面有几个部门",
            chat_id="chat_action_receipt_then_department_relation",
            result_context=action_receipt,
        ),
        providers={"people": PeopleProvider()},
    )

    assert result.intent.intent == "department_members"
    assert result.intent.entities["organization_relation"] == "children"
    assert result.execution is not None
    assert result.execution.status == "success"
    assert result.composed.answer == "半导体事业部下面有 3 个直属子部门：产品部、销售部、项目部。"
    assert result.composed.result_context is not None
    assert result.composed.result_context.result_type == "department_members"


def test_response_orchestrator_keeps_single_person_multi_field_answer() -> None:
    context = _context("李慧玲是什么岗位，她的领导是谁")
    command_plan = build_command_plan(context=context)
    result_context = ResultContext(
        result_type="people_search",
        count=1,
        items=(
            {
                "name": "李慧玲",
                "title": "高级人事专员",
                "leader": "400704",
                "leader_name_is_identifier": True,
                "leader_title": "人事经理",
                "leader_department": "人事组",
            },
        ),
        metadata={
            "entity_domain": "People",
            "people_query_field": "leader",
            "people_query_fields": ("title", "leader"),
            "domain_query": {"fields": ["title", "leader"]},
        },
        answer="李慧玲的职位是高级人事专员，直属上级在通讯录里的显示名是 400704（人事经理，人事组），当前没有可确认的中文姓名。",
    )
    execution = ExecutionResult(
        strategy="people_lookup",
        status="success",
        provider_results=(ProviderResult(source="people", status="success", result_type="people_search", count=1, answer=result_context.answer),),
        result_context=result_context,
    )

    composed = compose_answer(
        context=context,
        intent=command_plan.intent_result,
        permission=PermissionDecision(allowed=True),
        execution=execution,
    )

    assert composed.answer == result_context.answer


def test_people_provider_contract_applies_domain_query_filters_to_items_and_count() -> None:
    items = (
        {"name": "张三", "gender_normalized": "male", "gender_source": "source", "mobile": "1"},
        {"name": "李四", "gender_normalized": "female", "gender_source": "source", "mobile": "2"},
        {"name": "王五", "gender_normalized": "", "gender_source": "", "mobile": ""},
    )

    filtered, metadata = _apply_people_domain_filters(
        items,
        question="公司有多少男生，只回答数字",
        domain_query={"filters": {"gender": "male"}},
    )

    assert len(filtered) == 1
    assert filtered[0]["name"] == "张三"
    assert metadata["people_filter"] == {"filter": "gender", "value": "male"}
    assert metadata["unknown_gender_count"] == 1


def test_response_orchestrator_summarizes_department_count_without_output_contract() -> None:
    result_context = ResultContext(
        result_type="department_members",
        count=1,
        items=({"name": "汤冠男", "title": "部门高级经理", "mobile": "+8618862102927"},),
        metadata={"entity_domain": "People"},
        answer="「商务部多少人」我查到了 1 人：\n1. 汤冠男（部门高级经理，手机：+8618862102927）",
    )
    execution = ExecutionResult(
        strategy="department_members",
        status="success",
        provider_results=(
            ProviderResult(
                source="people",
                status="success",
                result_type="department_members",
                count=1,
                answer=result_context.answer,
            ),
        ),
        result_context=result_context,
    )

    composed = compose_answer(
        context=_context("商务部多少人"),
        intent=IntentResult(question_type="query", intent="department_members", data_scope="department", confidence=0.9),
        permission=PermissionDecision(allowed=True),
        execution=execution,
    )

    assert composed.answer == "1人。"


def test_response_orchestrator_uses_department_display_count_basis_for_counts() -> None:
    context = _context("半导体事业部有多少人")
    command_plan = build_command_plan(context=context)
    result_context = ResultContext(
        result_type="department_members",
        count=7,
        items=tuple({"name": name} for name in ("戴留兴", "缪瀛", "余莲莲", "张盛", "卢敏阳", "张瑞云", "王悦")),
        metadata={
            "entity_domain": "People",
            "organization_resolution": {"query": "半导体事业部", "resolved_name": "半导体事业部"},
            "display_member_count": 9,
            "unique_member_count": 7,
            "count_basis": "direct_members_plus_child_department_member_counts",
        },
        answer="半导体事业部目前 7 人。",
    )
    execution = ExecutionResult(
        strategy="department_members",
        status="success",
        provider_results=(ProviderResult(source="people", status="success", result_type="department_members", count=7, answer=result_context.answer),),
        result_context=result_context,
    )

    composed = compose_answer(
        context=context,
        intent=command_plan.intent_result,
        permission=PermissionDecision(allowed=True),
        execution=execution,
    )

    assert composed.answer == "按组织架构展示口径是 9 人；去重后是 7 位同事。"


def test_response_orchestrator_department_list_explains_count_basis_naturally() -> None:
    context = _context("半导体事业部有多少人，分别叫什么")
    command_plan = build_command_plan(context=context)
    result_context = ResultContext(
        result_type="department_members",
        count=7,
        items=tuple({"name": name} for name in ("戴留兴", "缪瀛", "余莲莲", "张盛", "卢敏阳", "张瑞云", "王悦")),
        metadata={
            "entity_domain": "People",
            "organization_resolution": {"query": "半导体事业部", "resolved_name": "半导体事业部"},
            "display_member_count": 9,
            "unique_member_count": 7,
            "count_basis": "direct_members_plus_child_department_member_counts",
        },
        answer="半导体事业部目前 7 人。",
    )
    execution = ExecutionResult(
        strategy="department_members",
        status="success",
        provider_results=(ProviderResult(source="people", status="success", result_type="department_members", count=7, answer=result_context.answer),),
        result_context=result_context,
    )

    composed = compose_answer(
        context=context,
        intent=command_plan.intent_result,
        permission=PermissionDecision(allowed=True),
        execution=execution,
    )

    assert composed.answer == "半导体事业部按组织架构展示口径是 9 人，去重后 7 位同事：戴留兴、缪瀛、余莲莲、张盛、卢敏阳、张瑞云、王悦。"


def test_runtime_v5_people_list_followup_uses_conversation_first_sidepanel() -> None:
    calls: list[str] = []

    class PeopleProvider:
        source = "people"
        _OPERATIONS = {"get_org_snapshot": ("feishu_contact_organization_snapshot", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            calls.append(request.operation)
            items = tuple({"name": f"同事{i}", "title": "工程师", "department": "工程部"} for i in range(24))
            return ProviderResult(
                source="people",
                status="success",
                result_type="organization_snapshot",
                count=24,
                items=items,
                metadata={"entity_domain": "People", "people_filter": {"filter": "gender", "value": "male"}, "field_projection": "name_only"},
                answer="\n".join(f"{i}. 同事{i}" for i in range(1, 25)),
            )

    result_context = ResultContext(
        result_type="organization_snapshot",
        count=24,
        items=({"name": "张三", "gender_normalized": "male"},),
        metadata={"entity_domain": "People", "people_filter": {"filter": "gender", "value": "male"}, "field_projection": "count_only"},
        answer="24",
    )

    result = run_runtime_v5(
        context=_context("全部展示出来", result_context=result_context),
        providers={"people": PeopleProvider()},
    )

    assert calls == ["get_org_snapshot"]
    assert result.intent.intent == "organization_snapshot"
    assert result.intent.entities["domain_query"]["filters"] == {"gender": "male"}
    assert result.composed.answer == "我把这 24 位男性员工整理好了，打开侧边栏可以看完整名单。"
    runtime_result = result.composed.metadata["runtime_result"]
    assert runtime_result["target_ui"] == "card"
    assert [action["action"] for action in runtime_result["actions"]] == ["open_sidepanel"]


def test_runtime_v5_people_count_with_previous_result_still_uses_conversation_first() -> None:
    calls: list[dict] = []

    class PeopleProvider:
        source = "people"
        _OPERATIONS = {"get_org_snapshot": ("feishu_contact_organization_snapshot", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            calls.append({"operation": request.operation, "filters": request.intent.entities["domain_query"]["filters"]})
            items = tuple({"name": f"男同事{i}", "gender_normalized": "male", "gender_source": "source"} for i in range(24))
            return ProviderResult(
                source="people",
                status="success",
                result_type="organization_snapshot",
                count=24,
                items=items,
                metadata={"entity_domain": "People", "people_filter": {"filter": "gender", "value": "male"}, "field_projection": "count_only"},
                answer="公司通讯录里明确标注为男性的员工有 24 位。",
            )

    previous = ResultContext(
        result_type="organization_snapshot",
        count=47,
        items=({"name": "张三"}, {"name": "李四"}),
        metadata={"entity_domain": "People", "field_projection": "count_only"},
        answer="47人。",
    )

    result = run_runtime_v5(
        context=_context(
            "公司有多少男生，只回答数字。",
            result_context=previous,
            session_context={"runtime_v5_state": {"actions": [], "status": "done"}},
        ),
        providers={"people": PeopleProvider()},
    )

    assert calls == [{"operation": "get_org_snapshot", "filters": {"gender": "male"}}]
    assert result.intent.intent == "organization_snapshot"
    assert result.composed.answer == "24"


def test_runtime_v5_people_gender_count_mentions_unknown_gender_count() -> None:
    class PeopleProvider:
        source = "people"
        _OPERATIONS = {"get_org_snapshot": ("feishu_contact_organization_snapshot", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            items = tuple({"name": f"男同事{i}", "gender_normalized": "male", "gender_source": "source"} for i in range(24))
            return ProviderResult(
                source="people",
                status="success",
                result_type="organization_snapshot",
                count=24,
                items=items,
                metadata={
                    "entity_domain": "People",
                    "people_filter": {"filter": "gender", "value": "male"},
                    "field_projection": "count_only",
                    "unknown_gender_count": 12,
                },
                answer="公司通讯录里明确标注为男性的员工有 24 位。",
            )

    result = run_runtime_v5(
        context=_context("男生有多少位"),
        providers={"people": PeopleProvider()},
    )

    assert result.composed.answer == "目前能确认的男性员工是 24 位。另有 12 位没有可靠性别字段，未计入。"


def test_runtime_v5_people_name_followup_inherits_previous_requested_field() -> None:
    calls: list[dict] = []

    class PeopleProvider:
        source = "people"
        _OPERATIONS = {"search_person": ("feishu_contact_user_search", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            calls.append(
                {
                    "operation": request.operation,
                    "keyword": request.params.get("keyword"),
                    "field": request.intent.entities.get("people_query_field"),
                }
            )
            return ProviderResult(
                source="people",
                status="success",
                result_type="people_search",
                count=1,
                items=({"name": "陈俊", "mobile": "+8618128123988", "title": "董事长"},),
                metadata={
                    "entity_domain": "People",
                    "people_query_field": "mobile",
                    "people_context_frame": {
                        "current_person": "陈俊",
                        "current_requested_field": "mobile",
                        "identity_resolution": "exact",
                        "visible_fields": ("mobile", "title"),
                    },
                },
                answer="陈俊的手机号是 +8618128123988。",
            )

    previous = ResultContext(
        result_type="people_search",
        count=1,
        items=({"name": "王云飞", "mobile": "+8618351080012"},),
        metadata={
            "entity_domain": "People",
            "people_query_field": "mobile",
            "people_context_frame": {
                "current_person": "王云飞",
                "current_requested_field": "mobile",
                "identity_resolution": "exact",
                "visible_fields": ("mobile",),
            },
        },
        answer="王云飞的手机号是 +8618351080012。",
    )

    result = run_runtime_v5(
        context=_context("那陈俊呢", result_context=previous),
        providers={"people": PeopleProvider()},
    )

    assert calls == [{"operation": "search_person", "keyword": "陈俊", "field": "mobile"}]
    assert result.intent.intent == "people_lookup"
    assert result.composed.result_context is not None
    assert result.composed.result_context.metadata["people_context_frame"]["current_person"] == "陈俊"


def test_runtime_v5_people_pronoun_followup_uses_active_object() -> None:
    calls: list[dict] = []

    class PeopleProvider:
        source = "people"
        _OPERATIONS = {"search_person": ("feishu_contact_user_search", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            calls.append(
                {
                    "operation": request.operation,
                    "keyword": request.params.get("keyword"),
                    "field": request.intent.entities.get("people_query_field"),
                }
            )
            return ProviderResult(
                source="people",
                status="success",
                result_type="people_search",
                count=1,
                items=({"name": "陈俊", "mobile": "+8618128123988", "title": "董事长"},),
                metadata={"entity_domain": "People", "people_query_field": "mobile"},
                answer="陈俊的手机号是 +8618128123988。",
            )

    previous = ResultContext(
        result_type="people_search",
        count=1,
        items=({"name": "陈俊", "mobile": "+8618128123988", "title": "董事长"},),
        metadata={
            "entity_domain": "People",
            "people_query_field": "mobile",
            "people_context_frame": {
                "current_person": "陈俊",
                "current_requested_field": "mobile",
                "identity_resolution": "exact",
                "visible_fields": ("mobile", "title"),
            },
        },
        answer="陈俊的手机号是 +8618128123988。",
    )

    result = run_runtime_v5(
        context=_context("他的电话是多少", result_context=previous),
        providers={"people": PeopleProvider()},
    )

    assert calls == [{"operation": "search_person", "keyword": "陈俊", "field": "mobile"}]
    assert result.intent.intent == "people_lookup"


def test_feishu_answer_rewrite_respects_conversation_output_contract(monkeypatch) -> None:
    monkeypatch.setattr(bot_runtime.settings, "bot_llm_answer_rewrite_enabled", True)

    for contract in (
        {"mode": "numeric_only", "surface": "text"},
        {"mode": "sidepanel", "surface": "sidepanel"},
    ):
        envelope = SimpleNamespace(
            intent=SimpleNamespace(
                intent="organization_snapshot",
                data_scope="organization",
                entities={"command_frame": {"params": {"output_contract": contract}}},
            ),
            composed=SimpleNamespace(
                result_context=SimpleNamespace(result_type="organization_snapshot"),
                metadata={},
            ),
        )

        assert bot_runtime._runtime_v5_answer_rewrite_allowed(
            envelope=envelope,
            answer="24",
            question="公司有多少男生，只回答数字",
        ) is False


def test_runtime_v5_foundation_domain_questions_route_before_smalltalk(monkeypatch) -> None:

    cases = (
        ("戴留兴是谁", "people_lookup", ("people",), "person"),
        ("查一下张三的邮箱", "people_lookup", ("people",), "person"),
        ("我有多少封邮件", "mail_query", ("mail",), "self"),
        ("最近邮箱里有哪些邮件", "mail_query", ("mail",), "self"),
        ("我现在有多少群", "chat_search", ("im",), "self"),
        ("我有哪些群聊", "chat_search", ("im",), "self"),
    )
    for question, expected_intent, expected_sources, expected_scope in cases:
        intent = recognize_intent(question, _context(question))
        plan = plan_task(intent)

        assert intent.intent == expected_intent
        assert intent.data_scope == expected_scope
        assert plan.sources == expected_sources


def test_runtime_v5_people_provider_ignores_empty_snapshot_cache(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.runtime_v5.feishu_resource_providers.load_people_snapshot",
        lambda company_id: {"users": [], "departments": []},
    )
    saved: list[dict] = []
    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers.save_people_snapshot", lambda company_id, payload: saved.append(payload))

    class Provider(FeishuPeopleProvider):
        def _execute_tool(
            self,
            request: ProviderRequest,
            *,
            tool_name: str,
            params: dict | None = None,
            confirm_write: bool = False,
        ):
            return SimpleNamespace(
                status=ToolExecutionStatus.SUCCESS,
                error="",
                answer="",
                structured_result={
                    "response_payload": {
                        "users": [{"name": "张三", "gender": "male"}, {"name": "李四", "gender": "female"}],
                        "departments": [{"name": "研发部"}],
                    }
                },
            )

    context = _context("公司有多少个人")
    result = Provider(db=None).execute(
        ProviderRequest(
            source="people",
            operation="get_org_snapshot",
            intent=IntentResult(question_type="query", intent="organization_snapshot", data_scope="organization", entities={"view": "people_aggregate"}),
            planner=_command_plan("organization_snapshot", sources=("people",)),
            context=context,
            execution_identity="bot",
        )
    )

    assert result.status == "success"
    assert result.count == 2
    assert result.metadata["cache_hit"] is False
    assert saved
    assert result.answer == "公司当前可读通讯录里是 2 人。"


def test_runtime_v5_people_resolver_reuses_normalized_contact_entities() -> None:
    items = (
        {
            "name": "张三",
            "email": "zhangsan@example.com",
            "mobile": "13800000000",
            "title": "销售经理",
            "department": "销售部",
            "open_id": "ou_zhang",
        },
        {
            "name": "李四",
            "email": "lisi@example.com",
            "title": "测试工程师",
            "department": "测试部",
            "open_id": "ou_li",
        },
    )

    by_name = resolve_people_from_items("张三", items)
    by_email = resolve_people_from_items("lisi@example.com", items)
    sales_members = filter_people_by_department(items, "销售部")
    engineers = filter_people_by_title(items, "工程师")

    assert by_name.count == 1
    assert by_name.items[0]["open_id"] == "ou_zhang"
    assert by_email.match_type == "exact_identity"
    assert by_email.items[0]["name"] == "李四"
    assert [item["name"] for item in sales_members] == ["张三"]
    assert [item["name"] for item in engineers] == ["李四"]


def test_runtime_v5_people_provider_ignores_legacy_partial_snapshot_cache(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.runtime_v5.feishu_resource_providers.load_people_snapshot",
        lambda company_id: {"users": [{"name": "旧缓存"}], "departments": [{"name": "旧部门"}]},
    )
    saved: list[dict] = []
    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers.save_people_snapshot", lambda company_id, payload: saved.append(payload))

    class Provider(FeishuPeopleProvider):
        def _execute_tool(
            self,
            request: ProviderRequest,
            *,
            tool_name: str,
            params: dict | None = None,
            confirm_write: bool = False,
        ):
            return SimpleNamespace(
                status=ToolExecutionStatus.SUCCESS,
                error="",
                answer="",
                structured_result={
                    "response_payload": {
                        "_runtime_v5_snapshot_version": 3,
                        "users": [{"name": "新数据"}],
                        "departments": [{"name": "新部门"}],
                    }
                },
            )

    result = Provider(db=None).execute(
        ProviderRequest(
            source="people",
            operation="get_org_snapshot",
            intent=IntentResult(question_type="query", intent="organization_snapshot", data_scope="organization", entities={"view": "people_aggregate"}),
            planner=_command_plan("organization_snapshot", sources=("people",)),
            context=_context("公司有多少个人"),
            execution_identity="bot",
        )
    )

    assert result.status == "success"
    assert result.items[0]["name"] == "新数据"
    assert result.metadata["cache_hit"] is False
    assert saved and saved[0]["_runtime_v5_snapshot_version"] == 3


def test_runtime_v5_people_aggregate_uses_department_reported_member_count(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.runtime_v5.feishu_resource_providers.load_people_snapshot",
        lambda company_id: {},
    )
    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers.save_people_snapshot", lambda company_id, payload: None)

    class Provider(FeishuPeopleProvider):
        def _execute_tool(
            self,
            request: ProviderRequest,
            *,
            tool_name: str,
            params: dict | None = None,
            confirm_write: bool = False,
        ):
            return SimpleNamespace(
                status=ToolExecutionStatus.SUCCESS,
                error="",
                answer="",
                structured_result={
                    "response_payload": {
                        "users": [{"name": "张三"}, {"name": "李四"}],
                        "departments": [
                            {"name": "研发部", "parent_department_id": "0", "primary_member_count": 5},
                            {"name": "运营部", "parent_department_id": "0", "primary_member_count": 2},
                            {"name": "研发一组", "parent_department_id": "dep_1", "primary_member_count": 5},
                        ],
                    }
                },
            )

    result = Provider(db=None).execute(
        ProviderRequest(
            source="people",
            operation="get_org_snapshot",
            intent=IntentResult(question_type="query", intent="organization_snapshot", data_scope="organization", entities={"view": "people_aggregate"}),
            planner=_command_plan("organization_snapshot", sources=("people",)),
            context=_context("公司有多少个人"),
            execution_identity="bot",
        )
    )

    assert result.status == "success"
    assert result.metadata["reported_member_count"] == 7
    assert result.metadata["visible_user_count"] == 2
    assert result.answer == "公司当前可读通讯录里是 7 人。"


def test_runtime_v5_people_aggregate_lists_requested_gender(monkeypatch) -> None:
    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers.load_people_snapshot", lambda company_id: {})
    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers.save_people_snapshot", lambda company_id, payload: None)

    class Provider(FeishuPeopleProvider):
        def _execute_tool(
            self,
            request: ProviderRequest,
            *,
            tool_name: str,
            params: dict | None = None,
            confirm_write: bool = False,
        ):
            return SimpleNamespace(
                status=ToolExecutionStatus.SUCCESS,
                error="",
                answer="",
                structured_result={
                    "response_payload": {
                        "_runtime_v5_snapshot_version": 3,
                        "users": [
                            {"name": "张三", "gender": "male", "title": "工程师"},
                            {"name": "李四", "gender": "female", "title": "财务"},
                            {"name": "王五", "gender": "female", "title": "人事"},
                        ],
                        "departments": [{"name": "职能中心", "parent_department_id": "0", "primary_member_count": 3}],
                    }
                },
            )

    result = Provider(db=None).execute(
        ProviderRequest(
            source="people",
            operation="get_org_snapshot",
            intent=IntentResult(question_type="query", intent="organization_snapshot", data_scope="organization", entities={"view": "people_aggregate"}),
            planner=_command_plan("organization_snapshot", sources=("people",)),
            context=_context("公司有多少个女生，分别是谁"),
            execution_identity="bot",
        )
    )

    assert "公司通讯录里明确标注为女性的员工有 2 位" in result.answer
    assert "李四" in result.answer
    assert "王五" in result.answer
    assert "张三" not in result.answer


def test_runtime_v5_people_aggregate_counts_requested_title(monkeypatch) -> None:
    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers.load_people_snapshot", lambda company_id: {})
    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers.save_people_snapshot", lambda company_id, payload: None)

    class Provider(FeishuPeopleProvider):
        def _execute_tool(
            self,
            request: ProviderRequest,
            *,
            tool_name: str,
            params: dict | None = None,
            confirm_write: bool = False,
        ):
            return SimpleNamespace(
                status=ToolExecutionStatus.SUCCESS,
                error="",
                answer="",
                structured_result={
                    "response_payload": {
                        "_runtime_v5_snapshot_version": 3,
                        "users": [
                            {"name": "张三", "title": "后端工程师"},
                            {"name": "李四", "title": "财务"},
                            {"name": "王五", "title": "测试工程师"},
                        ],
                        "departments": [{"name": "研发部", "parent_department_id": "0", "primary_member_count": 3}],
                    }
                },
            )

    intent = recognize_intent("公司有多少个工程师，分别是谁", _context("公司有多少个工程师，分别是谁"))
    result = Provider(db=None).execute(
        ProviderRequest(
            source="people",
            operation="get_org_snapshot",
            intent=intent,
            planner=_command_plan("organization_snapshot", sources=("people",)),
            context=_context("公司有多少个工程师，分别是谁"),
            execution_identity="bot",
        )
    )

    assert intent.intent == "organization_snapshot"
    assert result.status == "success"
    assert "公司里岗位/职位包含「工程师」的同事有 2 人" in result.answer
    assert "张三" in result.answer
    assert "王五" in result.answer
    assert "李四" not in result.answer


def test_runtime_v5_people_count_only_does_not_route_to_department_members() -> None:
    intent = recognize_intent("我们多少个人，你只需要回答我多少人，没必要告诉我多少部门。", _context("我们多少个人，你只需要回答我多少人，没必要告诉我多少部门。"))

    assert intent.intent == "organization_snapshot"
    assert intent.entities["people_query_mode"] == "count_only"
    assert intent.entities["foundation_route"] == "people.aggregate"


def test_runtime_v5_people_gender_count_does_not_preview_names(monkeypatch) -> None:
    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers.load_people_snapshot", lambda company_id: {})
    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers.save_people_snapshot", lambda company_id, payload: None)

    class Provider(FeishuPeopleProvider):
        def _execute_tool(
            self,
            request: ProviderRequest,
            *,
            tool_name: str,
            params: dict | None = None,
            confirm_write: bool = False,
        ):
            return SimpleNamespace(
                status=ToolExecutionStatus.SUCCESS,
                error="",
                answer="",
                structured_result={
                    "response_payload": {
                        "_runtime_v5_snapshot_version": 3,
                        "users": [
                            {"name": "李四", "gender": "female", "job_title": "财务"},
                            {"name": "王五", "gender": "female", "job_title": "人事"},
                        ],
                        "departments": [{"name": "职能中心", "parent_department_id": "0", "primary_member_count": 2}],
                    }
                },
            )

    intent = recognize_intent("公司有多少个女生", _context("公司有多少个女生"))
    result = Provider(db=None).execute(
        ProviderRequest(
            source="people",
            operation="get_org_snapshot",
            intent=intent,
            planner=_command_plan("organization_snapshot", sources=("people",)),
            context=_context("公司有多少个女生"),
            execution_identity="bot",
        )
    )

    assert result.answer == "公司通讯录里明确标注为女性的员工有 2 位。"
    assert "李四" not in result.answer
    assert "全部列出" not in result.answer


def test_runtime_v5_people_facts_keep_source_provenance_without_code_corrections() -> None:
    tang = normalize_people_item({"name": "汤冠男", "gender": 0, "job_title": "部门高级经理"})
    wang = normalize_people_item({"name": "王悦", "gender": 2, "job_title": "行政专员"})

    assert tang["gender_normalized"] == ""
    assert tang["gender_source"] == ""
    assert wang["title"] == "行政专员"
    assert wang["title_source"] == "source"


def test_runtime_v5_people_lookup_answers_requested_field_naturally(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.runtime_v5.feishu_resource_providers.load_people_snapshot",
        lambda company_id: {
            "_runtime_v5_snapshot_version": 3,
            "users": [
                {"name": "王悦", "gender": 2, "job_title": "行政专员", "department_names": ["行政部"], "mobile": "+8618061834925"},
                {"name": "汤冠男", "gender": 0, "job_title": "部门高级经理", "department_names": ["商务组"]},
            ],
        },
    )

    provider = FeishuPeopleProvider(db=None)
    wang_result = provider.execute(
        ProviderRequest(
            source="people",
            operation="search_person",
            intent=IntentResult(question_type="query", intent="people_lookup", data_scope="person", entities={"keyword": "王悦"}, canonical_question="王悦是什么岗位"),
            planner=_command_plan("people_lookup", sources=("people",)),
            context=_context("王悦是什么岗位"),
            execution_identity="bot",
            params={"keyword": "王悦"},
        )
    )
    tang_result = provider.execute(
        ProviderRequest(
            source="people",
            operation="search_person",
            intent=IntentResult(question_type="query", intent="people_lookup", data_scope="person", entities={"keyword": "汤冠男"}, canonical_question="汤冠男是男还是女"),
            planner=_command_plan("people_lookup", sources=("people",)),
            context=_context("汤冠男是男还是女"),
            execution_identity="bot",
            params={"keyword": "汤冠男"},
        )
    )

    assert wang_result.answer == "王悦是行政部的行政专员。"
    assert tang_result.answer == "我查到了汤冠男，但当前可读通讯录没有提供可靠性别字段，我不会根据名字判断，也不会把这位同事纳入明确男性或女性名单。"


def test_runtime_v5_people_lookup_enriches_missing_requested_field_from_snapshot(monkeypatch) -> None:
    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers.load_people_snapshot", lambda company_id: {})
    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers.save_people_snapshot", lambda company_id, payload: None)

    class Provider(FeishuPeopleProvider):
        def _execute_tool(
            self,
            request: ProviderRequest,
            *,
            tool_name: str,
            params: dict | None = None,
            confirm_write: bool = False,
        ):
            payload = (
                {"users": [{"name": "王云飞", "job_title": "部门高级经理", "department_names": ["商务组"]}]}
                if tool_name == "feishu_contact_user_search"
                else {
                    "_runtime_v5_snapshot_version": 3,
                    "users": [{"name": "王云飞", "job_title": "部门高级经理", "department_names": ["商务组"], "mobile": "+8613800000000"}],
                    "departments": [{"name": "商务组", "parent_department_id": "0", "primary_member_count": 1}],
                }
            )
            return SimpleNamespace(status=ToolExecutionStatus.SUCCESS, error="", answer="", structured_result={"response_payload": payload})

    result = Provider(db=None).execute(
        ProviderRequest(
            source="people",
            operation="search_person",
            intent=IntentResult(
                question_type="query",
                intent="people_lookup",
                data_scope="person",
                entities={"keyword": "王云飞", "people_query_field": "mobile"},
                canonical_question="王云飞的手机号",
            ),
            planner=_command_plan("people_lookup", sources=("people",)),
            context=_context("王云飞的手机号"),
            execution_identity="bot",
            params={"keyword": "王云飞"},
        )
    )

    assert result.answer == "王云飞的手机号是 +8613800000000。"


def test_runtime_v5_people_lookup_resolves_leader_name_from_organization_foundation(monkeypatch) -> None:
    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers.load_people_snapshot", lambda company_id: {})

    class _ScalarResult:
        def __init__(self, items):
            self._items = items

        def all(self):
            return self._items

    class _FoundationDb:
        def scalars(self, _statement):
            return _ScalarResult(
                [
                    SimpleNamespace(
                        open_id="ou_li",
                        source_user_id="u_li",
                        name="李慧玲",
                        email="",
                        mobile="",
                        job_title="高级人事专员",
                        metadata_json={"leader_user_id": "ou_du"},
                    ),
                    SimpleNamespace(
                        open_id="ou_du",
                        source_user_id="u_du",
                        name="杜玉娟",
                        email="",
                        mobile="",
                        job_title="行政主管",
                        metadata_json={},
                    ),
                ]
            )

    class Provider(FeishuPeopleProvider):
        def _execute_tool(
            self,
            request: ProviderRequest,
            *,
            tool_name: str,
            params: dict | None = None,
            confirm_write: bool = False,
        ):
            return SimpleNamespace(
                status=ToolExecutionStatus.SUCCESS,
                error="",
                answer="",
                structured_result={"response_payload": {"users": [{"open_id": "ou_li", "name": "李慧玲", "leader_user_id": "ou_du"}]}},
            )

    result = Provider(db=_FoundationDb()).execute(
        ProviderRequest(
            source="people",
            operation="search_person",
            intent=IntentResult(
                question_type="query",
                intent="people_lookup",
                data_scope="person",
                entities={"keyword": "李慧玲", "people_query_field": "leader"},
                canonical_question="李慧玲的直属上级",
            ),
            planner=_command_plan("people_lookup", sources=("people",)),
            context=_context("李慧玲的直属上级是谁"),
            execution_identity="bot",
            params={"keyword": "李慧玲"},
        )
    )

    assert result.answer == "李慧玲的直属上级是杜玉娟。"
    assert result.items[0]["leader"] == "杜玉娟"


def test_runtime_v5_people_lookup_marks_identifier_leader_name_as_uncertain(monkeypatch) -> None:
    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers.load_people_snapshot", lambda company_id: {})

    class _ScalarResult:
        def __init__(self, items):
            self._items = items

        def all(self):
            return self._items

    class _FoundationDb:
        def scalars(self, _statement):
            return _ScalarResult(
                [
                    SimpleNamespace(
                        open_id="ou_li",
                        source_user_id="u_li",
                        name="李慧玲",
                        email="",
                        mobile="",
                        job_title="高级人事专员",
                        metadata_json={"leader_user_id": "ou_400704"},
                    ),
                    SimpleNamespace(
                        open_id="ou_400704",
                        source_user_id="u_400704",
                        name="400704",
                        email="",
                        mobile="",
                        job_title="人事经理",
                        metadata_json={"department_names": ["人事组"]},
                    ),
                ]
            )

    class Provider(FeishuPeopleProvider):
        def _execute_tool(
            self,
            request: ProviderRequest,
            *,
            tool_name: str,
            params: dict | None = None,
            confirm_write: bool = False,
        ):
            return SimpleNamespace(
                status=ToolExecutionStatus.SUCCESS,
                error="",
                answer="",
                structured_result={"response_payload": {"users": [{"open_id": "ou_li", "name": "李慧玲", "leader_user_id": "ou_400704"}]}},
            )

    result = Provider(db=_FoundationDb()).execute(
        ProviderRequest(
            source="people",
            operation="search_person",
            intent=IntentResult(
                question_type="query",
                intent="people_lookup",
                data_scope="person",
                entities={"keyword": "李慧玲", "people_query_field": "leader"},
                canonical_question="李慧玲的直属上级",
            ),
            planner=_command_plan("people_lookup", sources=("people",)),
            context=_context("李慧玲的直属上级是谁"),
            execution_identity="bot",
            params={"keyword": "李慧玲"},
        )
    )

    assert "显示名是 400704" in result.answer
    assert "当前没有可确认的中文姓名" in result.answer


def test_runtime_v5_people_typo_match_asks_confirmation_without_answering_field(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.runtime_v5.feishu_resource_providers.load_people_snapshot",
        lambda company_id: {
            "_runtime_v5_snapshot_version": 3,
            "users": [{"name": "王云飞", "job_title": "IT专员", "mobile": "+8618351080012"}],
        },
    )

    result = FeishuPeopleProvider(db=None).execute(
        ProviderRequest(
            source="people",
            operation="search_person",
            intent=IntentResult(
                question_type="query",
                intent="people_lookup",
                data_scope="person",
                entities={"keyword": "五云飞", "people_query_field": "mobile"},
                canonical_question="五云飞的手机号",
            ),
            planner=_command_plan("people_lookup", sources=("people",)),
            context=_context("五云飞的手机号"),
            execution_identity="bot",
            params={"keyword": "五云飞"},
        )
    )

    assert result.metadata["match_type"] == "near_identity_candidate"
    assert result.metadata["needs_confirmation"] is True
    assert result.answer == "我没有精确找到「五云飞」，通讯录里相近的是：王云飞。你是不是指其中一位？"
    assert "+8618351080012" not in result.answer


def test_runtime_v5_people_contextual_followup_inherits_previous_field() -> None:
    result_context = ResultContext(
        result_type="people_search",
        count=1,
        items=({"name": "汤冠男", "title": "部门高级经理"},),
        metadata={"context_kind": "query_result", "entity_domain": "people", "people_query_field": "title"},
        answer="汤冠男是商务组的部门高级经理。",
    )

    intent = recognize_intent("那陈俊呢", _context("那陈俊呢", result_context=result_context))

    assert intent.intent == "people_lookup"
    assert intent.entities["keyword"] == "陈俊"
    assert intent.entities["people_query_field"] == "title"
    assert intent.canonical_question == "陈俊的岗位"


def test_runtime_v5_people_contextual_field_switch_uses_previous_person() -> None:
    result_context = ResultContext(
        result_type="people_search",
        count=1,
        items=({"name": "陈俊", "title": "董事长"},),
        metadata={"context_kind": "query_result", "entity_domain": "people", "people_query_field": "profile"},
        answer="我在通讯录里找到陈俊。",
    )

    intent = recognize_intent("我问的是他的职位", _context("我问的是他的职位", result_context=result_context))

    assert intent.intent == "people_lookup"
    assert intent.entities["keyword"] == "陈俊"
    assert intent.entities["people_query_field"] == "title"
    assert intent.canonical_question == "陈俊的岗位"


def test_runtime_v5_people_contextual_field_switch_uses_previous_person_for_leader() -> None:
    result_context = ResultContext(
        result_type="people_search",
        count=1,
        items=({"name": "李慧玲", "title": "高级人事专员"},),
        metadata={"context_kind": "query_result", "entity_domain": "people", "people_query_field": "title"},
        answer="李慧玲是高级人事专员。",
    )

    intent = recognize_intent("她的领导是哪位", _context("她的领导是哪位", result_context=result_context))

    assert intent.intent == "people_lookup"
    assert intent.entities["keyword"] == "李慧玲"
    assert intent.entities["people_query_field"] == "leader"
    assert intent.entities["domain_query"]["fields"] == ["leader"]
    assert intent.canonical_question == "李慧玲的直属上级"


def test_runtime_v5_people_single_question_can_request_title_and_leader() -> None:
    question = "李慧玲是什么岗位，他的领导是谁"
    intent = recognize_intent(question, _context(question))

    assert intent.intent == "people_lookup"
    assert intent.entities["keyword"] == "李慧玲"
    assert intent.entities["domain_query"]["fields"] == ["title", "leader"]


def test_conversation_first_people_single_question_preserves_multiple_fields() -> None:
    question = "李慧玲是什么岗位，她的领导是谁"
    plan = build_command_plan(context=_context(question))

    assert plan.intent == "people_lookup"
    assert plan.intent_result.entities["keyword"] == "李慧玲"
    assert plan.intent_result.entities["domain_query"]["fields"] == ["title", "leader"]
    assert plan.command_frame is not None
    assert plan.command_frame.params["domain_query"]["fields"] == ["title", "leader"]


def test_runtime_v5_people_contextual_pronoun_switches_to_phone() -> None:
    result_context = ResultContext(
        result_type="people_search",
        count=1,
        items=({"name": "吴健", "gender": "male", "gender_source": "source"},),
        metadata={"context_kind": "query_result", "entity_domain": "people", "people_query_field": "gender"},
        answer="吴健是男性。",
    )

    intent = recognize_intent("他的电话是多少", _context("他的电话是多少", result_context=result_context))

    assert intent.intent == "people_lookup"
    assert intent.entities["keyword"] == "吴健"
    assert intent.entities["people_query_field"] == "mobile"
    assert intent.entities["domain_query"]["subject"] == {"type": "person", "name": "吴健"}
    assert intent.entities["domain_query"]["fields"] == ["mobile"]
    assert intent.entities["domain_query"]["output_mode"] == "answer"
    assert intent.entities["domain_query"]["context_ref"]["current_person"] == "吴健"
    assert intent.canonical_question == "吴健的手机号"


def test_runtime_v5_people_keyword_strips_context_particles() -> None:
    intent = recognize_intent("那江红燕的电话呢", _context("那江红燕的电话呢"))

    assert intent.intent == "people_lookup"
    assert intent.entities["keyword"] == "江红燕"
    assert intent.entities["people_query_field"] == "mobile"
    assert intent.entities["domain_query"]["subject"] == {"type": "person", "name": "江红燕"}
    assert intent.entities["domain_query"]["fields"] == ["mobile"]


def test_runtime_v5_people_lookup_embedded_in_non_work_sentence_wins() -> None:
    question = "你又不能帮我点外卖，那就把戴留兴的电话告诉我，我让他帮我点。"
    intent = recognize_intent(question, _context(question))

    assert intent.intent == "people_lookup"
    assert intent.entities["keyword"] == "戴留兴"
    assert intent.entities["people_query_field"] == "mobile"
    assert intent.entities["domain_query"]["subject"] == {"type": "person", "name": "戴留兴"}
    assert intent.entities["domain_query"]["fields"] == ["mobile"]
    assert intent.entities["domain_query"]["presentation_hint"] == "text"
    trace = intent.entities["command_intent_trace"]
    assert trace["source"] == "candidate_arbiter"
    assert trace["reason"] == "exact_people_field_query"
    assert trace["candidates"][0]["domain"] == "People"
    assert "Conversation" in {candidate["domain"] for candidate in trace["candidates"]}


def test_runtime_v5_people_identity_question_strips_identity_suffix() -> None:
    question = "戴留兴是谁"
    intent = recognize_intent(question, _context(question))

    assert intent.intent == "people_lookup"
    assert intent.entities["keyword"] == "戴留兴"
    assert intent.entities["domain_query"]["subject"] == {"type": "person", "name": "戴留兴"}


def test_runtime_v5_people_role_question_routes_to_title_list() -> None:
    question = "公司董事长是谁"
    intent = recognize_intent(question, _context(question))

    assert intent.intent == "organization_snapshot"
    assert intent.entities["people_query_mode"] == "title_list"
    assert intent.entities["domain_query"]["filters"] == {"query_mode": "title_list"}
    assert intent.entities["domain_query"]["subject"] == {"type": "organization"}


def test_runtime_v5_people_pronoun_followup_requires_clarification_for_multi_result() -> None:
    result_context = ResultContext(
        result_type="people_search",
        count=2,
        items=(
            {"name": "吴健", "mobile": "+8615050181517"},
            {"name": "李悦", "mobile": "+8618860935802"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="上一轮找到了 2 位。",
    )

    intent = recognize_intent("他的电话是多少", _context("他的电话是多少", result_context=result_context))

    assert intent.intent == "smalltalk"
    assert intent.missing_params == ("person",)
    assert "哪一位" in intent.entities["fallback_answer"]
    assert intent.entities["command_intent_trace"]["reason"] == "people_pronoun_with_multiple_or_empty_results"


def test_runtime_v5_people_followup_inherits_field_after_empty_lookup() -> None:
    result_context = ResultContext(
        result_type="people_search",
        count=0,
        items=(),
        metadata={"context_kind": "query_result", "entity_domain": "people", "people_query_field": "mobile"},
        answer="没有找到戴留兴的手机号。",
    )

    intent = recognize_intent("那王悦的你有吗", _context("那王悦的你有吗", result_context=result_context))

    assert intent.intent == "people_lookup"
    assert intent.entities["keyword"] == "王悦"
    assert intent.entities["people_query_field"] == "mobile"
    assert intent.entities["domain_query"]["fields"] == ["mobile"]
    assert intent.entities["domain_query"]["context_ref"]["current_requested_field"] == "mobile"


def test_runtime_v5_people_mobile_availability_query_uses_field_present_filter() -> None:
    intent = recognize_intent("你到底有谁的号码", _context("你到底有谁的号码"))

    assert intent.intent == "organization_snapshot"
    assert intent.entities["people_query_mode"] == "list"
    assert intent.entities["domain_query"]["filters"]["field_present"] == "mobile"
    assert intent.entities["domain_query"]["fields"] == ["mobile"]
    assert intent.entities["domain_query"]["output_mode"] == "list"


def test_runtime_v5_people_contact_export_request_is_list_contract() -> None:
    intent = recognize_intent("那你把公司通讯录发我下", _context("那你把公司通讯录发我下"))

    assert intent.intent == "organization_snapshot"
    assert intent.entities["view"] == "people_aggregate"
    assert intent.entities["people_query_mode"] == "list"
    assert intent.entities["domain_query"]["output_mode"] == "list"
    assert intent.entities["domain_query"]["subject"] == {"type": "organization"}


def test_runtime_v5_people_aggregate_count_answer_is_conversational(monkeypatch) -> None:
    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers.load_people_snapshot", lambda company_id: {})
    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers.save_people_snapshot", lambda company_id, payload: None)

    class Provider(FeishuPeopleProvider):
        def _execute_tool(
            self,
            request: ProviderRequest,
            *,
            tool_name: str,
            params: dict | None = None,
            confirm_write: bool = False,
        ):
            return SimpleNamespace(
                status=ToolExecutionStatus.SUCCESS,
                error="",
                answer="",
                structured_result={
                    "response_payload": {
                        "_runtime_v5_snapshot_version": 3,
                        "users": [
                            {"name": "张三", "gender": "male", "job_title": "工程师"},
                            {"name": "李四", "gender": "female", "job_title": "财务"},
                        ],
                        "departments": [{"name": "职能中心", "parent_department_id": "0", "primary_member_count": 2}],
                    }
                },
            )

    result = Provider(db=None).execute(
        ProviderRequest(
            source="people",
            operation="get_org_snapshot",
            intent=IntentResult(question_type="query", intent="organization_snapshot", data_scope="organization", entities={"view": "people_aggregate"}),
            planner=_command_plan("organization_snapshot", sources=("people",)),
            context=_context("公司有多少人"),
            execution_identity="bot",
        )
    )

    assert result.answer == "公司当前可读通讯录里是 2 人。"
    assert "部门" not in result.answer
    assert "字段可见度" not in result.answer
    assert "性别字段" not in result.answer
    assert "说明：" not in result.answer
    assert "可继续问" not in result.answer


def test_runtime_v5_people_aggregate_count_only_keeps_terse_answer(monkeypatch) -> None:
    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers.load_people_snapshot", lambda company_id: {})
    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers.save_people_snapshot", lambda company_id, payload: None)

    class Provider(FeishuPeopleProvider):
        def _execute_tool(
            self,
            request: ProviderRequest,
            *,
            tool_name: str,
            params: dict | None = None,
            confirm_write: bool = False,
        ):
            return SimpleNamespace(
                status=ToolExecutionStatus.SUCCESS,
                error="",
                answer="",
                structured_result={
                    "response_payload": {
                        "_runtime_v5_snapshot_version": 3,
                        "users": [{"name": "张三"}, {"name": "李四"}],
                        "departments": [{"name": "职能中心", "parent_department_id": "0", "primary_member_count": 2}],
                    }
                },
            )

    result = Provider(db=None).execute(
        ProviderRequest(
            source="people",
            operation="get_org_snapshot",
            intent=IntentResult(
                question_type="query",
                intent="organization_snapshot",
                data_scope="organization",
                entities={"view": "people_aggregate", "people_query_mode": "count_only"},
            ),
            planner=_command_plan("organization_snapshot", sources=("people",)),
            context=_context("公司有多少人，只回答人数"),
            execution_identity="bot",
        )
    )

    assert result.answer == "2人。"


def test_runtime_v5_people_lookup_extracts_name_from_multi_field_question(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.runtime_v5.feishu_resource_providers.load_people_snapshot",
        lambda company_id: {
            "users": [
                {
                    "name": "王庆威",
                    "job_title": "软件工程师",
                    "department": "软件组",
                    "mobile": "+8618862102927",
                }
            ]
        },
    )

    intent = recognize_intent("王庆威的职位和手机是什么", _context("王庆威的职位和手机是什么"))
    result = FeishuPeopleProvider(db=None).execute(
        ProviderRequest(
            source="people",
            operation="search_person",
            intent=intent,
            planner=_command_plan("people_search", sources=("people",)),
            context=_context("王庆威的职位和手机是什么"),
            execution_identity="bot",
        )
    )

    assert intent.intent == "people_lookup"
    assert intent.entities["keyword"] == "王庆威"
    assert intent.entities["domain_query"]["fields"] == ["title", "mobile"]
    assert intent.entities["domain_query"]["output_mode"] == "answer"
    assert "软件工程师" in result.answer
    assert "+8618862102927" in result.answer
    assert "王庆威的职位和手机是什么" not in result.answer
    assert result.metadata["result_context_presentation"] == "summary"
    assert result.metadata["identity_resolution"] == "exact"
    assert result.metadata["people_context_frame"]["current_person"] == "王庆威"
    assert result.metadata["people_context_frame"]["current_requested_field"] == "mobile"
    assert result.metadata["people_context_frame"]["current_requested_fields"] == ("title", "mobile")
    assert result.metadata["domain_query"]["fields"] == ["title", "mobile"]
    assert result.metadata["sensitive_fields_present"] == ("mobile",)


def test_runtime_v5_people_followup_filters_gender_from_previous_result() -> None:
    result_context = ResultContext(
        result_type="organization_snapshot",
        count=3,
        items=(
            {"name": "张三", "gender": "male", "title": "工程师"},
            {"name": "李四", "gender": "female", "title": "财务"},
            {"name": "王五", "gender": "male", "title": "测试"},
        ),
        metadata={"context_kind": "query_result"},
        answer="公司共有 3 人。",
    )
    followup = detect_result_followup("哪2个男的", result_context)

    answer = compose_answer(
        context=_context("哪2个男的", result_context=result_context),
        intent=IntentResult(question_type="query", intent="smalltalk", data_scope="self"),
        permission=PermissionDecision(allowed=True),
        execution=None,
        followup=followup,
    )

    assert followup.is_result_followup is True
    assert followup.followup_type == "people_filter"
    assert followup.entity_ref["field_projection"] == "detail"
    assert answer.answer == "能确认的男性员工是 2 位。 男性员工明细我放到侧边栏里，聊天里不展开长清单。"
    assert "张三" not in answer.answer
    assert "王五" not in answer.answer
    assert "李四" not in answer.answer
    assert answer.result_context is not None
    assert answer.result_context.metadata["result_context_presentation"] == "detail"


def test_runtime_v5_people_followup_filters_title_and_updates_context() -> None:
    result_context = ResultContext(
        result_type="organization_snapshot",
        count=3,
        items=(
            {"name": "张三", "gender": "male", "title": "后端工程师"},
            {"name": "李四", "gender": "female", "title": "财务"},
            {"name": "王五", "gender": "male", "title": "测试工程师"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="公司共有 3 人。",
    )
    followup = detect_result_followup("哪些是工程师", result_context)

    answer = compose_answer(
        context=_context("哪些是工程师", result_context=result_context),
        intent=IntentResult(question_type="query", intent="smalltalk", data_scope="self"),
        permission=PermissionDecision(allowed=True),
        execution=None,
        followup=followup,
    )

    assert followup.is_result_followup is True
    assert followup.followup_type == "people_filter"
    assert answer.result_context is not None
    assert answer.result_context.count == 2
    assert [item["name"] for item in answer.result_context.items] == ["张三", "王五"]
    assert answer.result_context.metadata["people_filter"] == {"filter": "title", "keyword": "工程师"}
    assert answer.result_context.metadata["result_context_presentation"] == "detail"
    assert answer.answer == "岗位/职位包含「工程师」的人员是 2 人。 岗位/职位包含「工程师」的人员明细我放到侧边栏里，聊天里不展开长清单。"
    assert "张三" not in answer.answer
    assert "王五" not in answer.answer
    assert "李四" not in answer.answer


def test_runtime_v5_result_context_expand_can_project_people_names_only() -> None:
    result_context = ResultContext(
        result_type="organization_snapshot",
        count=3,
        items=(
            {"name": "张三", "title": "工程师", "mobile": "13800000001", "open_id": "ou_1", "company_id": "co_1"},
            {"name": "李四", "title": "财务", "email": "lisi@example.com", "open_id": "ou_2", "company_id": "co_1"},
            {"name": "王五", "title": "测试", "mobile": "13800000003", "open_id": "ou_3", "company_id": "co_1"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="上一轮人员结果。",
    )
    followup = detect_result_followup("全部名字告诉我", result_context)

    answer = compose_answer(
        context=_context("全部名字告诉我", result_context=result_context),
        intent=IntentResult(question_type="query", intent="smalltalk", data_scope="self"),
        permission=PermissionDecision(allowed=True),
        execution=None,
        followup=followup,
    )

    assert followup.is_result_followup is True
    assert followup.entity_ref["result_context_operation"] == "expand"
    assert followup.entity_ref["field_projection"] == "name_only"
    assert answer.answer == "上一轮结果共有 3 人：\n1. 张三\n2. 李四\n3. 王五"
    assert "13800000001" not in answer.answer
    assert "lisi@example.com" not in answer.answer
    assert "ou_1" not in answer.answer
    assert "co_1" not in answer.answer


def test_runtime_v5_people_followup_count_only_does_not_dump_details() -> None:
    result_context = ResultContext(
        result_type="organization_snapshot",
        count=3,
        items=(
            {"name": "张三", "gender": "male", "gender_source": "source", "title": "工程师", "mobile": "13800000001", "company_id": "co_1"},
            {"name": "李四", "gender": "female", "gender_source": "source", "title": "财务", "email": "lisi@example.com", "company_id": "co_1"},
            {"name": "王五", "gender": "male", "gender_source": "source", "title": "测试", "mobile": "13800000003", "company_id": "co_1"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="公司共有 3 人。",
    )
    followup = detect_result_followup("我是问你有多少男生，不用给我详情", result_context)

    answer = compose_answer(
        context=_context("我是问你有多少男生，不用给我详情", result_context=result_context),
        intent=IntentResult(question_type="query", intent="smalltalk", data_scope="self"),
        permission=PermissionDecision(allowed=True),
        execution=None,
        followup=followup,
    )

    assert followup.is_result_followup is True
    assert followup.entity_ref["field_projection"] == "count_only"
    assert answer.answer == "能确认的男性员工是 2 位。"
    assert "张三" not in answer.answer
    assert "13800000001" not in answer.answer
    assert "co_1" not in answer.answer


def test_runtime_v5_people_followup_quantity_phrase_is_count_only() -> None:
    result_context = ResultContext(
        result_type="organization_snapshot",
        count=2,
        items=(
            {"name": "张三", "gender": "male", "gender_source": "source", "title": "工程师"},
            {"name": "王五", "gender": "male", "gender_source": "source", "title": "测试"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="公司共有 2 人。",
    )
    followup = detect_result_followup("你只需要告诉我男生的数量", result_context)

    answer = compose_answer(
        context=_context("你只需要告诉我男生的数量", result_context=result_context),
        intent=IntentResult(question_type="query", intent="smalltalk", data_scope="self"),
        permission=PermissionDecision(allowed=True),
        execution=None,
        followup=followup,
    )

    assert followup.is_result_followup is True
    assert followup.entity_ref["field_projection"] == "count_only"
    assert answer.answer == "能确认的男性员工是 2 位。"
    assert "张三" not in answer.answer


def test_runtime_v5_people_followup_count_question_defaults_to_summary() -> None:
    result_context = ResultContext(
        result_type="organization_snapshot",
        count=3,
        items=(
            {"name": "张三", "gender": "male", "gender_source": "source", "title": "工程师"},
            {"name": "李四", "gender": "female", "gender_source": "source", "title": "财务"},
            {"name": "王五", "gender": "male", "gender_source": "source", "title": "测试"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="公司共有 3 人。",
    )
    followup = detect_result_followup("男生有多少人", result_context)

    answer = compose_answer(
        context=_context("男生有多少人", result_context=result_context),
        intent=IntentResult(question_type="query", intent="smalltalk", data_scope="self"),
        permission=PermissionDecision(allowed=True),
        execution=None,
        followup=followup,
    )

    assert followup.is_result_followup is True
    assert followup.entity_ref["field_projection"] == "count_only"
    assert answer.answer == "能确认的男性员工是 2 位。"
    assert answer.result_context is not None
    assert answer.result_context.metadata["result_context_presentation"] == "summary"
    assert "张三" not in answer.answer


def test_runtime_v5_company_people_count_starts_new_question_over_single_person_context() -> None:
    result_context = ResultContext(
        result_type="organization_snapshot",
        count=1,
        items=({"name": "陈俊", "title": "董事长"},),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="陈俊（董事长，职能中心）",
    )

    for question in ("公司有多少人，大飞哥", "我问你公司有多少人", "公司有多少男生"):
        followup = detect_result_followup(question, result_context)

        assert followup.is_result_followup is True
        assert should_start_new_question_over_result_context(question, result_context, followup=followup) is True


def test_runtime_v5_count_only_expand_followup_never_dumps_items() -> None:
    result_context = ResultContext(
        result_type="organization_snapshot",
        count=2,
        items=(
            {"name": "张三", "title": "工程师", "mobile": "13800000001"},
            {"name": "李四", "title": "财务", "mobile": "13800000002"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="上一轮人员结果。",
    )
    followup = detect_result_followup("公司有多少人", result_context)

    answer = compose_answer(
        context=_context("公司有多少人", result_context=result_context),
        intent=IntentResult(question_type="query", intent="smalltalk", data_scope="self"),
        permission=PermissionDecision(allowed=True),
        execution=None,
        followup=followup,
    )

    assert followup.is_result_followup is True
    assert followup.entity_ref["field_projection"] == "count_only"
    assert answer.answer == "这组结果共有 2 人。"
    assert "张三" not in answer.answer
    assert "13800000001" not in answer.answer


def test_runtime_v5_people_filter_followup_inherits_summary_projection() -> None:
    result_context = ResultContext(
        result_type="organization_snapshot",
        count=3,
        items=(
            {"name": "张三", "gender": "male", "gender_source": "source", "title": "工程师"},
            {"name": "李四", "gender": "female", "gender_source": "source", "title": "财务"},
            {"name": "王五", "gender": "male", "gender_source": "source", "title": "测试"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people", "result_context_presentation": "summary"},
        answer="3人。",
    )
    intent = recognize_intent("男生呢", _context("男生呢", result_context=result_context))
    followup = detect_result_followup("男生呢", result_context)

    answer = compose_answer(
        context=_context("男生呢", result_context=result_context),
        intent=IntentResult(question_type="query", intent="smalltalk", data_scope="self"),
        permission=PermissionDecision(allowed=True),
        execution=None,
        followup=followup,
    )

    assert intent.intent != "people_lookup"
    assert followup.is_result_followup is True
    assert followup.followup_type == "people_filter"
    assert followup.entity_ref["field_projection"] == "count_only"
    assert answer.answer == "能确认的男性员工是 2 位。"
    assert "张三" not in answer.answer
    assert "王五" not in answer.answer


def test_runtime_v5_people_list_followup_overrides_count_projection() -> None:
    result_context = ResultContext(
        result_type="organization_snapshot",
        count=2,
        items=(
            {"name": "张三", "gender": "male", "gender_source": "source", "title": "工程师"},
            {"name": "王五", "gender": "male", "gender_source": "source", "title": "测试"},
        ),
        metadata={
            "context_kind": "query_result",
            "entity_domain": "people",
            "field_projection": "count_only",
            "result_context_presentation": "summary",
            "people_filter": {"filter": "gender", "gender": "male"},
        },
        answer="能确认的男性员工是 2 位。",
    )
    followup = detect_result_followup("人员名单", result_context)

    answer = compose_answer(
        context=_context("人员名单", result_context=result_context),
        intent=IntentResult(question_type="query", intent="smalltalk", data_scope="self"),
        permission=PermissionDecision(allowed=True),
        execution=None,
        followup=followup,
    )

    assert followup.is_result_followup is True
    assert followup.entity_ref["field_projection"] == "detail"
    assert answer.result_context is not None
    assert answer.result_context.metadata["result_context_presentation"] == "detail"
    assert "确认操作" not in answer.answer
    assert "待确认" not in answer.answer


def test_runtime_v5_yes_is_not_standalone_action_confirmation() -> None:
    assert _is_standalone_confirmation_message("确认") is True
    assert _is_standalone_confirmation_message("是的") is False


def test_runtime_v5_result_context_which_people_expands_previous_filtered_set() -> None:
    result_context = ResultContext(
        result_type="organization_snapshot",
        count=2,
        items=(
            {"name": "李四", "gender": "female", "title": "财务"},
            {"name": "王五", "gender": "female", "title": "项目经理"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people", "people_filter": {"filter": "gender", "gender": "female"}},
        answer="上一轮女性员工结果。",
    )
    followup = detect_result_followup("哪2位呢", result_context)

    answer = compose_answer(
        context=_context("哪2位呢", result_context=result_context),
        intent=IntentResult(question_type="query", intent="smalltalk", data_scope="self"),
        permission=PermissionDecision(allowed=True),
        execution=None,
        followup=followup,
    )

    assert followup.is_result_followup is True
    assert followup.followup_type == "expand"
    assert "李四" in answer.answer
    assert "王五" in answer.answer


def test_runtime_v5_result_context_continue_uses_display_window_and_inherited_projection() -> None:
    items = tuple(
        {"name": f"员工{index}", "title": "工程师", "mobile": f"1380000{index:04d}", "open_id": f"ou_{index}"}
        for index in range(1, 23)
    )
    result_context = ResultContext(
        result_type="organization_snapshot",
        count=len(items),
        items=items,
        metadata={
            "context_kind": "query_result",
            "entity_domain": "people",
            "field_projection": "name_only",
            "display_offset": 0,
            "display_end": 20,
            "display_limit": 20,
            "has_more": True,
        },
        answer="上一轮已展示前 20 位姓名。",
    )
    followup = detect_result_followup("补全", result_context)

    answer = compose_answer(
        context=_context("补全", result_context=result_context),
        intent=IntentResult(question_type="query", intent="smalltalk", data_scope="self"),
        permission=PermissionDecision(allowed=True),
        execution=None,
        followup=followup,
    )

    assert followup.is_result_followup is True
    assert followup.entity_ref["result_context_operation"] == "continue"
    assert followup.entity_ref["field_projection"] == "name_only"
    assert answer.answer == "这组结果共有 22 人，名单我放到侧边栏里，聊天里不展开长清单。"
    assert "21. 员工21" not in answer.answer
    assert "22. 员工22" not in answer.answer
    assert "员工1" not in answer.answer
    assert "1380000" not in answer.answer
    assert "ou_21" not in answer.answer
    assert answer.result_context is not None
    assert answer.result_context.metadata["result_context_presentation"] == "detail"
    assert answer.result_context.metadata["display_offset"] == 20
    assert answer.result_context.metadata["display_end"] == 22
    assert answer.result_context.metadata["has_more"] is False


def test_runtime_v5_result_context_continue_does_not_restart_when_no_remaining_items() -> None:
    result_context = ResultContext(
        result_type="organization_snapshot",
        count=2,
        items=(
            {"name": "李四", "title": "财务"},
            {"name": "王五", "title": "项目经理"},
        ),
        metadata={
            "context_kind": "query_result",
            "entity_domain": "people",
            "display_end": 2,
            "display_limit": 20,
            "has_more": False,
        },
        answer="上一轮已展示完。",
    )
    followup = detect_result_followup("剩下的呢", result_context)

    answer = compose_answer(
        context=_context("剩下的呢", result_context=result_context),
        intent=IntentResult(question_type="query", intent="smalltalk", data_scope="self"),
        permission=PermissionDecision(allowed=True),
        execution=None,
        followup=followup,
    )

    assert followup.is_result_followup is True
    assert followup.entity_ref["result_context_operation"] == "continue"
    assert answer.answer == "上一轮结果已经没有剩余可补全的明细。"
    assert "李四" not in answer.answer
    assert "王五" not in answer.answer


def test_runtime_v5_im_send_requires_delivery_mode_for_people_context_targets() -> None:
    result_context = ResultContext(
        result_type="organization_snapshot",
        count=2,
        items=(
            {"name": "张三", "open_id": "ou_zhang", "title": "工程师"},
            {"name": "李四", "open_id": "ou_li", "title": "测试"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="上一轮人员结果。",
    )

    intent = recognize_intent("把这些人发消息说：明天上午提交周报", _context("把这些人发消息说：明天上午提交周报", result_context=result_context))

    assert intent.intent == "message_send"
    assert intent.missing_params == ("delivery_mode",)
    assert intent.entities["target_type"] == "people_context"
    assert intent.entities["text"] == "明天上午提交周报"
    assert [item["open_id"] for item in intent.entities["people_targets"]] == ["ou_zhang", "ou_li"]

    answer = compose_answer(
        context=_context("把这些人发消息说：明天上午提交周报", result_context=result_context),
        intent=intent,
        permission=PermissionDecision(allowed=True),
        execution=None,
    )
    assert "用机器人通知这些人" in answer.answer


def test_runtime_v5_im_send_reuses_people_result_context_with_explicit_delivery_mode() -> None:
    result_context = ResultContext(
        result_type="organization_snapshot",
        count=2,
        items=(
            {"name": "张三", "open_id": "ou_zhang", "title": "工程师"},
            {"name": "李四", "open_id": "ou_li", "title": "测试"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="上一轮人员结果。",
    )

    intent = recognize_intent("用机器人发给这些人说：明天上午提交周报", _context("用机器人发给这些人说：明天上午提交周报", result_context=result_context))

    assert intent.intent == "message_send"
    assert intent.missing_params == ()
    assert intent.entities["target_type"] == "people_context"
    assert intent.entities["delivery_mode"] == "bot_multi_notify"
    assert intent.entities["text"] == "明天上午提交周报"


def test_runtime_v5_im_send_to_organization_people_uses_people_context_not_chat() -> None:
    result_context = ResultContext(
        result_type="department_members",
        count=1,
        items=({"name": "汤冠男", "open_id": "ou_tang", "title": "部门高级经理"},),
        metadata={
            "context_kind": "query_result",
            "entity_domain": "people",
            "organization_resolution": {"query": "商务部", "resolved_name": "商务组"},
        },
        answer="商务组目前 1 位，是汤冠男。",
    )

    intent = recognize_intent(
        "给商务组的人员发条消息：测试内容",
        _context("给商务组的人员发条消息：测试内容", result_context=result_context),
    )

    assert intent.intent == "message_send"
    assert intent.entities["target_type"] == "people_context"
    assert intent.entities["target"] == "商务组"
    assert [(item["name"], item["open_id"], item["title"]) for item in intent.entities["people_targets"]] == [("汤冠男", "ou_tang", "部门高级经理")]
    assert intent.entities["text"] == "测试内容"
    assert intent.missing_params == ("delivery_mode",)


def test_runtime_v5_im_send_to_organization_people_without_delivery_mode_waits_for_choice() -> None:
    result_context = ResultContext(
        result_type="department_members",
        count=1,
        items=({"name": "汤冠男", "open_id": "ou_tang", "title": "部门高级经理"},),
        metadata={
            "context_kind": "query_result",
            "entity_domain": "people",
            "organization_resolution": {"query": "商务部", "resolved_name": "商务组"},
        },
        answer="商务组目前 1 位，是汤冠男。",
    )

    result = run_runtime_v5(
        context=_context(
            "给商务组的人员发条消息：测试内容",
            chat_id="chat_org_people_missing_delivery",
            result_context=result_context,
        ),
        providers={"im": object()},
    )

    assert result.intent.intent == "message_send"
    assert result.intent.entities["target_type"] == "people_context"
    assert result.intent.entities["target"] == "商务组"
    assert result.intent.entities["text"] == "测试内容"
    assert result.intent.missing_params == ("delivery_mode",)
    assert result.execution is None
    assert result.composed.result_context is not None
    assert result.composed.result_context.result_type == "runtime_waiting_input"
    assert "当前多人目标只开放" in result.composed.answer
    assert "操作确认" not in result.composed.answer


def test_runtime_v5_im_send_to_organization_people_with_unavailable_delivery_mode_stays_waiting() -> None:
    result_context = ResultContext(
        result_type="department_members",
        count=1,
        items=({"name": "汤冠男", "open_id": "ou_tang", "title": "部门高级经理"},),
        metadata={
            "context_kind": "query_result",
            "entity_domain": "people",
            "organization_resolution": {"query": "商务部", "resolved_name": "商务组"},
        },
        answer="商务组目前 1 位，是汤冠男。",
    )

    result = run_runtime_v5(
        context=_context(
            "给商务组的人分别发条消息：测试内容",
            chat_id="chat_org_people_message",
            result_context=result_context,
        ),
        providers={"im": object()},
    )

    assert result.intent.intent == "message_send"
    assert result.intent.entities["target_type"] == "people_context"
    assert result.intent.entities["target"] == "商务组"
    assert result.intent.entities["delivery_mode"] == "user_multi_private"
    assert result.intent.missing_params == ()
    assert result.execution is None
    assert result.composed.result_context is not None
    assert result.composed.result_context.result_type == "runtime_waiting_input"
    assert "当前多人目标只开放" in result.composed.answer
    assert "操作确认" not in result.composed.answer


def test_runtime_v5_explicit_chat_send_does_not_reuse_people_result_context() -> None:
    result_context = ResultContext(
        result_type="organization_snapshot",
        count=2,
        items=(
            {"name": "张三", "open_id": "ou_zhang", "title": "工程师"},
            {"name": "李四", "open_id": "ou_li", "title": "测试"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="上一轮人员结果。",
    )

    intent = recognize_intent("发条信息到大飞哥测试群：测试。", _context("发条信息到大飞哥测试群：测试。", result_context=result_context))

    assert intent.intent == "message_send"
    assert intent.missing_params == ()
    assert intent.entities["target_type"] == "chat"
    assert intent.entities["target"] == "大飞哥测试"
    assert intent.entities["text"] == "测试。"
    assert "people_targets" not in intent.entities
    assert intent.entities["execution_identity"] == "bot"


def test_runtime_v5_im_provider_does_not_auto_send_people_context_batch() -> None:
    provider = FeishuIMProvider(db=None)
    result = provider._im_send_params(
        ProviderRequest(
            source="im",
            operation="send_message",
            intent=IntentResult(question_type="action", intent="message_send", data_scope="self"),
            planner=_command_plan("message_send", sources=("im",)),
            context=_context("把这些人发消息说：明天上午十点开会"),
            execution_identity="user",
            params={
                "target_type": "people_context",
                "text": "明天上午十点开会",
                "people_targets": [
                    {"name": "张三", "open_id": "ou_zhang"},
                    {"name": "李四", "open_id": "ou_li"},
                ],
            },
        )
    )

    assert isinstance(result, ProviderResult)
    assert result.status == "partial"
    assert result.result_type == "message_send_people_context"
    assert result.count == 2
    assert result.metadata["requires_confirmation"] is True
    assert "未直接发送" in result.answer


def test_runtime_v5_im_provider_create_group_then_send_uses_create_chat_then_send() -> None:
    calls: list[tuple[str, dict]] = []

    class RecordingIMProvider(FeishuIMProvider):
        def _execute_tool(self, request, *, tool_name: str, params: dict | None = None, confirm_write: bool = False):
            calls.append((tool_name, dict(params or {})))
            if tool_name == "feishu_im_create_chat":
                return SimpleNamespace(
                    status=ToolExecutionStatus.SUCCESS,
                    error="",
                    answer="飞书群已通过 CLI 创建：oc_group_1",
                    structured_result={},
                )
            return SimpleNamespace(
                status=ToolExecutionStatus.SUCCESS,
                error="",
                answer="飞书消息已通过 CLI 发送：om_1",
                structured_result={},
            )

    provider = RecordingIMProvider(db=None)

    result = provider.execute(
        ProviderRequest(
            source="im",
            operation="send_message",
            intent=IntentResult(question_type="action", intent="message_send", data_scope="self"),
            planner=_command_plan("message_send", sources=("im",)),
            context=_context("拉群后发给这些人说：明天上午十点开会"),
            execution_identity="user",
            params={
                "target_type": "people_context",
                "delivery_mode": "create_group_then_send",
                "text": "明天上午十点开会",
                "people_targets": [
                    {"name": "张三", "open_id": "ou_zhang"},
                    {"name": "李四", "open_id": "ou_li"},
                ],
            },
        )
    )

    assert result.status == "success"
    assert result.result_type == "message_send_people_context_group"
    assert result.metadata["delivery_mode"] == "create_group_then_send"
    assert result.metadata["people_target_count"] == 2
    assert result.metadata["resolved_chat_id"] == "oc_group_1"
    assert calls[0][0] == "feishu_im_create_chat"
    assert calls[0][1]["user_id_list"] == ["ou_zhang", "ou_li"]
    assert calls[0][1]["chat_type"] == "private"
    assert calls[0][1]["chat_mode"] == "group"
    assert calls[0][1]["as"] == "bot"
    assert calls[1] == ("feishu_im_send_message", {"chat_id": "oc_group_1", "text": "明天上午十点开会", "as": "bot"})
    assert result.metadata["execution_identity"] == "bot"


def test_feishu_mcp_im_create_and_send_use_tenant_token_before_cli(monkeypatch) -> None:
    calls: list[tuple[str, str, dict, dict]] = []

    monkeypatch.setattr(feishu_mcp, "_active_feishu_app_config", lambda context: SimpleNamespace(app_id="cli_app_id"))

    def fake_tenant_http_request(app_config, method, path, *, params=None, payload=None, auth=True, label="Feishu API"):
        calls.append((method, path, dict(params or {}), dict(payload or {})))
        if path == "/open-apis/im/v1/chats":
            return {"code": 0, "data": {"chat": {"chat_id": "oc_group_1"}}}
        if path == "/open-apis/im/v1/messages":
            return {"code": 0, "data": {"message_id": "om_1"}}
        raise AssertionError(path)

    monkeypatch.setattr(feishu_mcp, "_tenant_http_request", fake_tenant_http_request)

    create_answer = feishu_mcp._execute_tenant_im_tool(
        SimpleNamespace(),
        ToolRequest(
            tool_name="feishu_im_create_chat",
            question="拉群",
            normalized_command="拉群",
            params={"name": "测试群", "user_id_list": ["ou_1", "ou_2"], "chat_type": "private", "chat_mode": "group"},
        ),
        fallback=lambda: "cli-fallback",
    )
    send_answer = feishu_mcp._execute_tenant_im_tool(
        SimpleNamespace(),
        ToolRequest(
            tool_name="feishu_im_send_message",
            question="发消息",
            normalized_command="发消息",
            params={"chat_id": "oc_group_1", "text": "测试"},
        ),
        fallback=lambda: "cli-fallback",
    )

    assert "Tenant Token" in create_answer
    assert "oc_group_1" in create_answer
    assert "Tenant Token" in send_answer
    assert "om_1" in send_answer
    assert calls[0] == (
        "POST",
        "/open-apis/im/v1/chats",
        {"user_id_type": "open_id"},
        {"name": "测试群", "chat_mode": "group", "chat_type": "private", "user_id_list": ["ou_1", "ou_2"]},
    )
    assert calls[1] == (
        "POST",
        "/open-apis/im/v1/messages",
        {"receive_id_type": "chat_id"},
        {"receive_id": "oc_group_1", "msg_type": "text", "content": '{"text":"测试"}'},
    )


def test_runtime_v5_im_provider_send_uses_tenant_token_before_cli(monkeypatch) -> None:
    calls: list[tuple[str, str, dict, dict]] = []

    class FakeDb:
        def scalar(self, query):
            return SimpleNamespace(app_id="cli_app_id", app_secret="app_secret")

    monkeypatch.setattr("app.services.tools.router._effective_definition", lambda context, definition: definition)
    monkeypatch.setattr("app.services.tools.router.write_audit_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        feishu_mcp,
        "_execute_cli_im_send_message",
        lambda request: (_ for _ in ()).throw(AssertionError("CLI fallback must not be used when tenant app config exists")),
    )

    def fake_tenant_http_request(app_config, method, path, *, params=None, payload=None, auth=True, label="Feishu API"):
        calls.append((method, path, dict(params or {}), dict(payload or {})))
        return {"code": 0, "data": {"message_id": "om_1"}}

    monkeypatch.setattr(feishu_mcp, "_tenant_http_request", fake_tenant_http_request)

    result = FeishuIMProvider(db=FakeDb()).execute(
        ProviderRequest(
            source="im",
            operation="send_message",
            intent=IntentResult(question_type="action", intent="message_send", data_scope="self"),
            planner=_command_plan("message_send", sources=("im",)),
            context=_context("发消息到当前会话：测试", chat_id="oc_current_chat"),
            execution_identity="bot",
            params={"target_type": "current_chat", "text": "测试"},
        )
    )

    assert result.status == "success"
    assert result.result_type == "message_send"
    assert result.metadata["tool_name"] == "feishu_im_send_message"
    assert calls == [
        (
            "POST",
            "/open-apis/im/v1/messages",
            {"receive_id_type": "chat_id"},
            {"receive_id": "oc_current_chat", "msg_type": "text", "content": '{"text":"测试"}'},
        )
    ]


def test_runtime_v5_mail_draft_reuses_people_result_context_as_recipients() -> None:
    result_context = ResultContext(
        result_type="people_search",
        count=2,
        items=(
            {"name": "张三", "open_id": "ou_zhang", "email": "zhangsan@example.com"},
            {"name": "李四", "open_id": "ou_li", "email": "lisi@example.com"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="上一轮人员结果。",
    )

    intent = recognize_intent(
        "给这些人写封邮件，主题：周报提醒 正文：明天上午提交周报",
        _context("给这些人写封邮件，主题：周报提醒 正文：明天上午提交周报", result_context=result_context),
    )

    assert intent.intent == "mail_draft_create"
    assert intent.missing_params == ()
    assert intent.entities["to"] == "zhangsan@example.com, lisi@example.com"
    assert [item["open_id"] for item in intent.entities["people_targets"]] == ["ou_zhang", "ou_li"]


def test_runtime_v5_calendar_create_reuses_people_result_context_as_attendees() -> None:
    result_context = ResultContext(
        result_type="organization_snapshot",
        count=2,
        items=(
            {"name": "张三", "open_id": "ou_zhang", "email": "zhangsan@example.com"},
            {"name": "李四", "open_id": "ou_li", "email": "lisi@example.com"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="上一轮人员结果。",
    )

    intent = recognize_intent(
        "安排这些人明天下午3点到4点开会，主题：周报同步",
        _context("安排这些人明天下午3点到4点开会，主题：周报同步", result_context=result_context),
    )

    assert intent.intent == "calendar_create"
    assert intent.missing_params == ()
    assert intent.entities["attendee_ids"] == ["ou_zhang", "ou_li"]
    assert intent.entities["user_id_type"] == "open_id"
    assert [item["email"] for item in intent.entities["people_targets"]] == ["zhangsan@example.com", "lisi@example.com"]


def test_runtime_v5_task_create_reuses_people_result_context_as_members() -> None:
    result_context = ResultContext(
        result_type="department_members",
        count=2,
        items=(
            {"name": "张三", "open_id": "ou_zhang", "email": "zhangsan@example.com"},
            {"name": "李四", "open_id": "ou_li", "email": "lisi@example.com"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="上一轮人员结果。",
    )

    intent = recognize_intent(
        "给这些人创建任务：明天下午提交测试报告",
        _context("给这些人创建任务：明天下午提交测试报告", result_context=result_context),
    )

    assert intent.intent == "task_create"
    assert intent.missing_params == ()
    assert intent.entities["summary"] == "明天下午提交测试报告"
    assert intent.entities["members"] == ["ou_zhang", "ou_li"]
    assert intent.entities["user_id_type"] == "open_id"
    assert [item["name"] for item in intent.entities["people_targets"]] == ["张三", "李四"]


def test_runtime_v5_people_results_prefer_natural_language_not_cards() -> None:
    command_plan = _command_plan("organization_snapshot", result_type="organization_snapshot", sources=("people",), data_scope="organization")
    runtime_result = RuntimeResult(
        result_type="organization_snapshot",
        status="success",
        title="通讯录",
        summary="公司共有 47 人。",
        target_ui="none",
    )

    assert should_use_interactive_card("feishu_contact_organization_snapshot", "公司共有 47 人。") is False
    built = build_runtime_result(
        command_plan=command_plan,
        permission=PermissionDecision(allowed=True, execution_identity="bot"),
        execution=ExecutionResult(strategy="organization_snapshot", status="success", provider_results=()),
        composed=ComposedAnswer(
            answer="公司共有 47 人。",
            result_context=ResultContext(result_type="organization_snapshot", count=1, items=({"name": "张三"},)),
        ),
    )
    assert built.target_ui == "none"
    assert build_runtime_result_card(runtime_result_payload(runtime_result)) is None


def test_runtime_v5_company_profile_query_does_not_render_raw_knowledge_events(monkeypatch) -> None:
    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers._knowledge_document_items", lambda *args, **kwargs: ())

    class Db:
        def get(self, model, company_id):
            return SimpleNamespace(
                name="能躬行科技",
                code="gaustek",
                status="active",
                metadata_json={},
            )

    context = _context("公司是做什么的")
    result = KnowledgeProvider(db=Db()).execute(
        ProviderRequest(
            source="knowledge",
            operation="search",
            intent=IntentResult(question_type="query", intent="general_query", data_scope="company", entities={"knowledge_context": "company_profile"}),
            planner=_command_plan("general_query", sources=("knowledge",)),
            context=context,
            execution_identity="bot",
        )
    )

    assert result.status == "success"
    assert result.result_type == "company_profile_knowledge"
    assert "暂时还没有沉淀主营业务或公司简介" in result.answer
    assert "mail_address" not in result.answer
    assert "文档事件" not in result.answer


def test_runtime_v5_company_profile_query_uses_snapshot_before_knowledge(monkeypatch) -> None:
    calls = {"knowledge_documents": 0}

    def fail_if_called(*args, **kwargs):
        calls["knowledge_documents"] += 1
        return ()

    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers._knowledge_document_items", fail_if_called)

    snapshot = SimpleNamespace(
        id=uuid4(),
        company_id=uuid4(),
        object_type="company",
        object_id="company-1",
        snapshot_type="company_profile_v1",
        status="completed",
        summary="固势主要面向测试测量和实验场景，提供相关产品与解决方案。",
        recommendation="",
        risk_level="unknown",
        reasons=[],
        source_event_ids=["event-1"],
        payload={
            "snapshot_version": "company_profile_v1",
            "structured_fields": {
                "company_positioning": "固势主要面向测试测量和实验场景，提供相关产品与解决方案。",
                "business_scope": ["测试测量相关产品与解决方案"],
                "products": ["GAUSTEK SRI 全系列产品", "测试测量产品系列"],
                "industry": ["测试测量"],
                "target_customers": ["需要测试测量能力的研发、实验和生产团队"],
                "advantages": ["让测试更简单"],
                "contacts": {"emails": ["Business@gaustek.com"], "phones": [], "addresses": []},
            },
            "confidence": "medium",
            "evidence_refs": ["event-1"],
        },
    )

    class Db:
        def scalar(self, query):
            return snapshot

    context = _context("公司有哪些产品")
    result = KnowledgeProvider(db=Db()).execute(
        ProviderRequest(
            source="knowledge",
            operation="search",
            intent=IntentResult(question_type="query", intent="general_query", data_scope="company", entities={"knowledge_context": "company_profile"}),
            planner=_command_plan("general_query", sources=("knowledge",)),
            context=context,
            execution_identity="bot",
        )
    )

    assert result.status == "success"
    assert result.result_type == "company_profile_knowledge"
    assert result.metadata["retrieval_source"] == "snapshot"
    assert result.metadata["snapshot_type"] == "company_profile_v1"
    assert result.items[0]["kind"] == "company_snapshot"
    assert "GAUSTEK SRI 全系列产品" in result.answer
    assert calls["knowledge_documents"] == 0


def test_runtime_v5_company_profile_query_builds_snapshot_from_knowledge_evidence(monkeypatch) -> None:
    document_item = {
        "kind": "knowledge_document",
        "title": "固势宣传册26--中文.pdf",
        "summary": "固势（苏州）科技有限公司 GAUSTEK SRI 全系列产品手册 让测试更简单 让实验更高效 PRODUCT SERIES 产品系列 Business@gaustek.com",
        "source": "registered_resource",
        "resource_type": "drive_file",
        "document_id": "file_pdf",
        "document_type": "file",
    }
    monkeypatch.setattr(
        "app.services.runtime_v5.feishu_resource_providers._knowledge_document_items",
        lambda *args, **kwargs: (document_item,),
    )

    class Db:
        def __init__(self):
            self.added = []
            self.flush_count = 0

        def scalar(self, query):
            return None

        def add(self, item):
            self.added.append(item)

        def flush(self):
            self.flush_count += 1

    db = Db()
    context = _context("公司是做什么的")
    result = KnowledgeProvider(db=db).execute(
        ProviderRequest(
            source="knowledge",
            operation="search",
            intent=IntentResult(question_type="query", intent="general_query", data_scope="company", entities={"knowledge_context": "company_profile"}),
            planner=_command_plan("general_query", sources=("knowledge",)),
            context=context,
            execution_identity="bot",
        )
    )

    assert result.status == "success"
    assert result.metadata["retrieval_source"] == "snapshot"
    assert result.metadata["document_count"] == 1
    assert result.items[0]["kind"] == "company_snapshot"
    assert "测试测量" in result.answer
    assert any(getattr(item, "event_type", "") == "evidence.company_profile.observed" for item in db.added)
    assert any(getattr(item, "snapshot_type", "") == "company_profile_v1" for item in db.added)


def test_runtime_v5_company_profile_query_uses_official_knowledge_documents(monkeypatch) -> None:
    document_item = {
        "kind": "knowledge_document",
        "title": "公司介绍",
        "summary": "主营业务：工业智能装备和企业数字化服务。",
        "source": "registered_resource",
        "resource_type": "drive_file",
    }
    monkeypatch.setattr(
        "app.services.runtime_v5.feishu_resource_providers._knowledge_document_items",
        lambda *args, **kwargs: (document_item,),
    )

    class Db:
        def get(self, model, company_id):
            return SimpleNamespace(
                name="能躬行科技",
                code="gaustek",
                status="active",
                metadata_json={"intro": "面向制造企业提供数字化能力。"},
            )

    context = _context("公司是做什么的")
    result = KnowledgeProvider(db=Db()).execute(
        ProviderRequest(
            source="knowledge",
            operation="search",
            intent=IntentResult(question_type="query", intent="general_query", data_scope="company", entities={"knowledge_context": "company_profile"}),
            planner=_command_plan("general_query", sources=("knowledge",)),
            context=context,
            execution_identity="bot",
        )
    )

    assert result.status == "success"
    assert result.result_type == "company_profile_knowledge"
    assert result.metadata["document_count"] == 1
    assert result.metadata["company_profile_count"] == 1
    assert "正式知识资料" in result.answer
    assert "公司介绍" in result.answer
    assert "企业画像补充" in result.answer
    assert "底层日志" in result.answer


def test_runtime_v5_company_profile_runtime_result_stays_text_summary() -> None:
    command_plan = build_command_plan(context=_context("公司是做什么的"))
    result = build_runtime_result(
        command_plan=command_plan,
        permission=PermissionDecision(allowed=True, requires_confirmation=False, execution_identity="bot"),
        execution=None,
        composed=ComposedAnswer(
            answer="主营业务：工业智能装备和企业数字化服务。",
            result_context=ResultContext(
                result_type="company_profile_knowledge",
                count=1,
                items=(
                    {
                        "kind": "knowledge_document",
                        "title": "公司介绍",
                        "summary": "主营业务：工业智能装备和企业数字化服务。",
                        "source": "registered_resource",
                    },
                ),
                metadata={
                    "knowledge_context": "company_profile",
                    "result_context_presentation": "summary",
                },
            ),
        ),
    )

    assert command_plan.command_frame is not None
    assert command_plan.command_frame.response_intent["should_render_card"] is False
    assert result.actions == ()
    assert result.metadata["sidepanel_context"] == {}
    assert build_runtime_result_card(runtime_result_payload(result)) is None


def test_runtime_v5_company_profile_llm_enriched_query_stays_text_summary() -> None:
    intent = IntentResult(
        question_type="query",
        intent="general_query",
        data_scope="company",
        entities={"query": "公司是做什么的"},
        confidence=0.82,
        canonical_question="公司是做什么的。",
    )
    planner = PlannerResult(strategy="general_query", sources=("knowledge",))
    base_plan = _command_plan("general_query", result_type="general_query", sources=("knowledge",), data_scope="company")
    frame = build_command_frame(context=_context("公司是做什么的。"), intent=intent, planner=planner)
    command_plan = CommandPlan(
        intent=intent.intent,
        steps=base_plan.steps,
        target_ui=base_plan.target_ui,
        tool_candidates=base_plan.tool_candidates,
        context_scope=base_plan.context_scope,
        intent_result=intent,
        planner_result=planner,
        command_frame=frame,
    )

    result = build_runtime_result(
        command_plan=command_plan,
        permission=PermissionDecision(allowed=True, requires_confirmation=False, execution_identity="bot"),
        execution=None,
        composed=ComposedAnswer(
            answer="主营业务：工业智能装备和企业数字化服务。",
            result_context=ResultContext(
                result_type="company_profile_knowledge",
                count=1,
                items=({"title": "公司介绍", "summary": "主营业务：工业智能装备和企业数字化服务。"},),
                metadata={},
            ),
        ),
    )

    assert frame.response_intent["should_render_card"] is False
    assert result.target_ui == "none"
    assert result.actions == ()
    assert build_runtime_result_card(runtime_result_payload(result)) is None


def test_runtime_v5_general_knowledge_query_uses_official_documents(monkeypatch) -> None:
    document_item = {
        "kind": "knowledge_document",
        "title": "报销流程说明",
        "summary": "员工提交报销时需要上传发票、审批单和付款信息。",
        "source": "drive_list",
        "resource_type": "drive_file",
    }
    monkeypatch.setattr(
        "app.services.runtime_v5.feishu_resource_providers._knowledge_document_items",
        lambda *args, **kwargs: (document_item,),
    )
    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers._knowledge_facts", lambda *args, **kwargs: [])
    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers._knowledge_events", lambda *args, **kwargs: [])
    monkeypatch.setattr("app.services.runtime_v5.feishu_resource_providers._company_profile_knowledge_items", lambda *args, **kwargs: ())

    context = _context("报销流程怎么做")
    result = KnowledgeProvider(db=SimpleNamespace()).execute(
        ProviderRequest(
            source="knowledge",
            operation="search",
            intent=IntentResult(question_type="query", intent="general_query", data_scope="company", entities={}),
            planner=_command_plan("general_query", sources=("knowledge",)),
            context=context,
            execution_identity="bot",
        )
    )

    assert result.status == "success"
    assert result.result_type == "knowledge_list"
    assert result.metadata["document_count"] == 1
    assert "正式文档｜报销流程说明" in result.answer


def test_registered_knowledge_candidates_read_nested_document_type_and_skip_bitable() -> None:
    company_id = uuid4()
    doc_resource = SimpleNamespace(
        id=uuid4(),
        company_id=company_id,
        resource_type="drive_file",
        resource_name="报销流程说明",
        resource_id="W6h6dl6dQoCN9zx2M1QcWHXtnOd",
        enabled=True,
        updated_at=None,
        config_json={
            "settings": {
                "document_type": "docx",
                "raw": {"type": "docx"},
            }
        },
    )
    bitable_resource = SimpleNamespace(
        id=uuid4(),
        company_id=company_id,
        resource_type="drive_file",
        resource_name="报销流程多维表格",
        resource_id="PidobYUGmaRT5bs5ExZcHGe6nwf",
        enabled=True,
        updated_at=None,
        config_json={
            "settings": {
                "document_type": "bitable",
                "raw": {"type": "bitable"},
            }
        },
    )

    class Scalars:
        def all(self):
            return [doc_resource, bitable_resource]

    class Db:
        query = None

        def scalars(self, query):
            self.query = query
            return Scalars()

    db = Db()
    candidates = _registered_knowledge_resource_candidates(
        db,
        company_id=company_id,
        seed_text="报销流程怎么做",
        context="general",
        limit=5,
    )

    assert [item["title"] for item in candidates] == ["报销流程说明"]
    assert candidates[0]["document_type"] == "docx"
    assert "business_domain" in str(db.query)

    unrelated = _registered_knowledge_resource_candidates(
        db,
        company_id=company_id,
        seed_text="制度文件在哪里",
        context="general",
        limit=5,
    )

    assert unrelated == []


def test_read_knowledge_document_candidate_extracts_file_text(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.runtime_v5.feishu_resource_providers.extract_file",
        lambda data, *, filename, content_type=None, max_chars=12000: SimpleNamespace(
            success=True,
            text="固势宣传册 公司介绍 主营业务：工业智能设备。",
        ),
    )

    class FakeService:
        def __init__(self):
            self.calls = []

        async def download_file_content(self, *, file_token):
            self.calls.append(file_token)
            return b"pdf", "application/pdf"

    service = FakeService()
    item = _read_knowledge_document_candidate(
        service,
        {
            "title": "固势宣传册26--中文.pdf",
            "document_id": "file_pdf",
            "document_type": "file",
            "source": "registered_resource",
            "resource_type": "drive_file",
        },
        seed_text="公司是做什么的",
        context="company_profile",
    )

    assert service.calls == ["file_pdf"]
    assert item is not None
    assert item["kind"] == "knowledge_document"
    assert item["document_type"] == "file"
    assert "主营业务" in item["summary"]


def test_runtime_v5_placeholder_objective_does_not_render_as_intro() -> None:
    from app.services.runtime_v5.composer import _natural_enrichment_intro

    assert _natural_enrichment_intro("query") == ""
    assert _natural_enrichment_intro("查询") == ""
    assert _natural_enrichment_intro("获取公司主营业务") == "我先按你的问题整理当前可见结果：获取公司主营业务。"


def test_feishu_contact_snapshot_uses_authorized_scope_when_root_tree_empty(monkeypatch) -> None:
    monkeypatch.setattr(feishu_mcp, "_run_contact_department_children", lambda params, *, department_id: {"data": {"items": []}})
    monkeypatch.setattr(
        feishu_mcp,
        "_run_contact_scope_list_payload",
        lambda params: {"data": {"department_ids": ["od_root"], "user_ids": ["ou_direct"], "group_ids": []}},
    )
    monkeypatch.setattr(
        feishu_mcp,
        "_run_contact_department_users",
        lambda params, *, department_id: (
            {"data": {"items": []}}
            if department_id == "0"
            else {
                "data": {
                    "items": [
                        {"open_id": "ou_1", "name": "张三"},
                        {"open_id": "ou_direct", "name": "李四"},
                    ]
                }
            }
        ),
    )

    answer = feishu_mcp._execute_cli_contact_organization_snapshot(
        ToolRequest(
            tool_name="feishu_contact_organization_snapshot",
            question="公司有多少人",
            normalized_command="公司有多少人",
            params={"response_format": "raw_json"},
        )
    )
    payload = json.loads(answer)

    assert payload["department_count"] == 1
    assert payload["user_count"] == 2
    assert {item["open_id"] for item in payload["users"]} == {"ou_1", "ou_direct"}


def test_feishu_contact_snapshot_paginates_departments_and_users(monkeypatch) -> None:
    def fake_children(params, *, department_id):
        token = params.get("page_token")
        if department_id == "0" and not token:
            return {
                "data": {
                    "items": [{"department_id": "od_1", "name": "部门一"}],
                    "has_more": True,
                    "page_token": "next_departments",
                }
            }
        if department_id == "0" and token == "next_departments":
            return {"data": {"items": [{"department_id": "od_2", "name": "部门二"}], "has_more": False}}
        return {"data": {"items": [], "has_more": False}}

    def fake_users(params, *, department_id):
        token = params.get("page_token")
        if department_id == "od_1" and not token:
            return {
                "data": {
                    "items": [{"open_id": "ou_1", "name": "张三"}],
                    "has_more": True,
                    "page_token": "next_users",
                }
            }
        if department_id == "od_1" and token == "next_users":
            return {"data": {"items": [{"open_id": "ou_2", "name": "李四"}], "has_more": False}}
        return {"data": {"items": [], "has_more": False}}

    monkeypatch.setattr(feishu_mcp, "_run_contact_department_children", fake_children)
    monkeypatch.setattr(feishu_mcp, "_run_contact_department_users", fake_users)

    answer = feishu_mcp._execute_cli_contact_organization_snapshot(
        ToolRequest(
            tool_name="feishu_contact_organization_snapshot",
            question="公司有多少人",
            normalized_command="公司有多少人",
            params={"response_format": "raw_json"},
        )
    )
    payload = json.loads(answer)

    assert payload["department_count"] == 2
    assert payload["user_count"] == 2
    assert payload["_runtime_v5_snapshot_version"] == 3
    assert {item["open_id"] for item in payload["users"]} == {"ou_1", "ou_2"}


def test_feishu_contact_snapshot_falls_back_to_scope_when_department_children_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        feishu_mcp,
        "_run_contact_department_children",
        lambda params, *, department_id: (_ for _ in ()).throw(RuntimeError("children internal error")),
    )
    monkeypatch.setattr(
        feishu_mcp,
        "_run_contact_scope_list_payload",
        lambda params: {"data": {"department_ids": ["od_sales"], "user_ids": ["ou_direct"], "group_ids": []}},
    )
    monkeypatch.setattr(
        feishu_mcp,
        "_run_contact_department_users",
        lambda params, *, department_id: {"data": {"items": [{"open_id": "ou_1", "name": "张三"}]}},
    )

    answer = feishu_mcp._execute_cli_contact_organization_snapshot(
        ToolRequest(
            tool_name="feishu_contact_organization_snapshot",
            question="公司有多少人",
            normalized_command="公司有多少人",
            params={"response_format": "raw_json"},
        )
    )
    payload = json.loads(answer)

    assert payload["department_count"] == 1
    assert payload["user_count"] == 2
    assert payload["department_fetch_errors"][0]["department_id"] == "0"
    assert {item["open_id"] for item in payload["users"]} == {"ou_1", "ou_direct"}


def test_runtime_v5_rejects_llm_workspace_candidate_for_people_aggregate() -> None:
    candidate = LLMCommandIntentCandidate(
        question_type="query",
        intent="task_query",
        data_scope="company",
        entities={},
        missing_params=(),
        confidence=0.94,
        canonical_question="公司有多少个人",
    )
    rule_intent = IntentResult(
        question_type="query",
        intent="organization_snapshot",
        data_scope="organization",
        entities={"view": "people_aggregate"},
        confidence=0.88,
        canonical_question="公司有多少个人",
    )

    assert validate_llm_command_intent(candidate, rule_intent=rule_intent, force=True) is None


def test_runtime_v5_rejects_llm_workspace_candidate_for_company_profile() -> None:
    candidate = LLMCommandIntentCandidate(
        question_type="query",
        intent="task_query",
        data_scope="company",
        entities={},
        missing_params=(),
        confidence=0.94,
        canonical_question="公司的主营业务是什么",
    )
    rule_intent = IntentResult(
        question_type="query",
        intent="general_query",
        data_scope="company",
        entities={"knowledge_context": "company_profile"},
        confidence=0.82,
        canonical_question="公司的主营业务是什么",
    )

    assert validate_llm_command_intent(candidate, rule_intent=rule_intent, force=True) is None


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
    called = False

    def fake_candidate(**kwargs):
        nonlocal called
        called = True
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

    assert called is False
    assert intent.intent == "task_query"
    assert intent.question_type == "query"
    assert intent.data_scope == "company"
    assert intent.entities["command_frame"]["route_path"] == "conversation_first_v1"
    assert intent.canonical_question == "帮我看看企业工作负荷"


def test_runtime_v5_external_public_info_does_not_route_to_business_tools(monkeypatch) -> None:

    intent = recognize_intent("今天苏州的天气怎么样", _context("今天苏州的天气怎么样"))

    assert intent.intent == "external_information_query"
    assert intent.data_scope == "external"
    assert intent.entities["requires_realtime"] is True
    assert intent.entities["external_category"] == "weather_realtime"
    assert intent.intent != "task_query"
    assert intent.intent != "calendar_query"


def test_runtime_v5_local_realtime_info_does_not_route_to_general_query(monkeypatch) -> None:

    intent = recognize_intent("附近有打印店吗", _context("附近有打印店吗"))

    assert intent.intent == "external_information_query"
    assert intent.data_scope == "external"
    assert intent.entities["external_category"] == "local_realtime"
    assert intent.intent != "general_query"


def test_runtime_v5_company_profile_query_stays_knowledge_when_command_llm_misses(monkeypatch) -> None:

    intent = recognize_intent("公司的主营业务是什么", _context("公司的主营业务是什么"))

    assert intent.intent == "general_query"
    assert intent.entities.get("knowledge_context") == "company_profile"


def test_runtime_v5_process_document_query_stays_knowledge_when_command_llm_misses(monkeypatch) -> None:

    for question in ("报销流程怎么做", "项目资料在哪里"):
        intent = recognize_intent(question, _context(question))

        assert intent.intent == "general_query"
        assert intent.data_scope == "company"
        assert intent.entities.get("knowledge_context") == "general"
        assert intent.entities.get("foundation_route") == "knowledge.general"


def test_runtime_v5_domainless_conversation_does_not_route_to_general_query(monkeypatch) -> None:

    result = run_runtime_v5(context=_context("你是故意重复吗"), providers={})

    assert result.intent.intent == "smalltalk"
    assert result.intent.data_scope == "self"
    assert "公司视角" not in result.composed.answer
    assert "任务查询" not in result.composed.answer


def test_runtime_v5_external_capability_conversation_does_not_route_to_external_query(monkeypatch) -> None:

    for question in (
        "你想联网让你变得更强大一点吗",
        "我跟你聊天，怎么什么都是要联网了呢",
    ):
        intent = recognize_intent(question, _context(question))

        assert intent.intent == "smalltalk"
        assert intent.data_scope == "self"


def test_runtime_v5_external_public_info_reports_boundary_without_generic_fallback(monkeypatch) -> None:

    result = run_runtime_v5(
        context=_context("今天苏州的天气怎么样"),
        providers={},
    )

    assert result.intent.intent == "external_information_query"
    assert result.execution is not None
    assert result.execution.result_context is not None
    assert result.execution.result_context.result_type == "external_information_unavailable"
    assert "外部实时" in result.composed.answer
    assert "你刚才问的是" not in result.composed.answer
    assert "补充一点范围或对象" not in result.composed.answer


def test_runtime_v5_external_public_info_does_not_use_synced_web_provider(monkeypatch) -> None:

    result = run_runtime_v5(
        context=_context("请问今天苏州的天气怎么样"),
        providers={"web": WebProvider(db=None)},
    )

    assert result.intent.intent == "external_information_query"
    assert result.execution is not None
    assert result.execution.provider_results[0].result_type == "external_information_unavailable"
    assert result.execution.provider_results[0].metadata["provider_boundary"] == "external_realtime_not_connected"
    assert "已同步的外部网页资料" not in result.composed.answer


def test_runtime_v5_external_public_info_followup_keeps_external_context(monkeypatch) -> None:
    result_context = ResultContext(
        result_type="external_information_unavailable",
        count=0,
        metadata={
            "strategy": "external_information_query",
            "operation": "external_information_query",
            "external_query": "苏州天气怎么样",
            "execution_status": "error",
        },
    )

    intent = recognize_intent("今天", _context("今天", result_context=result_context))

    assert intent.intent == "external_information_query"
    assert intent.data_scope == "external"
    assert intent.entities["external_query"] == "苏州天气怎么样 今天"


def test_runtime_v5_self_task_query_scope_wins_over_company_wording(monkeypatch) -> None:

    intent = recognize_intent("我问的是需要我处理的任务，不是全公司的", _context("我问的是需要我处理的任务，不是全公司的"))

    assert intent.intent == "task_query"
    assert intent.data_scope == "self"


def test_runtime_v5_self_task_query_recognizes_pending_work_for_me(monkeypatch) -> None:

    intent = recognize_intent("有哪些任务需要我处理的，我感觉有点困了", _context("有哪些任务需要我处理的，我感觉有点困了"))

    assert intent.intent == "task_query"
    assert intent.data_scope == "self"


def test_command_route_observer_flags_conversation_to_business_risk() -> None:
    observation = observe_command_route(
        question="你太机械了",
        intent="task_query",
        question_type="query",
        data_scope="self",
        confidence=0.8,
        route_source="llm_candidate",
    )

    assert observation["misroute_risk"] is True
    assert "conversation_routed_to_business" in observation["risk_reasons"]
    assert observation["denoise_action"] == "prefer_conversation"


def test_runtime_v5_llm_candidate_rejects_conversation_routed_to_business() -> None:
    validated = validate_llm_command_intent(
        LLMCommandIntentCandidate(
            question_type="query",
            intent="task_query",
            data_scope="self",
            confidence=0.92,
            canonical_question="你太机械了",
            reason="mistaken business route",
        ),
        rule_intent=IntentResult(
            question_type="query",
            intent="smalltalk",
            data_scope="self",
            confidence=0.95,
            canonical_question="你太机械了",
        ),
        force=True,
    )

    assert validated is None


def test_runtime_v5_llm_candidate_keeps_self_scope_for_self_workload() -> None:
    validated = validate_llm_command_intent(
        LLMCommandIntentCandidate(
            question_type="query",
            intent="task_query",
            data_scope="company",
            confidence=0.92,
            canonical_question="有哪些任务需要我处理",
            reason="workspace task query",
        ),
        rule_intent=IntentResult(
            question_type="query",
            intent="general_query",
            data_scope="company",
            confidence=0.55,
            canonical_question="有哪些任务需要我处理",
        ),
        force=True,
    )

    assert validated is not None
    assert validated.intent == "task_query"
    assert validated.data_scope == "self"
    assert validated.entities["command_intent_trace"]["route_observation"]["denoise_action"] == "none"


def test_runtime_v5_llm_candidate_cannot_turn_process_knowledge_into_task_query() -> None:
    validated = validate_llm_command_intent(
        LLMCommandIntentCandidate(
            question_type="query",
            intent="task_query",
            data_scope="company",
            confidence=0.92,
            canonical_question="报销流程怎么做",
            reason="process query",
            objective="了解报销流程",
        ),
        rule_intent=IntentResult(
            question_type="query",
            intent="general_query",
            data_scope="company",
            entities={"knowledge_context": "general", "foundation_route": "knowledge.general"},
            confidence=0.84,
            canonical_question="报销流程怎么做",
        ),
        force=True,
    )

    assert validated is None


def test_runtime_v5_records_route_observation_trace(monkeypatch) -> None:
    recorded: list[dict] = []
    monkeypatch.setattr("app.services.runtime_v5.runtime.record_route_observation_trace", lambda chat_id, entry: recorded.append({"chat_id": chat_id, **entry}))

    result = run_runtime_v5(
        context=_context("我跟你聊天，怎么什么都是要联网了呢", chat_id="chat-route"),
        providers={},
    )

    assert result.intent.intent == "smalltalk"
    assert recorded
    assert recorded[-1]["chat_id"] == "chat-route"
    assert recorded[-1]["intent"] == "smalltalk"
    assert recorded[-1]["route_observation"]["denoise_action"] == "none"


def test_runtime_diagnostics_includes_route_observation() -> None:
    envelope = run_runtime_v5(
        context=_context("我跟你聊天，怎么什么都是要联网了呢"),
        providers={},
    )

    summary = runtime_trace_summary(envelope)

    assert summary["route_observation"]["available"] is True
    assert summary["route_observation"]["intent"] == "smalltalk"
    assert summary["route_observation"]["interaction_kind"] == "conversation_feedback"


def test_route_observation_summary_counts_misroute_risks() -> None:
    summary = route_observation_summary(
        [
            {"misroute_risk": False, "denoise_action": "none", "risk_reasons": []},
            {"misroute_risk": True, "denoise_action": "prefer_conversation", "risk_reasons": ["conversation_routed_to_business"]},
            {"misroute_risk": True, "denoise_action": "prefer_conversation", "risk_reasons": ["conversation_routed_to_business"]},
        ]
    )

    assert summary["status"] == "needs_attention"
    assert summary["total_count"] == 3
    assert summary["risky_count"] == 2
    assert summary["risk_reasons"]["conversation_routed_to_business"] == 2


def test_runtime_v5_low_confidence_business_query_uses_contextual_clarification() -> None:
    intent = IntentResult(
        question_type="query",
        intent="task_query",
        data_scope="department",
        missing_params=(),
        confidence=0.52,
        canonical_question="看看情况",
    )

    guide = build_clarification_guide(context=_context("看看情况"), intent=intent, fallback="")

    assert "我还没对准要查的范围" in guide.prompt
    assert "这个问题我还不够确定" not in guide.prompt
    assert "补充一点范围或对象" not in guide.prompt


def test_runtime_v5_low_confidence_action_clarification_does_not_use_generic_fallback() -> None:
    answer = compose_answer(
        context=_context("帮我处理一下"),
        intent=IntentResult(
            question_type="action",
            intent="message_send",
            data_scope="self",
            missing_params=(),
            confidence=0.5,
            canonical_question="帮我处理一下",
        ),
        permission=PermissionDecision(allowed=True),
        execution=None,
    )

    assert "先不执行动作" in answer.answer
    assert "这个问题我还不够确定" not in answer.answer


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


def test_runtime_v5_confident_operational_query_does_not_block_on_command_llm(monkeypatch) -> None:
    called = False

    def fake_candidate(**kwargs):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr("app.services.runtime_v5.llm_intent.llm_command_intent_candidate", fake_candidate)

    intent = recognize_intent("我的任务", _context("我的任务"))

    assert called is False
    assert intent.intent == "task_query"
    assert intent.data_scope == "self"
    assert intent.question_type == "query"


def test_runtime_v5_command_llm_understands_precise_business_query(monkeypatch) -> None:
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

    assert called is False
    assert intent.intent == "task_query"
    assert intent.data_scope == "company"
    assert intent.entities["command_frame"]["route_path"] == "conversation_first_v1"


def test_command_plan_always_includes_command_frame_for_rule_query() -> None:
    command_plan = build_command_plan(context=_context("全公司任务"))

    assert command_plan.command_frame is not None
    assert command_plan.command_frame.intent == "task_query"
    assert command_plan.command_frame.dialogue_mode == "present"
    assert command_plan.command_frame.scope == "company"
    assert command_plan.command_frame.domain == "Workspace"
    assert command_plan.command_frame.skill_intent == "task_query"
    assert command_plan.command_frame.context_mode == "new_question"
    assert command_plan.command_frame.action_type == "read"
    assert command_plan.command_frame.safety_level == "low"
    assert command_plan.command_frame.gates["utterance"]["type"] == "business_query"
    assert command_plan.command_frame.gates["domain"]["domain"] == "Workspace"
    assert command_plan.command_frame.gates["scope"]["scope"] == "company"
    assert command_plan.intent_result.entities["command_frame"]["route_path"] == "conversation_first_v1"


def test_command_plan_uses_domain_query_presentation_hint_for_text_answers() -> None:
    command_plan = build_command_plan(context=_context("王庆威的手机号是多少"))

    assert command_plan.command_frame is not None
    assert command_plan.command_frame.intent == "people_lookup"
    assert command_plan.command_frame.params["domain_query"]["presentation_hint"] == "text"
    assert command_plan.command_frame.response_intent["should_render_card"] is False


def test_command_plan_always_includes_command_frame_for_conversation() -> None:
    command_plan = build_command_plan(context=_context("你好"))

    assert command_plan.command_frame is not None
    assert command_plan.command_frame.intent == "smalltalk"
    assert command_plan.command_frame.dialogue_mode == "answer"
    assert command_plan.command_frame.utterance_type == "conversation"
    assert command_plan.command_frame.gates["utterance"]["type"] == "conversation"
    assert command_plan.command_frame.gates["domain"]["domain"] == "Conversation"
    assert command_plan.command_frame.gates["safety"]["level"] == "low"
    assert command_plan.command_frame.response_intent["intro_intent"] == "natural_reply"


def test_command_plan_intent_layers_classify_people_context_action() -> None:
    result_context = ResultContext(
        result_type="organization_snapshot",
        count=2,
        items=(
            {"name": "张三", "open_id": "ou_zhang"},
            {"name": "李四", "open_id": "ou_li"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="上一轮通讯录结果。",
    )

    command_plan = build_command_plan(
        context=_context(
            "用机器人发给这些人说：明天上午提交周报",
            result_context=result_context,
        )
    )

    assert command_plan.command_frame is not None
    assert command_plan.command_frame.intent == "message_send"
    assert command_plan.command_frame.domain == "Communication"
    assert command_plan.command_frame.context_mode == "action_on_people_context"
    assert command_plan.command_frame.action_type == "send"
    assert command_plan.command_frame.safety_level == "high"
    assert command_plan.command_frame.gates["context"]["result_type"] == "organization_snapshot"
    assert command_plan.command_frame.gates["action"]["target"]["people_target_count"] == "2"
    assert command_plan.command_frame.gates["action"]["operation_kind"] == "send"
    assert command_plan.command_frame.gates["action"]["execution_mode"] == "bot_notify"
    assert command_plan.command_frame.gates["action"]["target_source"] == "people_result_context"
    assert command_plan.command_frame.gates["action"]["confirmation_hint"] == "confirm_before_send"
    assert command_plan.command_frame.gates["action"]["credential_hint"] == "depends_on_delivery_mode"
    assert command_plan.command_frame.gates["safety"]["confirmation_expected"] is True
    assert "uses_people_result_context" in command_plan.command_frame.gates["safety"]["risk_reasons"]
    assert "multiple_people_targets" in command_plan.command_frame.gates["safety"]["risk_reasons"]
    assert command_plan.command_frame.route_path == "conversation_first_v1"
    assert command_plan.command_frame.gates["route"]["path"] == "conversation_first_v1"
    assert command_plan.intent_result.entities["command_intent_trace"]["reason"] == "communication_send_action"


@pytest.mark.parametrize(
    ("message", "execution_mode"),
    (
        ("用机器人发给这些人说：明天上午提交周报", "bot_notify"),
        ("替我分别发给这些人说：明天上午提交周报", "user_delegated_send"),
        ("拉群后发给这些人说：明天上午提交周报", "create_group_then_send"),
    ),
)
def test_command_plan_intent_layers_distinguish_people_context_delivery_modes(
    message: str,
    execution_mode: str,
) -> None:
    result_context = ResultContext(
        result_type="organization_snapshot",
        count=2,
        items=(
            {"name": "张三", "open_id": "ou_zhang"},
            {"name": "李四", "open_id": "ou_li"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="上一轮通讯录结果。",
    )

    command_plan = build_command_plan(context=_context(message, result_context=result_context))

    assert command_plan.command_frame is not None
    assert command_plan.command_frame.intent == "message_send"
    assert command_plan.command_frame.gates["action"]["target_source"] == "people_result_context"
    assert command_plan.command_frame.gates["action"]["execution_mode"] == execution_mode
    assert command_plan.command_frame.gates["action"]["confirmation_hint"] == "confirm_before_send"


def test_command_plan_candidate_arbiter_keeps_send_action_missing_delivery_mode() -> None:
    result_context = ResultContext(
        result_type="department_members",
        count=2,
        items=(
            {"name": "张三", "open_id": "ou_zhang"},
            {"name": "李四", "open_id": "ou_li"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="上一轮人员结果。",
    )

    command_plan = build_command_plan(
        context=_context(
            "给这些人发消息说：明天上午提交周报",
            result_context=result_context,
        )
    )

    assert command_plan.intent_result.intent == "message_send"
    assert command_plan.intent_result.missing_params == ("delivery_mode",)
    assert command_plan.command_frame is not None
    assert command_plan.command_frame.route_path == "conversation_first_v1"
    assert command_plan.command_frame.gates["action"]["confirmation_hint"] == "clarify_delivery_mode"
    assert command_plan.command_frame.gates["safety"]["blocks_execution"] is True


def test_command_plan_action_gate_distinguishes_missing_delivery_mode_for_people_context() -> None:
    result_context = ResultContext(
        result_type="people_search",
        count=2,
        items=(
            {"name": "张三", "open_id": "ou_zhang"},
            {"name": "李四", "open_id": "ou_li"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="上一轮通讯录结果。",
    )

    command_plan = build_command_plan(
        context=_context(
            "把这些人发消息说：明天上午提交周报",
            result_context=result_context,
        )
    )

    assert command_plan.command_frame is not None
    action_gate = command_plan.command_frame.gates["action"]
    safety_gate = command_plan.command_frame.gates["safety"]
    assert action_gate["operation_kind"] == "send"
    assert action_gate["execution_mode"] == "unresolved_delivery_mode"
    assert action_gate["confirmation_hint"] == "clarify_delivery_mode"
    assert safety_gate["blocks_execution"] is True
    assert "delivery_mode_unresolved" in safety_gate["risk_reasons"]


def test_command_plan_action_gate_marks_mail_to_people_as_user_draft_not_send() -> None:
    result_context = ResultContext(
        result_type="people_search",
        count=2,
        items=(
            {"name": "张三", "open_id": "ou_zhang", "email": "zhangsan@example.com"},
            {"name": "李四", "open_id": "ou_li", "email": "lisi@example.com"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="上一轮通讯录结果。",
    )

    command_plan = build_command_plan(
        context=_context(
            "给这些人写封邮件，主题：周报提醒 正文：明天上午提交周报",
            result_context=result_context,
        )
    )

    assert command_plan.command_frame is not None
    action_gate = command_plan.command_frame.gates["action"]
    safety_gate = command_plan.command_frame.gates["safety"]
    assert command_plan.command_frame.action_type == "draft"
    assert command_plan.command_frame.safety_level == "medium"
    assert action_gate["operation_kind"] == "draft"
    assert action_gate["execution_mode"] == "user_draft"
    assert action_gate["target_source"] == "people_result_context"
    assert action_gate["confirmation_hint"] == "confirm_draft_creation"
    assert action_gate["credential_hint"] == "user_token_required"
    assert safety_gate["confirmation_expected"] is True
    assert "draft_creates_external_artifact" in safety_gate["risk_reasons"]


def test_command_plan_context_gate_distinguishes_followup_from_new_people_question() -> None:
    result_context = ResultContext(
        result_type="organization_snapshot",
        count=2,
        items=(
            {"name": "张三", "title": "后端工程师"},
            {"name": "李四", "title": "财务"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="上一轮通讯录结果。",
    )

    followup_plan = build_command_plan(context=_context("哪些是工程师", result_context=result_context))
    new_question_plan = build_command_plan(context=_context("王悦的电话号码是多少", result_context=result_context))

    assert followup_plan.command_frame is not None
    assert followup_plan.command_frame.context_mode == "inherit_result_context"
    assert followup_plan.command_frame.gates["context"]["mode"] == "inherit_result_context"
    assert new_question_plan.command_frame is not None
    assert new_question_plan.intent_result.intent == "people_lookup"
    assert new_question_plan.command_frame.context_mode == "new_question"
    assert new_question_plan.command_frame.gates["route"]["foundation_route"] == "people.person"
    assert new_question_plan.command_frame.params["domain_query"]["domain"] == "people"
    assert new_question_plan.command_frame.params["domain_query"]["fields"] == ["mobile"]
    assert new_question_plan.command_frame.params["domain_query"]["output_mode"] == "answer"


def test_command_plan_domain_gate_records_reason_source_and_confidence(monkeypatch) -> None:

    cases = (
        ("公司财务部门有多少人？", "People", "foundation_route:people.department_members", "foundation_rule"),
        ("公司是做什么的", "Knowledge", "foundation_route:knowledge.company_profile", "foundation_rule"),
        ("报销流程怎么做", "Knowledge", "foundation_route:knowledge.general", "foundation_rule"),
        ("我有多少封邮件", "Communication", "foundation_route:communication.mail", "foundation_rule"),
        ("我的任务", "Workspace", "workspace_signal", "rule"),
        ("你是谁", "Conversation", "conversation_boundary", "rule"),
        ("/system diagnostics", "System", "explicit_command:observability", "explicit_command"),
    )
    for question, domain, reason, source in cases:
        command_plan = build_command_plan(context=_context(question))
        assert command_plan.command_frame is not None
        domain_gate = command_plan.command_frame.gates["domain"]
        assert domain_gate["domain"] == domain
        assert domain_gate["reason"] == reason
        assert domain_gate["source"] == source
        assert domain_gate["confidence"] == command_plan.intent_result.confidence


def test_command_plan_scope_gate_records_target_and_resource_boundary(monkeypatch) -> None:

    cases = (
        ("公司财务部门有多少人？", "department", "department_resource_signal", "enterprise_directory", "department"),
        ("王悦的电话号码是多少", "person", "person_resource_signal", "enterprise_directory", "person"),
        ("公司有多少人", "organization", "organization_resource_signal", "enterprise_directory", "organization"),
        ("公司是做什么的", "company", "company_knowledge_signal", "enterprise_knowledge", "company"),
        ("我有多少封邮件", "self", "personal_mailbox_signal", "personal_mailbox", "self"),
        ("今天美国总统有什么新闻", "external", "external_information_signal", "public_information", "external"),
    )
    for question, scope, reason, boundary, target_type in cases:
        command_plan = build_command_plan(context=_context(question))
        assert command_plan.command_frame is not None
        scope_gate = command_plan.command_frame.gates["scope"]
        assert scope_gate["scope"] == scope
        assert scope_gate["requested_scope"] == command_plan.intent_result.data_scope
        assert scope_gate["resolved_scope"] == scope
        assert scope_gate["reason"] == reason
        assert scope_gate["resource_boundary"] == boundary
        assert scope_gate["target"]["type"] == target_type


def test_runtime_v5_plain_smalltalk_does_not_spend_command_llm(monkeypatch) -> None:
    called = False

    def fake_candidate(**kwargs):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr("app.services.runtime_v5.llm_intent.llm_command_intent_candidate", fake_candidate)

    intent = recognize_intent("你好", _context("你好"))

    assert called is False
    assert intent.intent == "smalltalk"


def test_runtime_v5_contextual_short_followup_keeps_recent_external_context(monkeypatch) -> None:
    result_context = ResultContext(
        result_type="external_information_unavailable",
        count=0,
        metadata={
            "strategy": "external_information_query",
            "operation": "external_information_query",
            "external_query": "苏州天气怎么样",
            "execution_status": "error",
        },
    )

    intent = recognize_intent("今天", _context("今天", chat_id="chat_weather_followup", result_context=result_context))

    assert intent.intent == "external_information_query"
    assert intent.data_scope == "external"


def test_runtime_v5_contextual_scope_correction_keeps_task_self_scope(monkeypatch) -> None:

    intent = recognize_intent("我问的是需要我处理的任务，不是全公司的", _context("我问的是需要我处理的任务，不是全公司的", chat_id="chat_task_scope"))

    assert intent.intent == "task_query"
    assert intent.data_scope == "self"


def test_runtime_v5_non_work_personal_service_does_not_route_to_task(monkeypatch) -> None:

    for message in ("有需要我帮忙的吗", "我有点饿了", "帮我订一下吃的"):
        intent = recognize_intent(message, _context(message))
        assert intent.intent == "smalltalk"
        assert intent.data_scope == "self"


def test_runtime_v5_contextual_business_rejection_returns_conversation(monkeypatch) -> None:

    intent = recognize_intent("我说的是别的，不是任务", _context("我说的是别的，不是任务", chat_id="chat_reject_task_context"))

    assert intent.intent == "smalltalk"
    assert intent.data_scope == "self"


def test_runtime_v5_clarification_does_not_leak_internal_param_names() -> None:
    answer = compose_answer(
        context=_context("帮你转我一下"),
        intent=IntentResult(
            question_type="query",
            intent="task_query",
            data_scope="self",
            missing_params=("task_query_criteria",),
            confidence=0.5,
        ),
        permission=PermissionDecision(allowed=True),
        execution=None,
    )

    assert "task_query_criteria" not in answer.answer
    assert "任务范围或条件" in answer.answer


def test_runtime_v5_command_llm_carries_profile_update_candidate() -> None:
    rule_intent = IntentResult(
        question_type="query",
        intent="smalltalk",
        data_scope="self",
        confidence=0.4,
    )

    validated = validate_llm_command_intent(
        LLMCommandIntentCandidate(
            question_type="query",
            intent="smalltalk",
            data_scope="self",
            confidence=0.91,
            canonical_question="以后不要直呼姓名",
            draft_response_hint="明白，以后我会按你的偏好称呼。",
            profile_update={
                "preferred_address": "陈总",
                "avoid_direct_name": True,
                "tone_tips": "更直接一点",
                "role": "owner",
            },
        ),
        rule_intent=rule_intent,
    )

    assert validated is not None
    assert validated.entities["profile_update_candidate"] == {
        "preferred_address": "陈总",
        "avoid_direct_name": True,
        "tone_tips": "更直接一点",
    }
    assert validated.entities["command_frame"]["profile_update"]["preferred_address"] == "陈总"


def test_runtime_v5_told_you_context_question_stays_conversation_when_llm_unavailable(monkeypatch) -> None:
    called = False

    def fake_candidate(**kwargs):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr("app.services.runtime_v5.llm_intent.llm_command_intent_candidate", fake_candidate)

    intent = recognize_intent("我刚才不是告诉你，你的外号是大飞哥吗", _context("我刚才不是告诉你，你的外号是大飞哥吗"))

    assert called is False
    assert intent.intent == "smalltalk"


def test_runtime_v5_company_owner_question_stays_conversation_without_execution(monkeypatch) -> None:
    called = False

    def fake_candidate(**kwargs):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr("app.services.runtime_v5.llm_intent.llm_command_intent_candidate", fake_candidate)

    result = run_runtime_v5(
        context=_context("这个公司的老板是谁", display_name="陈俊"),
        providers={},
    )

    assert called is False
    assert result.intent.intent == "smalltalk"
    assert result.execution is not None
    assert result.execution.status == "skipped"
    assert result.composed.answer


def test_runtime_v5_owner_correction_stays_conversation_without_execution(monkeypatch) -> None:
    called = False

    def fake_candidate(**kwargs):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr("app.services.runtime_v5.llm_intent.llm_command_intent_candidate", fake_candidate)

    result = run_runtime_v5(
        context=_context("我是老板，你忘记了", display_name="陈俊"),
        providers={},
    )

    assert called is False
    assert result.intent.intent == "smalltalk"
    assert result.execution is not None
    assert result.execution.status == "skipped"
    assert result.composed.answer


def test_runtime_v5_self_role_question_stays_conversation_without_execution(monkeypatch) -> None:
    called = False

    def fake_candidate(**kwargs):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr("app.services.runtime_v5.llm_intent.llm_command_intent_candidate", fake_candidate)

    result = run_runtime_v5(
        context=_context("你知道我现在在这个公司的职位吗", display_name="陈俊", department_names=("管理层",), job_title="CEO"),
        providers={},
    )

    assert called is False
    assert result.intent.intent == "smalltalk"
    assert result.execution is not None
    assert result.execution.status == "skipped"
    assert result.composed.answer


def test_runtime_v5_forced_command_llm_timeout_degrades_without_provider_route(monkeypatch) -> None:
    def slow_candidate(**kwargs):
        time.sleep(0.05)
        return None

    monkeypatch.setattr("app.services.runtime_v5.llm_intent.llm_command_intent_candidate", slow_candidate)
    monkeypatch.setattr("app.services.runtime_v5.llm_intent.llm_route_for_task", lambda task_type: type("Route", (), {"latency_budget_ms": 1})())

    intent = recognize_intent("这个公司谁是老板", _context("这个公司谁是老板"))

    assert intent.intent == "smalltalk"
    assert intent.entities["command_frame"]["route_path"] == "conversation_first_v1"


def test_runtime_v5_explicit_slash_command_bypasses_natural_language_rules() -> None:
    intent = recognize_intent("/system diagnostics", _context("/system diagnostics"))

    assert intent.intent == "runtime_status"
    assert intent.confidence == 1.0
    assert intent.entities["command_frame"]["route_path"] == "explicit_command"
    assert intent.entities["explicit_command"]["family"] == "observability"

    command_plan = build_command_plan(context=_context("/system diagnostics"))
    assert command_plan.command_frame is not None
    assert command_plan.command_frame.utterance_type == "explicit_command"
    assert command_plan.command_frame.gates["utterance"]["type"] == "explicit_command"
    assert command_plan.command_frame.gates["route"]["path"] == "explicit_command"


def test_runtime_v5_explicit_command_guard_covers_governance_policy_and_unknown() -> None:
    governance = recognize_intent("/capability registry", _context("/capability registry"))
    policy = recognize_intent("/policy identity", _context("/policy identity"))
    unknown = recognize_intent("/whatever", _context("/whatever"))

    assert governance.intent == "governance_view"
    assert governance.entities["explicit_command"]["family"] == "governance"
    assert policy.intent == "runtime_status"
    assert policy.entities["explicit_command"]["family"] == "policy"
    assert unknown.intent == "smalltalk"
    assert unknown.entities["explicit_command"]["family"] == "unknown"
    assert "可用显式命令" in unknown.entities["fallback_answer"]


def test_runtime_v5_smalltalk_composer_answers_job_title_fact_without_provider() -> None:
    result = run_runtime_v5(
        context=_context("你知道我在这个公司的职位吗", display_name="陈俊", department_names=("管理层",), job_title="CEO"),
        providers={},
    )

    assert result.intent.intent == "smalltalk"
    assert result.execution is not None
    assert result.execution.status == "skipped"
    assert "逐步了解你" in result.composed.answer or "直接告诉我" in result.composed.answer


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


def test_runtime_v5_command_llm_candidate_uses_command_intent_route(monkeypatch) -> None:
    class FakeGateway:
        def complete_task_text(self, prompt: str, *, task_type: str, temperature: float = 0.2) -> str:
            assert task_type == "command_intent"
            assert temperature == 0.0
            return """
            {
              "question_type": "query",
              "intent": "task_query",
              "data_scope": "company",
              "confidence": 0.91,
              "canonical_question": "查看公司任务"
            }
            """

    monkeypatch.setattr("app.services.runtime_v5.llm_intent._running_tests", lambda: False)
    monkeypatch.setattr("app.services.runtime_v5.llm_intent.settings.bot_llm_semantics_enabled", True)
    monkeypatch.setattr("app.services.runtime_v5.llm_intent.LLMGateway", lambda: FakeGateway())

    candidate = llm_command_intent_candidate(
        question="帮我看看公司任务",
        context=_context("帮我看看公司任务"),
        rule_intent=IntentResult(
            question_type="query",
            intent="general_query",
            data_scope="company",
            confidence=0.5,
            canonical_question="帮我看看公司任务",
        ),
    )

    assert candidate is not None
    assert candidate.intent == "task_query"
    assert candidate.data_scope == "company"


def test_runtime_v5_command_prompt_includes_context_but_stays_compact() -> None:
    prompt = _prompt(
        question="系统为什么这么慢",
        context=_context("系统为什么这么慢"),
        rule_intent=IntentResult(
            question_type="query",
            intent="general_analysis",
            data_scope="company",
            confidence=0.55,
            canonical_question="系统为什么这么慢",
        ),
    )
    audit = prompt_audit_payload(prompt=prompt, task_type="command_intent", lane="foreground_fast")

    assert audit["prompt_chars"] < 3600
    assert audit["line_count"] < 80
    assert audit["has_intent_profile_context"] is True
    assert audit["has_session_context"] is True
    assert "prompt_long" not in audit["risks"]
    assert "command_with_session_context" not in audit["risks"]


def test_runtime_v5_command_llm_trace_includes_model_route() -> None:
    record_llm_call_trace(
        {
            "task_type": "command_intent",
            "provider": "deepseek_api",
            "model": "deepseek-chat",
            "duration_ms": 12,
            "allow_fallback": False,
            "fallback_used": False,
            "status": "success",
        }
    )
    validated = validate_llm_command_intent(
        LLMCommandIntentCandidate(
            question_type="query",
            intent="task_query",
            data_scope="company",
            confidence=0.91,
            canonical_question="查看公司任务",
        ),
        rule_intent=IntentResult(
            question_type="query",
            intent="general_query",
            data_scope="company",
            confidence=0.5,
            canonical_question="查看公司任务",
        ),
    )

    assert validated is not None
    llm_call = validated.entities["command_intent_trace"]["llm_call"]
    assert llm_call["task_type"] == "command_intent"
    assert llm_call["provider"] == "deepseek_api"
    assert llm_call["model"] == "deepseek-chat"
    assert llm_call["allow_fallback"] is False


def test_runtime_diagnostics_includes_llm_trace_summary() -> None:
    llm_call = record_llm_call_trace(
        {
            "task_type": "command_intent",
            "lane": "foreground_fast",
            "provider": "deepseek_api",
            "model": "deepseek-chat",
            "duration_ms": 23,
            "latency_budget_ms": 6000,
            "allow_fallback": False,
            "fallback_used": False,
            "status": "success",
            "prompt_chars": 120,
            "response_chars": 60,
            "prompt_audit": {
                "prompt_chars": 120,
                "line_count": 12,
                "has_profile_context": False,
                "has_session_context": False,
                "has_original_answer": False,
                "risk_count": 0,
                "risks": [],
            },
        }
    )
    envelope = SimpleNamespace(
        intent=IntentResult(
            question_type="query",
            intent="task_query",
            data_scope="company",
            entities={
                "command_intent_trace": {
                    "source": "llm",
                    "llm_call": llm_call,
                }
            },
            confidence=0.91,
            canonical_question="查看公司任务",
        ),
        plan=PlannerResult(strategy="task_query", sources=("task",)),
        permission=PermissionDecision(allowed=True, requires_confirmation=False, execution_identity="bot"),
        execution=ExecutionResult(strategy="task_query", status="skipped", provider_results=()),
        composed=ComposedAnswer(answer="ok", result_context=ResultContext(result_type="task_list", count=0)),
        context=_context("查看公司任务"),
    )

    summary = runtime_trace_summary(envelope)

    llm_summary = summary["llm_trace_summary"]
    assert llm_summary["available"] is True
    assert llm_summary["call_count"] == 1
    assert llm_summary["total_duration_ms"] == 23
    assert llm_summary["fallback_used"] is False
    assert llm_summary["providers"] == ["deepseek_api"]
    assert llm_summary["lanes"] == ["foreground_fast"]
    assert llm_summary["task_types"] == ["command_intent"]
    assert llm_summary["latest"]["lane"] == "foreground_fast"
    assert llm_summary["latest"]["model"] == "deepseek-chat"
    assert llm_summary["latest"]["prompt_chars"] == 120
    assert llm_summary["latest"]["prompt_audit"]["line_count"] == 12


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
    assert validated.entities["command_intent_trace"]["source"] == "llm"
    assert validated.entities["command_intent_trace"]["final_intent"] == "task_query"


def test_runtime_v5_smalltalk_composer_answers_identity_without_provider() -> None:
    result = run_runtime_v5(
        context=_context("你知道我是谁吗", display_name="陈俊"),
        providers={},
    )

    assert result.intent.intent == "smalltalk"
    assert result.execution is not None
    assert result.execution.status == "skipped"
    assert "逐步了解你" in result.composed.answer or "直接告诉我" in result.composed.answer


def test_runtime_v5_smalltalk_composer_answers_name_question_without_llm() -> None:
    result = run_runtime_v5(
        context=_context("我叫什么名字", display_name="陈俊"),
        providers={},
    )

    assert result.intent.intent == "smalltalk"
    assert result.execution is not None
    assert result.execution.status == "skipped"
    assert "逐步了解你" in result.composed.answer or "直接告诉我" in result.composed.answer


def test_runtime_v5_smalltalk_composer_enriches_identity_with_people_profile() -> None:
    result = run_runtime_v5(
        context=_context("我是谁", display_name="王敏", department_names=("销售部",), job_title="销售经理"),
        providers={},
    )

    assert result.intent.intent == "smalltalk"
    assert result.execution is not None
    assert result.execution.status == "skipped"
    assert "逐步了解你" in result.composed.answer or "直接告诉我" in result.composed.answer


def test_runtime_v5_smalltalk_composer_answers_owner_identity_without_llm() -> None:
    result = run_runtime_v5(
        context=_context("我是老板吗", display_name="陈俊"),
        providers={},
    )

    assert result.intent.intent == "smalltalk"
    assert result.execution is not None
    assert result.execution.status == "skipped"
    assert "逐步了解你" in result.composed.answer or "直接告诉我" in result.composed.answer


def test_runtime_v5_smalltalk_composer_answers_assistant_alias_naturally() -> None:
    result = run_runtime_v5(
        context=_context("你是大飞哥"),
        providers={},
    )

    assert result.intent.intent == "smalltalk"
    assert result.composed.answer == "对，我就是大飞哥，也就是 Digital Advisor。你可以把我当成企业数字参谋，不是普通聊天机器人。"


def test_runtime_v5_smalltalk_composer_answers_preferred_address() -> None:
    result = run_runtime_v5(
        context=_context("你应该叫我什么", display_name="陈俊"),
        providers={},
    )

    assert result.intent.intent == "smalltalk"
    assert "逐步了解你" in result.composed.answer or "直接告诉我" in result.composed.answer


def test_runtime_v5_smalltalk_composer_answers_owner_address_preference_naturally(monkeypatch) -> None:
    called = False

    def fake_candidate(**kwargs):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr("app.services.runtime_v5.llm_intent.llm_command_intent_candidate", fake_candidate)

    result = run_runtime_v5(
        context=_context("可以不直接叫我名字吗，我是你老板呢", display_name="陈俊"),
        providers={},
    )

    assert called is False
    assert result.intent.intent == "smalltalk"
    assert result.execution is not None
    assert result.execution.status == "skipped"
    assert result.composed.answer


def test_runtime_v5_smalltalk_composer_answers_owner_statement_naturally(monkeypatch) -> None:
    called = False

    def fake_candidate(**kwargs):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr("app.services.runtime_v5.llm_intent.llm_command_intent_candidate", fake_candidate)

    result = run_runtime_v5(
        context=_context("我是这个公司的老板", display_name="陈俊"),
        providers={},
    )

    assert called is False
    assert result.intent.intent == "smalltalk"
    assert result.execution is not None
    assert result.execution.status == "skipped"
    assert result.composed.answer


def test_runtime_v5_smalltalk_owner_greeting_has_warmth() -> None:
    result = run_runtime_v5(
        context=_context("你好", display_name="陈俊"),
        providers={},
    )

    assert result.intent.intent == "smalltalk"
    assert result.composed.answer == "在的。你直接说要看什么或想聊什么就行。"


def test_runtime_v5_smalltalk_after_people_context_does_not_inherit_people_domain() -> None:
    result_context = ResultContext(
        result_type="department_members",
        count=3,
        items=({"name": "产品部"}, {"name": "销售部"}, {"name": "项目部"}),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="半导体事业部下面有 3 个直属子部门：产品部、销售部、项目部。",
    )

    result = run_runtime_v5(
        context=_context("你好，大飞哥", display_name="陈俊", result_context=result_context),
        providers={},
    )

    assert result.intent.intent == "smalltalk"
    assert "通讯录" not in result.composed.answer
    assert "47" not in result.composed.answer
    assert result.execution is None or result.execution.status == "skipped"


def test_runtime_v5_smalltalk_composer_answers_profile_boundary() -> None:
    result = run_runtime_v5(
        context=_context("我是什么性格", display_name="陈俊"),
        providers={},
    )

    assert result.intent.intent == "smalltalk"
    assert "直接、系统性、少绕弯子" in result.composed.answer


def test_runtime_v5_smalltalk_composer_answers_external_capability_feedback() -> None:
    result = run_runtime_v5(
        context=_context("我跟你聊天，怎么什么都是要联网了呢"),
        providers={},
    )

    assert result.intent.intent == "smalltalk"
    assert "普通聊天" in result.composed.answer
    assert "外部事实" in result.composed.answer


def test_runtime_v5_general_chat_uses_conversation_llm_without_business_route(monkeypatch) -> None:
    calls = []

    def fake_conversation_reply(context):
        calls.append(context)
        return "宋朝的开国皇帝是赵匡胤。"

    monkeypatch.setattr("app.services.runtime_v5.composer.conversation_llm_reply", fake_conversation_reply)

    result = run_runtime_v5(
        context=_context("大飞哥，宋朝的开国皇帝是谁"),
        providers={},
    )

    assert result.intent.intent == "smalltalk"
    assert result.plan.sources == ()
    assert result.execution is not None
    assert result.execution.status == "skipped"
    assert result.composed.answer == "宋朝的开国皇帝是赵匡胤。"
    assert calls


def test_runtime_v5_public_person_chat_does_not_fall_into_people_count(monkeypatch) -> None:
    def fake_conversation_reply(context):
        return "认识这个名字。马云是阿里巴巴的创始人之一。"

    monkeypatch.setattr("app.services.runtime_v5.composer.conversation_llm_reply", fake_conversation_reply)

    result = run_runtime_v5(
        context=_context("马云认识吗"),
        providers={},
    )

    assert result.intent.intent == "smalltalk"
    assert result.plan.sources == ()
    assert result.execution is not None
    assert result.execution.status == "skipped"
    assert result.composed.answer == "认识这个名字。马云是阿里巴巴的创始人之一。"
    assert "47" not in result.composed.answer


def test_runtime_v5_llm_semantic_arbitrates_open_questions_before_people_hint(monkeypatch) -> None:
    class FakeSemanticGateway:
        def complete_task_text(self, prompt: str, *, task_type: str, temperature: float = 0.2) -> str:
            assert task_type == "command_intent"
            assert temperature == 0.0
            return """
            {
              "speech_act": "ask",
              "topic": "conversation",
              "operation": "ask",
              "requested_output": "natural_text",
              "target": {"kind": "unknown"},
              "parameters": {},
              "confidence": 0.93,
              "ambiguities": []
            }
            """

    def fake_conversation_reply(context):
        if "宋朝" in context.question:
            return "宋朝的开国皇帝是赵匡胤。"
        return "认识这个名字。马云是阿里巴巴的创始人之一。"

    monkeypatch.setattr("app.services.runtime_v5.semantic_understanding._running_tests", lambda: False)
    monkeypatch.setattr("app.services.runtime_v5.semantic_understanding.settings.bot_llm_semantics_enabled", True)
    monkeypatch.setattr("app.services.runtime_v5.semantic_understanding.LLMGateway", lambda: FakeSemanticGateway())
    monkeypatch.setattr("app.services.runtime_v5.composer.conversation_llm_reply", fake_conversation_reply)

    for question in ("马云是谁", "宋朝开国皇帝是谁"):
        result = run_runtime_v5(
            context=_context(question),
            providers={},
        )

        assert result.intent.intent == "smalltalk"
        assert result.plan.sources == ()
        assert result.execution is not None
        assert result.execution.status == "skipped"
        assert "47" not in result.composed.answer


def test_runtime_v5_llm_previous_result_presentation_inherits_filters_without_confirmation(monkeypatch) -> None:
    result_context = ResultContext(
        result_type="organization_snapshot",
        count=24,
        items=({"name": "王云飞", "gender_normalized": "male"}, {"name": "吴健", "gender_normalized": "male"}),
        metadata={
            "context_kind": "query_result",
            "entity_domain": "People",
            "people_filter": {"filter": "gender", "value": "male"},
            "field_projection": "name_only",
            "result_context_presentation": "summary",
        },
        answer="目前能确认的男性员工是 24 位。",
    )

    class FakeSemanticGateway:
        def complete_task_text(self, prompt: str, *, task_type: str, temperature: float = 0.2) -> str:
            assert task_type == "command_intent"
            assert temperature == 0.0
            return """
            {
              "speech_act": "request_action",
              "topic": "people",
              "operation": "list",
              "requested_output": "full_list",
              "target": {"kind": "previous_result"},
              "parameters": {},
              "confidence": 0.9,
              "ambiguities": []
            }
            """

    monkeypatch.setattr("app.services.runtime_v5.semantic_understanding._running_tests", lambda: False)
    monkeypatch.setattr("app.services.runtime_v5.semantic_understanding.settings.bot_llm_semantics_enabled", True)
    monkeypatch.setattr("app.services.runtime_v5.semantic_understanding.LLMGateway", lambda: FakeSemanticGateway())

    command_plan = build_command_plan(context=_context("你帮我把明细直接发出来", result_context=result_context))

    assert command_plan.intent == "organization_snapshot"
    assert command_plan.intent_result.question_type == "query"
    assert command_plan.command_frame is not None
    assert command_plan.command_frame.dialogue_mode == "present"
    assert command_plan.command_frame.context_mode == "inherit_result_context"
    assert command_plan.command_frame.params["domain_query"]["operation_kind"] == "read"
    assert command_plan.command_frame.params["domain_query"]["filters"] == {"gender": "male"}


def test_runtime_v5_rejects_llm_topic_drift_from_explicit_approval_anchor(monkeypatch) -> None:
    class FakeSemanticGateway:
        def complete_task_text(self, prompt: str, *, task_type: str, temperature: float = 0.2) -> str:
            assert task_type == "command_intent"
            assert temperature == 0.0
            return """
            {
              "speech_act": "ask",
              "topic": "task",
              "operation": "list",
              "requested_output": "full_list",
              "target": {"kind": "collection", "value": "approval"},
              "parameters": {},
              "confidence": 0.66,
              "ambiguities": []
            }
            """

    task_result_context = ResultContext(
        result_type="task_list",
        count=2,
        items=({"title": "出差西安", "status": "todo"}, {"title": "测试会", "status": "done"}),
        answer="你有 2 条任务。",
    )
    monkeypatch.setattr("app.services.runtime_v5.semantic_understanding._running_tests", lambda: False)
    monkeypatch.setattr("app.services.runtime_v5.semantic_understanding.settings.bot_llm_semantics_enabled", True)
    monkeypatch.setattr("app.services.runtime_v5.semantic_understanding.LLMGateway", lambda: FakeSemanticGateway())

    result = run_runtime_v5(
        context=_context("我的审批", result_context=task_result_context),
        providers={},
    )

    assert result.intent.intent == "approval_query"
    assert result.plan.strategy == "approval_query"
    assert result.plan.sources == ("approval",)
    assert result.composed.result_context is not None
    assert result.composed.result_context.result_type != "task_list"


def test_runtime_v5_inline_previous_people_result_presentation_stays_read_only(monkeypatch) -> None:
    class FakeSemanticGateway:
        def complete_task_text(self, prompt: str, *, task_type: str, temperature: float = 0.2) -> str:
            assert task_type == "command_intent"
            assert temperature == 0.0
            return """
            {
              "speech_act": "request_action",
              "topic": "people",
              "operation": "action_request",
              "requested_output": "full_list",
              "target": {"kind": "previous_result"},
              "parameters": {},
              "confidence": 0.9,
              "ambiguities": []
            }
            """

    class PeopleProvider:
        source = "people"
        _OPERATIONS = {"get_org_snapshot": ("people.get_org_snapshot", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            assert request.operation == "get_org_snapshot"
            return ProviderResult(
                source="people",
                status="success",
                result_type="organization_snapshot",
                count=24,
                items=tuple({"name": f"男员工{i}", "gender_normalized": "male"} for i in range(1, 25)),
                metadata={
                    "context_kind": "query_result",
                    "entity_domain": "People",
                    "people_filter": {"filter": "gender", "value": "male"},
                    "field_projection": "name_only",
                    "result_context_presentation": "detail",
                },
                answer="男性员工名单。",
            )

    result_context = ResultContext(
        result_type="organization_snapshot",
        count=24,
        items=tuple({"name": f"男员工{i}", "gender_normalized": "male"} for i in range(1, 25)),
        metadata={
            "context_kind": "query_result",
            "entity_domain": "People",
            "people_filter": {"filter": "gender", "value": "male"},
            "field_projection": "name_only",
            "result_context_presentation": "summary",
        },
        answer="目前能确认的男性员工是 24 位。",
    )
    monkeypatch.setattr("app.services.runtime_v5.semantic_understanding._running_tests", lambda: False)
    monkeypatch.setattr("app.services.runtime_v5.semantic_understanding.settings.bot_llm_semantics_enabled", True)
    monkeypatch.setattr("app.services.runtime_v5.semantic_understanding.LLMGateway", lambda: FakeSemanticGateway())

    result = run_runtime_v5(
        context=_context("不要在侧边栏，就在对话框显示。", result_context=result_context),
        providers={"people": PeopleProvider()},
    )

    assert result.intent.intent == "organization_snapshot"
    assert result.intent.question_type == "query"
    assert result.plan.strategy == "organization_snapshot"
    assert result.composed.metadata.get("requires_confirmation") is None
    assert result.composed.result_context is not None
    assert result.composed.result_context.result_type == "organization_snapshot"
    assert "男员工1" in result.composed.answer
    assert "男员工24" in result.composed.answer
    assert "侧边栏" not in result.composed.answer
    assert "操作确认" not in result.composed.answer


def test_runtime_v5_smalltalk_composer_answers_assistant_identity_without_provider() -> None:
    result = run_runtime_v5(
        context=_context("你是谁"),
        providers={},
    )

    assert result.intent.intent == "smalltalk"
    assert result.execution is not None
    assert result.execution.status == "skipped"
    assert result.composed.answer == "我是大飞哥，Digital Advisor。我的正事是帮你理解企业里的审批、任务、日程、消息和后续接入的数据。"


def test_runtime_v5_smalltalk_composer_answers_current_time_without_provider() -> None:
    result = run_runtime_v5(
        context=_context("你好，现在几点了。"),
        providers={},
    )

    assert result.intent.intent == "smalltalk"
    assert result.execution is not None
    assert result.execution.status == "skipped"
    assert result.composed.answer.startswith("现在是北京时间 ")


def test_runtime_v5_smalltalk_composer_answers_emoji_without_provider_lookup() -> None:
    result = run_runtime_v5(
        context=_context("你知道这个表情是什么情绪吗"),
        providers={},
    )

    assert result.intent.intent == "smalltalk"
    assert result.execution is not None
    assert result.execution.status == "skipped"
    assert "不能可靠识别具体表情含义" in result.composed.answer


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
    assert result.intent.intent == "task_query"
    assert result.execution is not None
    assert result.execution.status == "error"
    assert result.execution.provider_results[0].metadata["error_type"] == "operation_not_covered"


def test_guided_clarification_builder_uses_contextual_department_option() -> None:
    company_id = uuid4()
    context = RuntimeContext(
        identity=RuntimeIdentity(open_id="ou_owner", role="owner", department_id="dept-1"),
        runtime_scope=RuntimeScope(company_ids=(company_id,), active_company_id=company_id, active_department_id="dept-1"),
        current_message="看看部门情况",
    )
    intent = IntentResult(
        question_type="query",
        intent="task_query",
        data_scope="department",
        missing_params=("department", "time_range"),
        confidence=0.52,
    )

    guide = build_clarification_guide(context=context, intent=intent, fallback="")

    payloads = guide.option_payloads()
    assert guide.reason == "missing_params"
    assert guide.next_step == "请补充：部门、时间范围"
    assert {"param": "department", "label": "当前部门", "value": "current_department"} in payloads
    assert {"param": "time_range", "label": "本周", "value": "this_week"} in payloads


def test_clarification_reply_resolves_short_answer_to_command_message() -> None:
    result_context = ResultContext(
        result_type="task_query_clarification",
        count=1,
        metadata={
            "execution_status": "clarification",
            "operation": "task_query",
            "missing_params": ["scope", "time_range"],
            "clarification_options": [
                {"param": "scope", "label": "部门", "value": "department"},
                {"param": "time_range", "label": "本周", "value": "this_week"},
            ],
        },
    )

    reply = resolve_clarification_reply("部门 本周", result_context)

    assert reply.is_reply is True
    assert reply.filled_params == {"scope": "department", "time_range": "this_week"}
    assert reply.resolved_message == "查看部门任务 本周 部门 本周"


def test_runtime_v5_clarification_reply_reenters_command_mainline() -> None:
    clarification_context = ResultContext(
        result_type="task_query_clarification",
        count=1,
        items=({"status": "clarification"},),
        metadata={
            "execution_status": "clarification",
            "operation": "task_query",
            "missing_params": ["scope", "time_range"],
            "clarification_options": [
                {"param": "scope", "label": "部门", "value": "department"},
                {"param": "time_range", "label": "本周", "value": "this_week"},
            ],
        },
    )

    result = run_runtime_v5(
        context=_context("部门 本周", result_context=clarification_context),
        providers={},
    )

    assert result.intent.intent == "task_query"
    assert result.intent.data_scope == "department"
    assert result.execution is not None
    assert result.execution.status == "error"
    assert result.execution.provider_results[0].metadata["provider_governance"] is True
    assert result.context.current_message == "查看部门任务 本周 部门 本周"
    assert result.context.session_context["runtime_v5_last_clarification_reply"]["filled_params"] == {
        "scope": "department",
        "time_range": "this_week",
    }


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


def test_runtime_v5_department_task_query_recognizes_department_scope() -> None:
    intent = recognize_intent("查看部门任务", _context("查看部门任务"))

    assert intent.intent == "task_query"
    assert intent.question_type == "query"
    assert intent.data_scope == "department"


def test_runtime_v5_company_calendar_query_recognizes_company_scope() -> None:
    intent = recognize_intent("查看全公司日程", _context("查看全公司日程"))

    assert intent.intent == "calendar_query"
    assert intent.question_type == "query"
    assert intent.data_scope == "company"


def test_runtime_v5_department_calendar_query_recognizes_department_scope() -> None:
    intent = recognize_intent("查看部门日程", _context("查看部门日程"))

    assert intent.intent == "calendar_query"
    assert intent.question_type == "query"
    assert intent.data_scope == "department"


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
            department_names=("管理层",),
            display_name="陈俊",
            job_title="CEO",
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
        "department_names": ["管理层"],
        "managed_departments": [],
        "management_scope": [],
        "subject_source": "runtime_identity",
        "is_owner": True,
        "is_admin": False,
        "identity_fact": {
            "user_id": "user_1",
            "open_id": "ou_workspace",
            "display_name": "陈俊",
            "role": "owner",
            "department_id": "dept_1",
            "department_names": ["管理层"],
            "job_title": "CEO",
            "email": "",
            "domains": ["workspace"],
            "is_owner": True,
            "is_admin": False,
            "source": "runtime_identity",
        },
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


def test_workspace_policy_subject_prefers_organization_foundation_subject() -> None:
    company_id = uuid4()
    context = RuntimeContext(
        identity=RuntimeIdentity(
            user_id="fallback_user",
            open_id="ou_workspace",
            role="manager",
            department_id="fallback_dept",
            department_names=("旧部门",),
            display_name="陈俊",
            domains=("workspace",),
        ),
        runtime_scope=RuntimeScope(company_ids=(company_id,), active_company_id=company_id),
        current_message="查看部门任务",
        organization_subject={
            "source": "organization_foundation",
            "actor_user_id": "u_org",
            "actor_open_id": "ou_workspace",
            "department_ids": ["dept_org"],
            "department_names": ["组织部"],
            "managed_departments": [{"id": "dept_org", "name": "组织部", "scope": "DEPARTMENT"}],
            "management_scope": [{"scope": "DEPARTMENT", "department_id": "dept_org", "department_name": "组织部"}],
        },
    )
    intent = IntentResult(
        question_type="query",
        intent="task_query",
        data_scope="department",
        confidence=0.9,
        canonical_question="查看部门任务",
    )
    plan = PlannerResult(strategy="task_query", sources=("task",))

    permission = check_runtime_permission(context=context, intent=intent, plan=plan)

    subject = permission.metadata["policy_subject"]
    assert subject["actor_user_id"] == "u_org"
    assert subject["departments"] == ["dept_org"]
    assert subject["department_names"] == ["组织部"]
    assert subject["managed_departments"] == [{"id": "dept_org", "name": "组织部", "scope": "DEPARTMENT"}]
    assert subject["management_scope"] == [{"scope": "DEPARTMENT", "department_id": "dept_org", "department_name": "组织部"}]
    assert subject["subject_source"] == "organization_foundation"


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


def test_runtime_policy_metadata_includes_intent_contract_from_domain_query() -> None:
    context = _context("王庆威的手机号是多少")
    intent = recognize_intent("王庆威的手机号是多少", context)
    plan = PlannerResult(strategy="people_lookup", sources=("people",))

    permission = check_runtime_permission(context=context, intent=intent, plan=plan)

    assert permission.metadata["intent_contract"] == {
        "intent": "people_lookup",
        "question_type": "query",
        "operation_kind": "read",
        "domain": "people",
        "scope": "person",
        "presentation_hint": "text",
        "risk_hint": "low",
        "evidence_requirement": "source",
        "needs_clarification": False,
    }


def test_runtime_policy_metadata_includes_action_intent_contract() -> None:
    result_context = ResultContext(
        result_type="department_members",
        count=2,
        items=(
            {"name": "张三", "open_id": "ou_zhang"},
            {"name": "李四", "open_id": "ou_li"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="上一轮人员结果。",
    )
    context = _context("用机器人发给这些人说：明天上午提交周报", result_context=result_context)
    intent = recognize_intent(context.current_message, context)
    plan = PlannerResult(strategy="message_send", sources=("im",))

    permission = check_runtime_permission(context=context, intent=intent, plan=plan)

    assert permission.metadata["intent_contract"] == {
        "intent": "message_send",
        "question_type": "action",
        "operation_kind": "send",
        "domain": "communication",
        "scope": "self",
        "presentation_hint": "",
        "risk_hint": "high",
        "evidence_requirement": "",
        "needs_clarification": False,
    }


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
    assert "我先基于已授权的 Workspace 认知数据给你一个概览" in result.answer
    assert "聚合视角" in result.answer
    assert "负荷分布" not in result.answer


def test_workspace_department_task_query_filters_cognitive_projection_by_department() -> None:
    company_id = uuid4()
    db = _RuntimeWriteDb()
    append_workspace_cognitive_event(
        db,
        company_id=company_id,
        object_type="task",
        object_id="task-dept-1",
        source="feishu_user_observation",
        actor="ou_1",
        raw_payload={"task_guid": "task-dept-1", "title": "本部门逾期", "status": "todo", "due_at": "2026-06-20T10:00:00+00:00"},
        owner_user_id="user-1",
        owner_open_id="ou_1",
        owner_department_id="dept-1",
    )
    append_workspace_cognitive_event(
        db,
        company_id=company_id,
        object_type="task",
        object_id="task-dept-2",
        source="feishu_user_observation",
        actor="ou_2",
        raw_payload={"task_guid": "task-dept-2", "title": "其他部门逾期", "status": "todo", "due_at": "2026-06-20T10:00:00+00:00"},
        owner_user_id="user-2",
        owner_open_id="ou_2",
        owner_department_id="dept-2",
    )
    context = RuntimeContext(
        identity=RuntimeIdentity(open_id="ou_owner", role="owner", department_id="dept-1"),
        runtime_scope=RuntimeScope(company_ids=(company_id,), active_company_id=company_id, active_department_id="dept-1"),
        current_message="查看部门任务",
    )
    intent = IntentResult(
        question_type="query",
        intent="task_query",
        data_scope="department",
        confidence=0.9,
        canonical_question="查看部门任务",
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
            resource_scope="DEPARTMENT",
            authorization_status="AUTHORIZED",
        ),
    )

    result = FeishuTaskProvider(db=db).execute(request)  # type: ignore[arg-type]

    assert result.status == "success"
    assert result.result_type == "workspace_aggregation_summary"
    assert result.metadata["queried_event_count"] == 2
    assert result.metadata["visible_event_count"] == 1
    assert result.items[0]["visibility_scope"] == "DEPARTMENT"
    assert result.items[0]["metrics"]["task_total"] == 1
    assert result.items[0]["metrics"]["overdue_task_count"] == 1


def test_workspace_department_task_query_without_department_context_returns_cognitive_gap() -> None:
    company_id = uuid4()
    db = _RuntimeWriteDb()
    append_workspace_cognitive_event(
        db,
        company_id=company_id,
        object_type="task",
        object_id="task-dept-1",
        source="feishu_user_observation",
        actor="ou_1",
        raw_payload={"task_guid": "task-dept-1", "title": "部门任务", "status": "todo"},
        owner_user_id="user-1",
        owner_open_id="ou_1",
        owner_department_id="dept-1",
    )
    context = RuntimeContext(
        identity=RuntimeIdentity(open_id="ou_owner", role="owner"),
        runtime_scope=RuntimeScope(company_ids=(company_id,), active_company_id=company_id),
        current_message="查看部门任务",
    )
    intent = IntentResult(
        question_type="query",
        intent="task_query",
        data_scope="department",
        confidence=0.9,
        canonical_question="查看部门任务",
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
            resource_scope="DEPARTMENT",
            authorization_status="AUTHORIZED",
        ),
    )

    result = FeishuTaskProvider(db=db).execute(request)  # type: ignore[arg-type]

    assert result.status == "denied"
    assert result.result_type == "workspace_aggregation_summary"
    assert result.error == "missing_department_context"
    assert result.metadata["provider_boundary"] == "workspace_cognitive_gap"
    assert result.metadata["realtime_provider_boundary"] == "enterprise_realtime_not_integrated"
    assert result.metadata["visible_event_count"] == 0
    assert result.metadata["user_fallback_allowed"] is False
    assert "还没有对准要看的部门" in result.answer
    assert "不会改用" not in result.answer


def test_workspace_company_task_query_can_aggregate_multiple_departments_for_owner() -> None:
    company_id = uuid4()
    db = _RuntimeWriteDb()
    for dept in ("dept-1", "dept-2"):
        append_workspace_cognitive_event(
            db,
            company_id=company_id,
            object_type="task",
            object_id=f"task-{dept}",
            source="feishu_user_observation",
            actor=f"ou-{dept}",
            raw_payload={"task_guid": f"task-{dept}", "title": dept, "status": "todo"},
            owner_user_id=f"user-{dept}",
            owner_open_id=f"ou-{dept}",
            owner_department_id=dept,
        )
    context = RuntimeContext(
        identity=RuntimeIdentity(open_id="ou_owner", role="owner", department_id="dept-1"),
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
    assert result.metadata["queried_event_count"] == 2
    assert result.metadata["visible_event_count"] == 2
    assert result.items[0]["visibility_scope"] == "COMPANY"
    assert result.items[0]["metrics"]["task_total"] == 2


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


def test_runtime_provider_results_include_foundation_data_source_contract() -> None:
    class PeopleProvider:
        source = "people"
        _OPERATIONS = {"get_org_snapshot": ("feishu_contact_organization_snapshot", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            return ProviderResult(
                source="people",
                status="success",
                result_type="organization_snapshot",
                count=1,
                items=({"name": "张三", "open_id": "ou_1"},),
                answer="ok",
            )

    context = _context("公司有多少人")
    intent = IntentResult(
        question_type="query",
        intent="organization_snapshot",
        data_scope="organization",
        confidence=0.9,
        canonical_question="公司有多少人",
    )
    plan = PlannerResult(strategy="organization_snapshot", sources=("people",))
    permission = check_runtime_permission(context=context, intent=intent, plan=plan)

    result = CapabilityRouter({"people": PeopleProvider()}).execute(
        context=context,
        intent=intent,
        plan=plan,
        permission=permission,
    )

    provider = result.provider_results[0]
    foundation = provider.metadata["foundation_data_source"]
    assert foundation["domain"] == "people"
    assert foundation["source"] == "people"
    assert foundation["operation"] == "get_org_snapshot"
    assert foundation["strategy"] == "organization_snapshot"
    assert foundation["actor_identity"] == "BOT"
    assert foundation["credential_mode"] == "TENANT_TOKEN"
    assert foundation["standard_contract"] == "Runtime ProviderRequest -> ProviderResult"
    assert provider.metadata["execution_identity_contract"]["credential_mode"] == "TENANT_TOKEN"
    assert result.result_context is not None
    summary_foundation = result.result_context.metadata["provider_results"][0]["foundation_data_source"]
    assert summary_foundation["domain"] == "people"
    assert summary_foundation["provider_runtime"] == "tool_router"


def test_runtime_knowledge_provider_uses_knowledge_foundation_domain() -> None:
    class KnowledgeRuntimeProvider:
        source = "knowledge"
        _OPERATIONS = {"search": ("local_public_knowledge", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            return ProviderResult(
                source="knowledge",
                status="success",
                result_type="knowledge_list",
                count=1,
                items=({"kind": "knowledge_document", "title": "报销流程说明"},),
                answer="ok",
            )

    context = _context("报销流程怎么做")
    intent = IntentResult(
        question_type="query",
        intent="general_query",
        data_scope="company",
        confidence=0.9,
        canonical_question="报销流程怎么做",
    )
    plan = PlannerResult(strategy="general_query", sources=("knowledge",))
    permission = check_runtime_permission(context=context, intent=intent, plan=plan)

    result = CapabilityRouter({"knowledge": KnowledgeRuntimeProvider()}).execute(
        context=context,
        intent=intent,
        plan=plan,
        permission=permission,
    )

    provider = result.provider_results[0]
    foundation = provider.metadata["foundation_data_source"]
    assert foundation["domain"] == "knowledge"
    assert foundation["source"] == "knowledge"
    assert foundation["provider_runtime"] == "hybrid_knowledge"


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


def test_runtime_v5_workevent_items_expose_policy_visibility_contract() -> None:
    event_id = uuid4()
    event = SimpleNamespace(
        id=event_id,
        title="部门风险",
        event_type="risk",
        source="feishu",
        business_domain="workspace",
        occurred_at=None,
        content_text="项目交付风险需要跟进",
        importance_score=0.8,
        labels=["risk"],
        visibility_scope="department",
        allowed_user_ids=[],
        allowed_departments=["dept_1"],
        allowed_roles=[],
        object_type="approval",
        object_id="approval_1",
        data_classification="workspace_cognitive",
        payload={
            "cognitive_fields": {
                "owner_open_id": "ou_owner",
                "owner_user_id": "user_owner",
                "owner_department_id": "dept_1",
            }
        },
        raw_json={},
    )

    item = _workevent_item(event)
    knowledge_item = _knowledge_event_item(event)

    assert item["resource_plane"] == "cognitive"
    assert item["resource_type"] == "approval"
    assert item["source_system"] == "feishu"
    assert item["source_object_type"] == "approval"
    assert item["source_object_id"] == "approval_1"
    assert item["visibility_scope"] == "DEPARTMENT"
    assert item["inherited_visibility_scope"] == "DEPARTMENT"
    assert item["allowed_departments"] == ["dept_1"]
    assert item["owner_open_id"] == "ou_owner"
    assert item["owner_user_id"] == "user_owner"
    assert item["owner_department_id"] == "dept_1"
    assert item["source_event_ids"] == [str(event_id)]
    assert knowledge_item["visibility_scope"] == "DEPARTMENT"
    assert knowledge_item["allowed_departments"] == ["dept_1"]


def test_runtime_v5_memory_items_are_filtered_by_policy_subject() -> None:
    fact = SimpleNamespace(
        id=uuid4(),
        fact_type="preference",
        subject="个人偏好",
        content="只对本人可见",
        confidence="high",
        scope="personal",
        user_open_id="ou_1",
        chat_id=None,
        source_kind="extracted_fact",
        source_work_event_id=None,
        payload={},
    )
    item = _memory_item(fact)

    assert item["visibility_scope"] == "SELF"
    assert item["allowed_user_ids"] == ["ou_1"]
    assert item["owner_open_id"] == "ou_1"

    result = build_runtime_result(
        command_plan=_command_plan("memory_query", result_type="memory_query", sources=("memory",)),
        permission=PermissionDecision(
            allowed=True,
            requires_confirmation=False,
            execution_identity="bot",
            metadata={
                "policy_subject": {"actor_user_id": "user_2", "actor_open_id": "ou_2", "company_id": "company_1"},
                "policy_scope": {"requested_scope": "self", "resolved_scope": "self"},
                "identity_decision": {"actor_identity": "BOT", "credential_mode": "TENANT_TOKEN"},
                "allowed_resource_types": ["memory"],
            },
        ),
        execution=None,
        composed=ComposedAnswer(
            answer="相关记忆。",
            result_context=ResultContext(result_type="memory_fact_list", count=1, items=(item,)),
        ),
    )

    payload = runtime_result_payload(result)

    assert payload["items"] == []
    assert payload["metadata"]["policy_result_filter"]["resource_filters"][0]["visible"] is False


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


def test_runtime_v5_pending_confirmation_exposes_people_targets() -> None:
    result_context = ResultContext(
        result_type="people_search",
        count=2,
        items=(
            {"name": "张三", "open_id": "ou_zhang", "email": "zhangsan@example.com"},
            {"name": "李四", "open_id": "ou_li", "email": "lisi@example.com"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="上一轮人员结果。",
    )

    result = run_runtime_v5(
        context=_context(
            "用机器人发给这些人说：明天上午提交周报",
            chat_id="chat_people_targets_confirmation",
            result_context=result_context,
        ),
        providers={"im": object()},
    )

    assert result.execution is None
    assert result.composed.result_context is not None
    assert result.composed.result_context.result_type == "runtime_waiting_input"
    assert "当前多人目标只开放" in result.composed.answer
    assert "操作确认" not in result.composed.answer


@pytest.mark.parametrize(
    ("delivery_reply", "delivery_mode", "delivery_label"),
    (
        ("拉群后发到群里", "create_group_then_send", "建群后在群里发送"),
    ),
)
def test_runtime_v5_people_context_missing_delivery_mode_reply_enters_confirmation(
    delivery_reply: str,
    delivery_mode: str,
    delivery_label: str,
) -> None:
    result_context = ResultContext(
        result_type="people_search",
        count=2,
        items=(
            {"name": "张三", "open_id": "ou_zhang", "email": "zhangsan@example.com"},
            {"name": "李四", "open_id": "ou_li", "email": "lisi@example.com"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="上一轮人员结果。",
    )

    waiting_input = run_runtime_v5(
        context=_context(
            "发给这些人说：明天上午提交周报",
            chat_id=f"chat_people_context_delivery_{delivery_mode}",
            result_context=result_context,
        ),
        providers={"im": object()},
    )

    assert waiting_input.execution is None
    assert waiting_input.intent.intent == "message_send"
    assert waiting_input.intent.missing_params == ("delivery_mode",)
    assert waiting_input.composed.result_context is not None
    assert waiting_input.composed.result_context.result_type == "runtime_waiting_input"
    assert "当前多人目标只开放" in waiting_input.composed.answer
    runtime_state = waiting_input.composed.result_context.metadata["runtime_state"]

    waiting_confirmation = run_runtime_v5(
        context=_context(
            delivery_reply,
            chat_id=f"chat_people_context_delivery_{delivery_mode}",
            result_context=waiting_input.composed.result_context,
            session_context={"runtime_v5_state": runtime_state},
        ),
        providers={"im": object()},
    )

    assert waiting_confirmation.execution is None
    assert waiting_confirmation.intent.intent == "message_send"
    assert waiting_confirmation.intent.missing_params == ()
    assert waiting_confirmation.intent.entities["target_type"] == "people_context"
    assert waiting_confirmation.intent.entities["delivery_mode"] == delivery_mode
    assert waiting_confirmation.intent.entities["text"] == "明天上午提交周报"
    assert waiting_confirmation.intent.entities["people_target_count"] == "2"
    assert [item["name"] for item in waiting_confirmation.intent.entities["people_targets"]] == ["张三", "李四"]
    assert waiting_confirmation.composed.result_context is not None
    assert waiting_confirmation.composed.result_context.result_type == "runtime_pending_confirmation"
    assert "人员目标：2 人：张三、李四" in waiting_confirmation.composed.answer
    assert f"发送方式：{delivery_label}" in waiting_confirmation.composed.answer


@pytest.mark.parametrize(
    ("delivery_reply", "delivery_mode"),
    (
        ("用机器人通知这些人", "bot_multi_notify"),
        ("替我分别发给这些人", "user_multi_private"),
    ),
)
def test_runtime_v5_unavailable_people_context_delivery_mode_stays_waiting(
    delivery_reply: str,
    delivery_mode: str,
) -> None:
    result_context = ResultContext(
        result_type="people_search",
        count=2,
        items=(
            {"name": "张三", "open_id": "ou_zhang", "email": "zhangsan@example.com"},
            {"name": "李四", "open_id": "ou_li", "email": "lisi@example.com"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="上一轮人员结果。",
    )

    waiting_input = run_runtime_v5(
        context=_context(
            "发给这些人说：明天上午提交周报",
            chat_id="chat_people_context_delivery_guard",
            result_context=result_context,
        ),
        providers={"im": object()},
    )
    assert waiting_input.composed.result_context is not None
    waiting_confirmation = run_runtime_v5(
        context=_context(
            delivery_reply,
            chat_id="chat_people_context_delivery_guard",
            result_context=waiting_input.composed.result_context,
            session_context={"runtime_v5_state": waiting_input.composed.result_context.metadata["runtime_state"]},
        ),
        providers={"im": object()},
    )
    assert waiting_confirmation.composed.result_context is not None
    assert waiting_confirmation.execution is None
    assert waiting_confirmation.composed.result_context.result_type == "runtime_waiting_input"
    assert "当前多人目标只开放" in waiting_confirmation.composed.answer


def test_runtime_v5_people_context_create_group_delivery_mode_enters_confirmation() -> None:
    result_context = ResultContext(
        result_type="people_search",
        count=2,
        items=(
            {"name": "张三", "open_id": "ou_zhang", "email": "zhangsan@example.com"},
            {"name": "李四", "open_id": "ou_li", "email": "lisi@example.com"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="上一轮人员结果。",
    )

    waiting_input = run_runtime_v5(
        context=_context(
            "发给这些人说：明天上午提交周报",
            chat_id="chat_people_context_group_delivery",
            result_context=result_context,
        ),
        providers={"im": object()},
    )
    assert waiting_input.composed.result_context is not None
    waiting_confirmation = run_runtime_v5(
        context=_context(
            "拉群后发到群里",
            chat_id="chat_people_context_group_delivery",
            result_context=waiting_input.composed.result_context,
            session_context={"runtime_v5_state": waiting_input.composed.result_context.metadata["runtime_state"]},
        ),
        providers={"im": object()},
    )
    assert waiting_confirmation.composed.result_context is not None
    assert waiting_confirmation.composed.result_context.result_type == "runtime_pending_confirmation"
    assert waiting_confirmation.intent.entities["delivery_mode"] == "create_group_then_send"
    assert "发送方式：建群后在群里发送" in waiting_confirmation.composed.answer


def test_runtime_v5_people_context_single_send_reply_stays_waiting() -> None:
    result_context = ResultContext(
        result_type="people_search",
        count=2,
        items=(
            {"name": "张三", "open_id": "ou_zhang", "email": "zhangsan@example.com"},
            {"name": "李四", "open_id": "ou_li", "email": "lisi@example.com"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="上一轮人员结果。",
    )

    waiting_input = run_runtime_v5(
        context=_context(
            "发给这些人说：明天上午提交周报",
            chat_id="chat_people_context_single_send_guard",
            result_context=result_context,
        ),
        providers={"im": object()},
    )
    assert waiting_input.composed.result_context is not None

    waiting_confirmation = run_runtime_v5(
        context=_context(
            "单独发",
            chat_id="chat_people_context_single_send_guard",
            result_context=waiting_input.composed.result_context,
            session_context={"runtime_v5_state": waiting_input.composed.result_context.metadata["runtime_state"]},
        ),
        providers={"im": object()},
    )
    assert waiting_confirmation.execution is None
    assert waiting_confirmation.composed.result_context is not None
    assert waiting_confirmation.composed.result_context.result_type == "runtime_waiting_input"
    assert "当前多人目标只开放" in waiting_confirmation.composed.answer
    assert "操作确认" not in waiting_confirmation.composed.answer


def test_runtime_v5_people_context_create_group_then_send_executes_after_confirmation() -> None:
    calls: list[dict] = []

    class IMProvider:
        source = "im"
        _OPERATIONS = {"send_message": ("feishu_im_send_message", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            calls.append(
                {
                    "operation": request.operation,
                    "target_type": request.params.get("target_type"),
                    "delivery_mode": request.params.get("delivery_mode"),
                    "text": request.params.get("text"),
                    "people_targets": request.params.get("people_targets"),
                    "execution_identity": request.execution_identity,
                }
            )
            return ProviderResult(
                source="im",
                status="success",
                result_type="message_send_people_context_group",
                count=1,
                items=(
                    {
                        "target": "临时沟通群-张三、李四",
                        "text": request.params.get("text"),
                        "people_target_count": 2,
                        "delivery_mode": "create_group_then_send",
                    },
                ),
                metadata={"delivery_mode": "create_group_then_send", "people_target_count": 2},
                answer="已创建群聊并发送消息。",
            )

    result_context = ResultContext(
        result_type="people_search",
        count=2,
        items=(
            {"name": "张三", "open_id": "ou_zhang", "email": "zhangsan@example.com"},
            {"name": "李四", "open_id": "ou_li", "email": "lisi@example.com"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="上一轮人员结果。",
    )

    waiting_input = run_runtime_v5(
        context=_context(
            "发给这些人说：明天上午提交周报",
            chat_id="chat_people_context_group_send",
            result_context=result_context,
        ),
        providers={"im": IMProvider()},
    )
    assert waiting_input.composed.result_context is not None

    waiting_confirmation = run_runtime_v5(
        context=_context(
            "拉群后发到群里",
            chat_id="chat_people_context_group_send",
            result_context=waiting_input.composed.result_context,
            session_context={"runtime_v5_state": waiting_input.composed.result_context.metadata["runtime_state"]},
        ),
        providers={"im": IMProvider()},
    )
    assert waiting_confirmation.composed.result_context is not None

    confirmed = run_runtime_v5(
        context=_context(
            "确认",
            chat_id="chat_people_context_group_send",
            result_context=waiting_confirmation.composed.result_context,
            session_context={"runtime_v5_state": waiting_confirmation.composed.result_context.metadata["runtime_state"]},
        ),
        providers={"im": IMProvider()},
    )

    assert len(calls) == 1
    assert calls[0]["operation"] == "send_message"
    assert calls[0]["target_type"] == "people_context"
    assert calls[0]["delivery_mode"] == "create_group_then_send"
    assert calls[0]["text"] == "明天上午提交周报"
    assert calls[0]["execution_identity"] == "user"
    assert [(item["name"], item["open_id"]) for item in calls[0]["people_targets"]] == [
        ("张三", "ou_zhang"),
        ("李四", "ou_li"),
    ]
    assert confirmed.execution is not None
    assert confirmed.execution.status == "success"
    assert confirmed.execution.provider_results[0].result_type == "message_send_people_context_group"
    assert confirmed.execution.provider_results[0].metadata["delivery_mode"] == "create_group_then_send"
    assert confirmed.composed.result_context is not None
    assert confirmed.composed.result_context.result_type == "runtime_action"
    assert confirmed.composed.result_context.items[0]["status"] == "success"
    assert "已创建群聊并发送消息" in confirmed.composed.answer
    assert "可继续问" not in confirmed.composed.answer


def test_runtime_v5_organization_people_context_create_group_send_keeps_targets_after_confirmation() -> None:
    calls: list[dict] = []

    class IMProvider:
        source = "im"
        _OPERATIONS = {"send_message": ("feishu_im_send_message", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            calls.append(
                {
                    "target_type": request.params.get("target_type"),
                    "target": request.params.get("target"),
                    "delivery_mode": request.params.get("delivery_mode"),
                    "text": request.params.get("text"),
                    "people_targets": request.params.get("people_targets"),
                }
            )
            return ProviderResult(
                source="im",
                status="success",
                result_type="message_send_people_context_group",
                count=1,
                items=({"target": "商务组沟通群", "text": request.params.get("text")},),
                metadata={"delivery_mode": "create_group_then_send", "people_target_count": 1},
                answer="已创建群聊并发送消息。",
            )

    result_context = ResultContext(
        result_type="department_members",
        count=1,
        items=({"name": "汤冠男", "open_id": "ou_tang", "title": "部门高级经理"},),
        metadata={
            "context_kind": "query_result",
            "entity_domain": "people",
            "organization_resolution": {"query": "商务部", "resolved_name": "商务组"},
        },
        answer="商务组目前 1 位，是汤冠男。",
    )

    waiting_input = run_runtime_v5(
        context=_context(
            "给商务组的人员发条消息：测试内容",
            chat_id="chat_org_people_context_group_send",
            result_context=result_context,
        ),
        providers={"im": IMProvider()},
    )
    assert waiting_input.composed.result_context is not None

    waiting_confirmation = run_runtime_v5(
        context=_context(
            "拉群后发到群里",
            chat_id="chat_org_people_context_group_send",
            result_context=waiting_input.composed.result_context,
            session_context={"runtime_v5_state": waiting_input.composed.result_context.metadata["runtime_state"]},
        ),
        providers={"im": IMProvider()},
    )
    assert waiting_confirmation.composed.result_context is not None
    assert "人员目标：1 人：汤冠男" in waiting_confirmation.composed.answer

    confirmed = run_runtime_v5(
        context=_context(
            "确认",
            chat_id="chat_org_people_context_group_send",
            result_context=waiting_confirmation.composed.result_context,
            session_context={"runtime_v5_state": waiting_confirmation.composed.result_context.metadata["runtime_state"]},
        ),
        providers={"im": IMProvider()},
    )

    assert len(calls) == 1
    assert calls[0]["target_type"] == "people_context"
    assert calls[0]["target"] == "商务组"
    assert calls[0]["delivery_mode"] == "create_group_then_send"
    assert calls[0]["text"] == "测试内容"
    assert [(item["name"], item["open_id"]) for item in calls[0]["people_targets"]] == [("汤冠男", "ou_tang")]
    assert confirmed.execution is not None
    assert confirmed.execution.status == "success"


def test_runtime_v5_organization_people_action_without_targets_does_not_confirm() -> None:
    first = run_runtime_v5(
        context=_context(
            "给商务组的人员发条消息：测试内容",
            chat_id="chat_org_people_context_missing_targets",
        ),
        providers={"im": object()},
    )

    assert first.execution is None
    assert first.composed.result_context is not None
    assert first.composed.result_context.result_type == "runtime_waiting_input"
    assert "还没有拿到这批人的具体名单" in first.composed.answer
    assert "操作确认" not in first.composed.answer

    second = run_runtime_v5(
        context=_context(
            "拉群后发到群里",
            chat_id="chat_org_people_context_missing_targets",
            result_context=first.composed.result_context,
            session_context={"runtime_v5_state": first.composed.result_context.metadata["runtime_state"]},
        ),
        providers={"im": object()},
    )

    assert second.execution is None
    assert second.composed.result_context is not None
    assert second.composed.result_context.result_type == "runtime_waiting_input"
    assert "还没有拿到这批人的具体名单" in second.composed.answer
    assert "操作确认" not in second.composed.answer


def test_runtime_v5_organization_people_action_hydrates_single_member_target() -> None:
    calls: list[dict] = []

    class PeopleProvider:
        source = "people"
        _OPERATIONS = {"list_department_members": ("feishu_contact_organization_snapshot", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            assert request.operation == "list_department_members"
            assert request.params["keyword"] == "商务组"
            return ProviderResult(
                source="people",
                status="success",
                result_type="department_members",
                count=1,
                items=(
                    {
                        "name": "汤冠男",
                        "open_id": "ou_tang",
                        "title": "部门高级经理",
                        "department": "商务组",
                    },
                ),
                answer="商务组目前 1 位，是汤冠男。",
            )

    class IMProvider:
        source = "im"
        _OPERATIONS = {"send_message": ("feishu_im_send_message", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            calls.append(
                {
                    "target_type": request.params.get("target_type"),
                    "target": request.params.get("target"),
                    "target_open_id": request.params.get("target_open_id"),
                    "text": request.params.get("text"),
                }
            )
            return ProviderResult(source="im", status="success", result_type="message_send", count=1, answer="消息已发送。")

    waiting_confirmation = run_runtime_v5(
        context=_context(
            "给商务组的人员发条消息：测试内容",
            chat_id="chat_org_people_action_hydrates_single_member",
        ),
        providers={"people": PeopleProvider(), "im": IMProvider()},
    )

    assert waiting_confirmation.execution is None
    assert waiting_confirmation.intent.intent == "message_send"
    assert waiting_confirmation.intent.entities["target_type"] == "person"
    assert waiting_confirmation.intent.entities["target"] == "汤冠男"
    assert waiting_confirmation.intent.entities["target_open_id"] == "ou_tang"
    assert waiting_confirmation.composed.result_context is not None
    assert waiting_confirmation.composed.result_context.result_type == "runtime_pending_confirmation"
    assert "还没有拿到这批人的具体名单" not in waiting_confirmation.composed.answer

    confirmed = run_runtime_v5(
        context=_context(
            "确认",
            chat_id="chat_org_people_action_hydrates_single_member",
            result_context=waiting_confirmation.composed.result_context,
            session_context={"runtime_v5_state": waiting_confirmation.composed.result_context.metadata["runtime_state"]},
        ),
        providers={"people": PeopleProvider(), "im": IMProvider()},
    )

    assert calls == [
        {
            "target_type": "person",
            "target": "汤冠男",
            "target_open_id": "ou_tang",
            "text": "测试内容",
        }
    ]
    assert confirmed.execution is not None
    assert confirmed.execution.status == "success"


def test_runtime_v5_explicit_org_action_does_not_reuse_previous_people_targets() -> None:
    result_context = ResultContext(
        result_type="department_members",
        count=1,
        items=({"name": "汤冠男", "open_id": "ou_tang", "title": "部门高级经理", "department": "商务组"},),
        metadata={
            "context_kind": "query_result",
            "entity_domain": "people",
            "organization_resolution": {"query": "商务组", "resolved_name": "商务组"},
        },
        answer="商务组目前 1 位，是汤冠男。",
    )

    class PeopleProvider:
        source = "people"
        _OPERATIONS = {"list_department_members": ("feishu_contact_organization_snapshot", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            assert request.operation == "list_department_members"
            assert request.params["keyword"] == "IT组"
            return ProviderResult(
                source="people",
                status="success",
                result_type="department_members",
                count=1,
                items=(
                    {
                        "name": "王云飞",
                        "open_id": "ou_wangyunfei",
                        "title": "IT 专员",
                        "department": "IT组",
                    },
                ),
                answer="IT组目前 1 位，是王云飞。",
            )

    waiting_confirmation = run_runtime_v5(
        context=_context(
            "给IT组的人发条消息：测试内容",
            chat_id="chat_explicit_org_does_not_reuse_previous_people_targets",
            result_context=result_context,
        ),
        providers={"people": PeopleProvider(), "im": object()},
    )

    assert waiting_confirmation.intent.intent == "message_send"
    assert waiting_confirmation.intent.entities["organization_unit"] == "IT组"
    assert waiting_confirmation.intent.entities["target_type"] == "person"
    assert waiting_confirmation.intent.entities["target"] == "王云飞"
    assert waiting_confirmation.intent.entities["target_open_id"] == "ou_wangyunfei"
    assert [item["name"] for item in waiting_confirmation.intent.entities["people_targets"]] == ["王云飞"]
    assert "汤冠男" not in waiting_confirmation.composed.answer


def test_runtime_v5_explicit_org_action_does_not_reuse_pending_action_target() -> None:
    calls: list[str] = []

    class PeopleProvider:
        source = "people"
        _OPERATIONS = {"list_department_members": ("feishu_contact_organization_snapshot", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            keyword = str(request.params["keyword"])
            calls.append(keyword)
            if keyword == "商务组":
                item = {"name": "汤冠男", "open_id": "ou_tang", "title": "部门高级经理", "department": "商务组"}
            elif keyword == "IT组":
                item = {"name": "王云飞", "open_id": "ou_wangyunfei", "title": "IT 专员", "department": "IT组"}
            else:
                raise AssertionError(f"unexpected organization keyword: {keyword}")
            return ProviderResult(
                source="people",
                status="success",
                result_type="department_members",
                count=1,
                items=(item,),
                answer=f"{keyword}目前 1 位，是{item['name']}。",
            )

    first = run_runtime_v5(
        context=_context(
            "给商务组的人员发条消息：测试内容",
            chat_id="chat_explicit_org_does_not_reuse_pending_action_target",
        ),
        providers={"people": PeopleProvider(), "im": object()},
    )

    assert first.composed.result_context is not None
    assert first.composed.result_context.result_type == "runtime_pending_confirmation"
    assert first.intent.entities["target"] == "汤冠男"

    second = run_runtime_v5(
        context=_context(
            "给IT组的人发条消息：测试内容",
            chat_id="chat_explicit_org_does_not_reuse_pending_action_target",
            result_context=first.composed.result_context,
            session_context={"runtime_v5_state": first.composed.result_context.metadata["runtime_state"]},
        ),
        providers={"people": PeopleProvider(), "im": object()},
    )

    assert calls == ["商务组", "IT组"]
    assert second.intent.intent == "message_send"
    assert second.intent.entities["organization_unit"] == "IT组"
    assert second.intent.entities["target_type"] == "person"
    assert second.intent.entities["target"] == "王云飞"
    assert second.intent.entities["target_open_id"] == "ou_wangyunfei"
    assert [item["name"] for item in second.intent.entities["people_targets"]] == ["王云飞"]
    assert "汤冠男" not in second.composed.answer


def test_runtime_v5_waiting_input_result_context_does_not_block_new_people_query_without_session_state() -> None:
    waiting = run_runtime_v5(
        context=_context(
            "给商务组的人员发条消息：测试内容",
            chat_id="chat_waiting_result_context_only",
        ),
        providers={"im": object()},
    )
    assert waiting.composed.result_context is not None
    assert waiting.composed.result_context.result_type == "runtime_waiting_input"

    result = run_runtime_v5(
        context=_context(
            "商务组有几人，分别是谁",
            chat_id="chat_waiting_result_context_only",
            result_context=waiting.composed.result_context,
        ),
        providers={"im": object()},
    )

    assert result.composed.result_context is not None
    assert result.composed.result_context.result_type != "runtime_waiting_input"
    assert "还没有拿到这批人的具体名单" not in result.composed.answer
    assert result.intent.intent in {"department_members", "organization_snapshot"}


def test_runtime_v5_conversation_first_single_im_send_waits_for_confirmation_then_executes() -> None:
    calls: list[dict] = []

    class IMProvider:
        source = "im"
        _OPERATIONS = {"send_message": ("feishu_im_send_message", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            calls.append(
                {
                    "operation": request.operation,
                    "target_type": request.params.get("target_type"),
                    "target": request.params.get("target"),
                    "text": request.params.get("text"),
                    "execution_identity": request.execution_identity,
                }
            )
            return ProviderResult(
                source="im",
                status="success",
                result_type="message_send",
                count=1,
                items=({"target": request.params.get("target"), "text": request.params.get("text")},),
                answer="消息已发送。",
            )

    waiting_confirmation = run_runtime_v5(
        context=_context("给王悦发消息说：下午开会", chat_id="chat_im_single_send"),
        providers={"im": IMProvider()},
    )

    assert calls == []
    assert waiting_confirmation.intent.intent == "message_send"
    assert waiting_confirmation.intent.entities["command_frame"]["route_path"] == "conversation_first_v1"
    assert waiting_confirmation.composed.result_context is not None
    assert waiting_confirmation.composed.result_context.result_type == "runtime_pending_confirmation"
    runtime_state = waiting_confirmation.composed.result_context.metadata["runtime_state"]

    executed = run_runtime_v5(
        context=_context(
            "确认执行",
            chat_id="chat_im_single_send",
            result_context=waiting_confirmation.composed.result_context,
            session_context={"runtime_v5_state": runtime_state},
        ),
        providers={"im": IMProvider()},
    )

    assert calls == [
        {
            "operation": "send_message",
            "target_type": "person",
            "target": "王悦",
            "text": "下午开会",
            "execution_identity": "user",
        }
    ]
    assert executed.execution is not None
    assert executed.execution.status == "success"


def test_runtime_v5_pending_confirmation_does_not_execute_on_smalltalk() -> None:
    calls: list[dict] = []

    class IMProvider:
        source = "im"
        _OPERATIONS = {"send_message": ("feishu_im_send_message", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            calls.append({"target": request.params.get("target"), "text": request.params.get("text")})
            return ProviderResult(source="im", status="success", result_type="message_send", count=1, answer="消息已发送。")

    waiting_confirmation = run_runtime_v5(
        context=_context("给王悦发消息说：下午开会", chat_id="chat_im_smalltalk_must_not_confirm"),
        providers={"im": IMProvider()},
    )

    assert waiting_confirmation.composed.result_context is not None
    assert waiting_confirmation.composed.result_context.result_type == "runtime_pending_confirmation"

    result = run_runtime_v5(
        context=_context(
            "你好",
            chat_id="chat_im_smalltalk_must_not_confirm",
            result_context=waiting_confirmation.composed.result_context,
            session_context={"runtime_v5_state": waiting_confirmation.composed.result_context.metadata["runtime_state"]},
        ),
        providers={"im": IMProvider()},
    )

    assert calls == []
    assert result.execution is None or result.execution.status == "skipped"
    assert "动作已完成" not in result.composed.answer


def test_runtime_v5_card_confirmation_reuses_saved_pending_action_target() -> None:
    calls: list[dict] = []

    class IMProvider:
        source = "im"
        _OPERATIONS = {"send_message": ("feishu_im_send_message", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            calls.append(
                {
                    "target_type": request.params.get("target_type"),
                    "target": request.params.get("target"),
                    "text": request.params.get("text"),
                    "execution_identity": request.execution_identity,
                }
            )
            return ProviderResult(source="im", status="success", result_type="message_send", count=1, answer="消息已发送。")

    waiting_confirmation = run_runtime_v5(
        context=_context("给王悦发消息说：下午开会", chat_id="chat_im_card_confirm_reuse_pending"),
        providers={"im": IMProvider()},
    )
    assert waiting_confirmation.composed.result_context is not None
    runtime_state = waiting_confirmation.composed.result_context.metadata["runtime_state"]
    action_id = waiting_confirmation.composed.result_context.metadata["action_id"]

    card_action_input = build_runtime_action_input_payload(
        action_id=action_id,
        action_type="execute",
        intent="message_send",
        strategy="message_send",
        company_id=str(waiting_confirmation.context.runtime_scope.active_company_id),
        target={},
        confirmed=True,
        confirmation_token=action_id,
        chat_id="chat_im_card_confirm_reuse_pending",
        open_id="ou_test",
        source_ui="feishu_card",
        message="确认执行",
        sources=("im",),
    )

    executed = run_runtime_v5(
        context=_context(
            "确认执行",
            chat_id="chat_im_card_confirm_reuse_pending",
            result_context=waiting_confirmation.composed.result_context,
            session_context={
                "runtime_v5_state": runtime_state,
                "runtime_v5_action_input": card_action_input,
            },
        ),
        providers={"im": IMProvider()},
    )

    assert calls == [
        {
            "target_type": "person",
            "target": "王悦",
            "text": "下午开会",
            "execution_identity": "user",
        }
    ]
    assert executed.execution is not None
    assert executed.execution.status == "success"


def test_runtime_v5_conversation_first_im_send_missing_text_asks_for_message_content() -> None:
    calls: list[dict] = []

    class IMProvider:
        source = "im"
        _OPERATIONS = {"send_message": ("feishu_im_send_message", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            calls.append(dict(request.params))
            return ProviderResult(source="im", status="success", result_type="message_send", count=1)

    result = run_runtime_v5(
        context=_context("给王悦发消息", chat_id="chat_im_missing_text"),
        providers={"im": IMProvider()},
    )

    assert calls == []
    assert result.intent.intent == "message_send"
    assert result.intent.entities["command_frame"]["route_path"] == "conversation_first_v1"
    assert result.intent.entities["target_type"] == "person"
    assert result.intent.entities["target"] == "王悦"
    assert result.intent.entities.get("text") in (None, "")
    assert result.intent.missing_params == ("text",)
    assert result.composed.result_context is not None
    assert result.composed.result_context.result_type == "runtime_waiting_input"
    assert "消息正文" in result.composed.answer
    assert "确认编号" not in result.composed.answer


def test_runtime_v5_conversation_first_im_missing_text_reply_enters_confirmation_then_executes() -> None:
    calls: list[dict] = []

    class IMProvider:
        source = "im"
        _OPERATIONS = {"send_message": ("feishu_im_send_message", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            calls.append(
                {
                    "target_type": request.params.get("target_type"),
                    "target": request.params.get("target"),
                    "text": request.params.get("text"),
                    "execution_identity": request.execution_identity,
                }
            )
            return ProviderResult(source="im", status="success", result_type="message_send", count=1, answer="消息已发送。")

    waiting_input = run_runtime_v5(
        context=_context("给王悦发消息", chat_id="chat_im_missing_text_flow"),
        providers={"im": IMProvider()},
    )

    assert calls == []
    assert waiting_input.composed.result_context is not None
    assert waiting_input.composed.result_context.result_type == "runtime_waiting_input"
    assert "消息正文" in waiting_input.composed.answer
    runtime_state = waiting_input.composed.result_context.metadata["runtime_state"]

    waiting_confirmation = run_runtime_v5(
        context=_context(
            "下午开会",
            chat_id="chat_im_missing_text_flow",
            result_context=waiting_input.composed.result_context,
            session_context={"runtime_v5_state": runtime_state},
        ),
        providers={"im": IMProvider()},
    )

    assert calls == []
    assert waiting_confirmation.intent.intent == "message_send"
    assert waiting_confirmation.intent.entities["target_type"] == "person"
    assert waiting_confirmation.intent.entities["target"] == "王悦"
    assert waiting_confirmation.intent.entities["text"] == "下午开会"
    assert waiting_confirmation.composed.result_context is not None
    assert waiting_confirmation.composed.result_context.result_type == "runtime_pending_confirmation"
    runtime_state = waiting_confirmation.composed.result_context.metadata["runtime_state"]

    executed = run_runtime_v5(
        context=_context(
            "确认",
            chat_id="chat_im_missing_text_flow",
            result_context=waiting_confirmation.composed.result_context,
            session_context={"runtime_v5_state": runtime_state},
        ),
        providers={"im": IMProvider()},
    )

    assert calls == [
        {
            "target_type": "person",
            "target": "王悦",
            "text": "下午开会",
            "execution_identity": "user",
        }
    ]
    assert executed.execution is not None
    assert executed.execution.status == "success"


def test_runtime_v5_conversation_first_im_missing_target_reply_enters_confirmation() -> None:
    calls: list[dict] = []

    class IMProvider:
        source = "im"
        _OPERATIONS = {"send_message": ("feishu_im_send_message", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            calls.append(dict(request.params))
            return ProviderResult(source="im", status="success", result_type="message_send", count=1)

    waiting_input = run_runtime_v5(
        context=_context("发消息说：收到", chat_id="chat_im_missing_target_flow"),
        providers={"im": IMProvider()},
    )

    assert calls == []
    assert waiting_input.composed.result_context is not None
    assert waiting_input.composed.result_context.result_type == "runtime_waiting_input"
    assert "要发给谁" in waiting_input.composed.answer
    runtime_state = waiting_input.composed.result_context.metadata["runtime_state"]

    waiting_confirmation = run_runtime_v5(
        context=_context(
            "王悦",
            chat_id="chat_im_missing_target_flow",
            result_context=waiting_input.composed.result_context,
            session_context={"runtime_v5_state": runtime_state},
        ),
        providers={"im": IMProvider()},
    )

    assert calls == []
    assert waiting_confirmation.intent.intent == "message_send"
    assert waiting_confirmation.intent.entities["target_type"] == "person"
    assert waiting_confirmation.intent.entities["target"] == "王悦"
    assert waiting_confirmation.intent.entities["text"] == "收到"
    assert waiting_confirmation.composed.result_context is not None
    assert waiting_confirmation.composed.result_context.result_type == "runtime_pending_confirmation"


def test_runtime_v5_conversation_first_current_chat_send_waits_for_confirmation_then_executes() -> None:
    calls: list[dict] = []

    class IMProvider:
        source = "im"
        _OPERATIONS = {"send_message": ("feishu_im_send_message", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            calls.append(
                {
                    "operation": request.operation,
                    "target_type": request.params.get("target_type"),
                    "text": request.params.get("text"),
                    "chat_id": request.context.chat_id,
                    "execution_identity": request.execution_identity,
                }
            )
            return ProviderResult(
                source="im",
                status="success",
                result_type="message_send",
                count=1,
                items=({"text": request.params.get("text")},),
                answer="消息已发送。",
            )

    waiting_confirmation = run_runtime_v5(
        context=_context("发到当前会话说：收到", chat_id="chat_im_current"),
        providers={"im": IMProvider()},
    )

    assert calls == []
    assert waiting_confirmation.intent.intent == "message_send"
    assert waiting_confirmation.intent.entities["command_frame"]["route_path"] == "conversation_first_v1"
    assert waiting_confirmation.intent.entities["target_type"] == "current_chat"
    assert waiting_confirmation.intent.entities["text"] == "收到"
    assert waiting_confirmation.composed.result_context is not None
    assert waiting_confirmation.composed.result_context.result_type == "runtime_pending_confirmation"
    runtime_state = waiting_confirmation.composed.result_context.metadata["runtime_state"]

    executed = run_runtime_v5(
        context=_context(
            "确认执行",
            chat_id="chat_im_current",
            result_context=waiting_confirmation.composed.result_context,
            session_context={"runtime_v5_state": runtime_state},
        ),
        providers={"im": IMProvider()},
    )

    assert calls == [
        {
            "operation": "send_message",
            "target_type": "current_chat",
            "text": "收到",
            "chat_id": "chat_im_current",
            "execution_identity": "bot",
        }
    ]
    assert executed.execution is not None
    assert executed.execution.status == "success"


@pytest.mark.parametrize(
    ("message", "target_type", "target"),
    (
        ("发给王悦说：收到", "person", "王悦"),
        ("发给测试群说：收到", "chat", "测试"),
        ("发到测试群说：收到", "chat", "测试"),
    ),
)
def test_runtime_v5_conversation_first_im_send_target_boundary_keeps_message_text_separate(
    message: str,
    target_type: str,
    target: str,
) -> None:
    result = run_runtime_v5(
        context=_context(message, chat_id="chat_im_target_boundary"),
        providers={"im": object()},
    )

    assert result.intent.intent == "message_send"
    assert result.intent.entities["command_frame"]["route_path"] == "conversation_first_v1"
    assert result.intent.entities["target_type"] == target_type
    assert result.intent.entities["target"] == target
    assert result.intent.entities["text"] == "收到"
    assert result.intent.missing_params == ()
    assert result.execution is None
    assert result.composed.result_context is not None
    assert result.composed.result_context.result_type == "runtime_pending_confirmation"


def test_runtime_v5_conversation_first_im_send_pronoun_uses_active_person_context() -> None:
    previous = ResultContext(
        result_type="people_search",
        count=1,
        items=({"name": "王云飞", "job_title": "IT 专员", "department": "IT组"},),
        metadata={"entity_domain": "People", "field_projection": "profile"},
        answer="王云飞是 IT 专员，IT组。",
    )

    result = run_runtime_v5(
        context=_context("发条信息给他:大飞哥测试", chat_id="chat_im_pronoun", result_context=previous),
        providers={"im": object()},
    )

    assert result.intent.intent == "message_send"
    assert result.intent.entities["command_frame"]["route_path"] == "conversation_first_v1"
    assert result.intent.entities["target_type"] == "person"
    assert result.intent.entities["target"] == "王云飞"
    assert result.intent.entities["text"] == "大飞哥测试"
    assert result.intent.missing_params == ()
    assert result.execution is None
    assert result.composed.result_context is not None
    assert result.composed.result_context.result_type == "runtime_pending_confirmation"


def test_runtime_v5_conversation_first_im_send_success_receipt_keeps_resolved_target() -> None:
    class IMProvider:
        source = "im"
        _OPERATIONS = {"send_message": ("feishu_im_send_message", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            return ProviderResult(
                source="im",
                status="success",
                result_type="message_send",
                count=1,
                items=({"target": "王悦", "text": request.params.get("text")},),
                metadata={
                    "operation": "send_message",
                    "target": "王悦",
                    "target_type": "person",
                    "target_query": request.params.get("target"),
                    "resolved_user_id": "ou_wangyue",
                    "resolved_target_name": "王悦",
                },
                answer="消息已发送。",
            )

    waiting_confirmation = run_runtime_v5(
        context=_context("发给王悦说：收到", chat_id="chat_im_receipt_success"),
        providers={"im": IMProvider()},
    )
    assert waiting_confirmation.composed.result_context is not None
    runtime_state = waiting_confirmation.composed.result_context.metadata["runtime_state"]

    executed = run_runtime_v5(
        context=_context(
            "确认",
            chat_id="chat_im_receipt_success",
            result_context=waiting_confirmation.composed.result_context,
            session_context={"runtime_v5_state": runtime_state},
        ),
        providers={"im": IMProvider()},
    )

    assert executed.execution is not None
    assert executed.execution.status == "success"
    assert executed.composed.result_context is not None
    assert executed.composed.result_context.result_type == "runtime_action"
    item = executed.composed.result_context.items[0]
    assert item["status"] == "success"
    assert item["target"] == "王悦"
    assert item["target_type"] == "person"
    assert item["target_query"] == "王悦"
    assert item["resolved_user_id"] == "ou_wangyue"
    assert item["resolved_target_name"] == "王悦"


@pytest.mark.parametrize(
    ("message", "result_type", "operation", "error", "error_type", "target_type", "target_query", "answer"),
    (
        ("发给王悦说：收到", "person_resolve", "search_person", "ambiguous_or_missing_person", "ambiguous_target", "person", "王悦", "没有找到人员“王悦”。"),
        ("发给测试群说：收到", "chat_resolve", "search_chats", "ambiguous_or_missing_chat", "ambiguous_target", "chat", "测试", "没有找到群聊“测试”。"),
    ),
)
def test_runtime_v5_conversation_first_im_send_failure_receipt_keeps_resolution_error(
    message: str,
    result_type: str,
    operation: str,
    error: str,
    error_type: str,
    target_type: str,
    target_query: str,
    answer: str,
) -> None:
    class IMProvider:
        source = "im"
        _OPERATIONS = {"send_message": ("feishu_im_send_message", True)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            return ProviderResult(
                source="im",
                status="error",
                result_type=result_type,
                count=0,
                items=(),
                metadata={
                    "operation": operation,
                    "target_type": target_type,
                    "target_query": target_query,
                    "error_type": error_type,
                },
                answer=answer,
                error=error,
            )

    waiting_confirmation = run_runtime_v5(
        context=_context(message, chat_id=f"chat_im_receipt_failure_{target_type}"),
        providers={"im": IMProvider()},
    )
    assert waiting_confirmation.composed.result_context is not None
    runtime_state = waiting_confirmation.composed.result_context.metadata["runtime_state"]

    executed = run_runtime_v5(
        context=_context(
            "确认",
            chat_id=f"chat_im_receipt_failure_{target_type}",
            result_context=waiting_confirmation.composed.result_context,
            session_context={"runtime_v5_state": runtime_state},
        ),
        providers={"im": IMProvider()},
    )

    assert executed.execution is not None
    assert executed.execution.status == "error"
    assert answer in executed.composed.answer
    assert executed.composed.result_context is not None
    assert executed.composed.result_context.result_type == "runtime_action"
    assert executed.composed.result_context.metadata["execution_status"] == "error"
    item = executed.composed.result_context.items[0]
    assert item["status"] == "error"
    assert item["operation"] == operation
    assert item["error"] == error
    assert item["error_type"] == error_type
    assert item["target_type"] == target_type
    assert item["target_query"] == target_query
    assert item["status_group"] == "terminal"


def test_runtime_v5_unavailable_people_context_message_stays_waiting_until_executor_exists() -> None:
    result_context = ResultContext(
        result_type="people_search",
        count=2,
        items=(
            {"name": "张三", "open_id": "ou_zhang", "email": "zhangsan@example.com"},
            {"name": "李四", "open_id": "ou_li", "email": "lisi@example.com"},
        ),
        metadata={"context_kind": "query_result", "entity_domain": "people"},
        answer="上一轮人员结果。",
    )

    waiting_input = run_runtime_v5(
        context=_context(
            "用机器人发给这些人说：明天上午提交周报",
            chat_id="chat_people_context_guard",
            result_context=result_context,
        ),
        providers={"im": object()},
    )

    assert waiting_input.execution is None
    assert waiting_input.composed.result_context is not None
    assert waiting_input.composed.result_context.result_type == "runtime_waiting_input"
    assert "当前多人目标只开放" in waiting_input.composed.answer
    runtime_state = waiting_input.composed.result_context.metadata["runtime_state"]

    confirmed = run_runtime_v5(
        context=_context(
            "确认执行",
            chat_id="chat_people_context_guard",
            session_context={"runtime_v5_state": runtime_state},
        ),
        providers={"im": object()},
    )

    assert confirmed.execution is None
    assert confirmed.composed.result_context is not None
    assert confirmed.composed.result_context.result_type == "runtime_waiting_input"
    assert "当前多人目标只开放" in confirmed.composed.answer


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
        permission=PermissionDecision(allowed=True, requires_confirmation=True, execution_identity="user"),
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
    assert waiting_input.metadata["requires_confirmation"] is False
    assert waiting_input.metadata["policy_requires_confirmation"] is True
    assert waiting_input.metadata["response_policy"]["reason"] != "confirmation_must_not_wait_for_llm"

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


def test_confirmation_card_includes_people_target_and_delivery_mode() -> None:
    card = build_interactive_card(
        card_hint="confirmation",
        route_path="runtime_v5_confirmation",
        raw_answer=(
            "动作：发送飞书消息\n"
            "请求：给商务组的人分别发条消息：测试内容\n"
            "人员目标：1 人：汤冠男\n"
            "发送方式：以本人身份分别发送\n"
            "消息摘要：测试内容\n"
            "确认编号：confirm_1\n"
        ),
        chat_id="oc_card_people_target",
    )

    summary = card["elements"][0]["text"]["content"]
    assert "人员目标：1 人：汤冠男" in summary
    assert "发送方式：以本人身份分别发送" in summary


def test_runtime_result_builder_exposes_generic_sidepanel_for_large_people_result() -> None:
    permission = PermissionDecision(allowed=True, requires_confirmation=False, execution_identity="bot")
    items = tuple(
        {"name": f"员工{index}", "title": "工程师", "mobile": f"1380000{index:04d}", "open_id": f"ou_{index}"}
        for index in range(1, 23)
    )

    result = build_runtime_result(
        command_plan=_command_plan("people_search", result_type="people_search", sources=("people",), target_ui="none"),
        permission=permission,
        execution=None,
        composed=ComposedAnswer(
            answer="公司通讯录里现有 22 人。",
            result_context=ResultContext(
                result_type="people_search",
                count=len(items),
                items=items,
                metadata={
                    "context_kind": "query_result",
                    "entity_domain": "people",
                    "display_offset": 0,
                    "display_end": 20,
                    "display_limit": 20,
                    "has_more": True,
                },
            ),
        ),
    )

    sidepanel = result.metadata["sidepanel_context"]

    assert result.target_ui == "none"
    assert [action["action"] for action in result.actions] == ["open_sidepanel"]
    assert result.actions[0]["target_ui"] == "sidepanel"
    assert result.actions[0]["requires_confirmation"] is False
    assert sidepanel["kind"] == "result_context"
    assert sidepanel["presentation"] == "table_detail"
    assert sidepanel["result_type"] == "people_search"
    assert sidepanel["entity_domain"] == "people"
    assert sidepanel["item_count"] == 22
    assert sidepanel["display_end"] == 20
    assert sidepanel["has_more"] is True
    assert "mobile" in sidepanel["visible_fields"]
    assert "open_id" not in sidepanel["visible_fields"]


def test_runtime_result_builder_renders_people_detail_as_card_entry() -> None:
    permission = PermissionDecision(allowed=True, requires_confirmation=False, execution_identity="bot")
    items = tuple({"name": f"员工{index}", "title": "工程师", "mobile": f"1380000{index:04d}"} for index in range(1, 12))

    result = build_runtime_result(
        command_plan=_command_plan("people_search", result_type="people_search", sources=("people",), target_ui="none"),
        permission=permission,
        execution=None,
        composed=ComposedAnswer(
            answer="这组结果共有 11 人，名单我放到侧边栏里，聊天里不展开长清单。",
            result_context=ResultContext(
                result_type="people_search",
                count=len(items),
                items=items,
                metadata={
                    "context_kind": "query_result",
                    "entity_domain": "people",
                    "field_projection": "name_only",
                    "result_context_presentation": "detail",
                    "display_offset": 0,
                    "display_end": 11,
                    "display_limit": 20,
                },
            ),
        ),
    )

    assert result.target_ui == "card"
    assert [action["action"] for action in result.actions] == ["open_sidepanel"]
    assert result.metadata["sidepanel_context"]["item_count"] == 11


def test_runtime_result_builder_skips_generic_sidepanel_for_small_people_result() -> None:
    permission = PermissionDecision(allowed=True, requires_confirmation=False, execution_identity="bot")

    result = build_runtime_result(
        command_plan=_command_plan("people_search", result_type="people_search", sources=("people",), target_ui="none"),
        permission=permission,
        execution=None,
        composed=ComposedAnswer(
            answer="找到 1 人。",
            result_context=ResultContext(
                result_type="people_search",
                count=1,
                items=({"name": "王五", "title": "工程师"},),
                metadata={"context_kind": "query_result", "entity_domain": "people"},
            ),
        ),
    )

    assert result.actions == ()
    assert result.metadata["sidepanel_context"] == {}


def test_runtime_result_builder_skips_sidepanel_for_single_people_field_summary() -> None:
    permission = PermissionDecision(allowed=True, requires_confirmation=False, execution_identity="bot")

    result = build_runtime_result(
        command_plan=_command_plan("people_search", result_type="people_search", sources=("people",), target_ui="none"),
        permission=permission,
        execution=None,
        composed=ComposedAnswer(
            answer="吴健的手机号是 +8615050181517。",
            result_context=ResultContext(
                result_type="people_search",
                count=1,
                items=(
                    {
                        "name": "吴健",
                        "title": "中级机械工程师",
                        "department": "机械部",
                        "mobile": "+8615050181517",
                        "email": "wu@example.com",
                    },
                ),
                metadata={
                    "context_kind": "query_result",
                    "entity_domain": "people",
                    "people_query_field": "mobile",
                    "result_context_presentation": "summary",
                    "people_context_frame": {
                        "current_person": "吴健",
                        "current_requested_field": "mobile",
                        "identity_resolution": "exact",
                    },
                },
            ),
        ),
    )

    assert result.target_ui == "none"
    assert result.actions == ()
    assert result.metadata["sidepanel_context"] == {}


def test_runtime_result_card_skips_open_sidepanel_when_target_ui_none() -> None:
    runtime_result = RuntimeResult(
        result_type="people_search",
        status="success",
        title="人员明细",
        summary="王云飞的手机号是 +8618351080012。",
        target_ui="none",
        actions=(
            {
                "action": "open_sidepanel",
                "label": "打开侧边栏",
                "target_ui": "sidepanel",
                "route": "/sidepanel",
            },
        ),
        metadata={
            "sidepanel_context": {
                "kind": "result_context",
                "title": "人员明细",
                "item_count": 1,
            }
        },
    )

    assert build_runtime_result_card(runtime_result_payload(runtime_result)) is None


def test_runtime_result_builder_skips_sidepanel_for_text_presentation_contract() -> None:
    permission = PermissionDecision(allowed=True, requires_confirmation=False, execution_identity="bot")
    command_plan = build_command_plan(context=_context("王云飞的手机号是多少"))
    items = (
        {
            "name": "王云飞",
            "title": "IT专员",
            "department": "IT组",
            "mobile": "+8618351080012",
            "email": "wang@example.com",
        },
    )

    result = build_runtime_result(
        command_plan=command_plan,
        permission=permission,
        execution=None,
        composed=ComposedAnswer(
            answer="王云飞的手机号是 +8618351080012。",
            result_context=ResultContext(
                result_type="people_search",
                count=1,
                items=items,
                metadata={"context_kind": "query_result", "entity_domain": "people"},
            ),
        ),
    )

    assert result.target_ui == "none"
    assert result.actions == ()
    assert result.metadata["sidepanel_context"] == {}


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
    assert payload["metadata"]["response_policy"] == {
        "response_mode": "instant",
        "llm_allowed": False,
        "async_followup_allowed": False,
        "latency_budget_ms": 800,
        "reason": "business_result_must_reply_fast",
    }


def test_runtime_result_payload_includes_command_enrichment() -> None:
    permission = PermissionDecision(allowed=True, requires_confirmation=False, execution_identity="bot")
    command_plan = _command_plan("task_query", result_type="task_query", sources=("task",), data_scope="company")
    enriched_intent = IntentResult(
        question_type="query",
        intent="task_query",
        data_scope="company",
        entities={
            "command_enrichment": {
                "business_domain": "Workspace",
                "capability": "task_query",
                "objective": "查看公司任务负荷",
                "constraints": {"status": "open"},
                "time_range": {"preset": "current"},
                "output_preferences": {"detail_level": "summary", "group_by": "owner"},
                "semantic_tags": ["workload", "risk"],
                "unsafe_extra": "ignored",
            }
        },
        confidence=0.9,
        canonical_question="查看公司任务负荷",
    )
    command_plan = CommandPlan(
        intent=command_plan.intent,
        steps=command_plan.steps,
        target_ui=command_plan.target_ui,
        tool_candidates=command_plan.tool_candidates,
        context_scope=command_plan.context_scope,
        intent_result=enriched_intent,
        planner_result=command_plan.planner_result,
    )

    result = build_runtime_result(
        command_plan=command_plan,
        permission=permission,
        execution=None,
        composed=ComposedAnswer(
            answer="公司任务聚合。",
            result_context=ResultContext(result_type="task_list", count=1),
        ),
    )

    enrichment = runtime_result_payload(result)["metadata"]["command_enrichment"]

    assert enrichment == {
        "business_domain": "Workspace",
        "capability": "task_query",
        "objective": "查看公司任务负荷",
        "constraints": {"status": "open"},
        "time_range": {"preset": "current"},
        "output_preferences": {"detail_level": "summary", "group_by": "owner"},
        "semantic_tags": ["workload", "risk"],
    }


def test_runtime_result_payload_includes_command_frame() -> None:
    permission = PermissionDecision(allowed=True, requires_confirmation=False, execution_identity="bot")
    command_plan = build_command_plan(context=_context("全公司任务"))

    result = build_runtime_result(
        command_plan=command_plan,
        permission=permission,
        execution=None,
        composed=ComposedAnswer(
            answer="公司任务聚合。",
            result_context=ResultContext(result_type="task_query", count=1),
        ),
    )

    frame = runtime_result_payload(result)["metadata"]["command_frame"]

    assert frame["intent"] == "task_query"
    assert frame["dialogue_mode"] == "present"
    assert frame["domain"] == "Workspace"
    assert frame["skill_intent"] == "task_query"
    assert frame["gates"]["utterance"]["type"] == "business_query"
    assert frame["gates"]["domain"]["domain"] == "Workspace"
    assert frame["gates"]["action"]["type"] == "read"


def test_runtime_result_response_policy_allows_llm_for_smalltalk() -> None:
    permission = PermissionDecision(allowed=True, requires_confirmation=False, execution_identity="bot")
    command_plan = _command_plan("smalltalk", result_type="smalltalk", sources=(), data_scope="self")
    command_plan = CommandPlan(
        intent=command_plan.intent,
        steps=command_plan.steps,
        target_ui=command_plan.target_ui,
        tool_candidates=command_plan.tool_candidates,
        context_scope=command_plan.context_scope,
        intent_result=IntentResult(
            question_type="query",
            intent="smalltalk",
            data_scope="self",
            confidence=1.0,
            canonical_question="你好",
        ),
        planner_result=command_plan.planner_result,
    )

    result = build_runtime_result(
        command_plan=command_plan,
        permission=permission,
        execution=None,
        composed=ComposedAnswer(answer="我在。", result_context=ResultContext(result_type="smalltalk", count=0)),
    )

    policy = runtime_result_payload(result)["metadata"]["response_policy"]

    assert policy["response_mode"] == "llm_enhanced"
    assert policy["llm_allowed"] is True
    assert policy["latency_budget_ms"] == 6000
    assert policy["reason"] == "smalltalk_can_use_bounded_conversation_llm"


def test_runtime_result_response_policy_keeps_operational_query_instant() -> None:
    permission = PermissionDecision(allowed=True, requires_confirmation=False, execution_identity="bot")
    command_plan = _command_plan("people_search", result_type="people_search", sources=("people",), data_scope="self")

    result = build_runtime_result(
        command_plan=command_plan,
        permission=permission,
        execution=None,
        composed=ComposedAnswer(answer="找到 1 个人员。", result_context=ResultContext(result_type="people_search", count=1)),
    )

    policy = runtime_result_payload(result)["metadata"]["response_policy"]

    assert policy["response_mode"] == "instant"
    assert policy["llm_allowed"] is False
    assert policy["reason"] == "business_result_must_reply_fast"


def test_runtime_result_response_policy_keeps_permission_boundary_instant_for_any_scope() -> None:
    permission = PermissionDecision(allowed=True, requires_confirmation=False, execution_identity="bot")
    command_plan = _command_plan("calendar_query", result_type="calendar_query", sources=("calendar",), data_scope="self")

    result = build_runtime_result(
        command_plan=command_plan,
        permission=permission,
        execution=None,
        composed=ComposedAnswer(
            answer="日程实时读取能力还没有接入 Bot/Tenant 主路径。",
            result_context=ResultContext(result_type="calendar_query", count=0),
        ),
    )

    policy = runtime_result_payload(result)["metadata"]["response_policy"]

    assert policy["response_mode"] == "instant"
    assert policy["llm_allowed"] is False
    assert policy["reason"] == "scope_or_permission_boundary_must_reply_fast"


def test_runtime_result_response_policy_sends_analysis_to_async_followup() -> None:
    permission = PermissionDecision(allowed=True, requires_confirmation=False, execution_identity="bot")
    command_plan = _command_plan(
        "general_analysis",
        result_type="general_analysis",
        question_type="analysis",
        sources=("workevent",),
        data_scope="company",
    )

    result = build_runtime_result(
        command_plan=command_plan,
        permission=permission,
        execution=None,
        composed=ComposedAnswer(answer="正在分析公司风险。", result_context=ResultContext(result_type="general_analysis", count=0)),
    )

    policy = runtime_result_payload(result)["metadata"]["response_policy"]

    assert policy["response_mode"] == "async_followup"
    assert policy["llm_allowed"] is False
    assert policy["async_followup_allowed"] is True


def test_runtime_v5_composer_uses_command_enrichment_for_query_answer() -> None:
    intent = IntentResult(
        question_type="query",
        intent="task_query",
        data_scope="company",
        entities={
            "command_enrichment": {
                "objective": "查看公司任务负荷和风险",
                "output_preferences": {"detail_level": "summary", "group_by": "owner"},
                "semantic_tags": ["workload", "risk"],
            }
        },
        confidence=0.9,
        canonical_question="查看公司任务负荷",
    )
    result_context = ResultContext(
        result_type="task_list",
        count=1,
        items=({"title": "出差西安", "status": "todo", "task_guid": "task/1"},),
        answer="任务 1 个。",
    )

    composed = compose_answer(
        context=_context("全公司任务"),
        intent=intent,
        permission=PermissionDecision(allowed=True),
        execution=ExecutionResult(
            strategy="task_query",
            status="success",
            provider_results=(ProviderResult(source="task", status="success", result_type="task_list", count=1),),
            result_context=result_context,
        ),
    )

    assert composed.answer.startswith("我先按你的问题整理当前可见结果：查看公司任务负荷和风险。")
    assert "任务 1 个。" in composed.answer
    assert "可继续问" in composed.answer


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


def test_runtime_result_policy_filter_hides_self_resource_for_other_user() -> None:
    result = build_runtime_result(
        command_plan=_command_plan("task_query", result_type="task_query", sources=("task",)),
        permission=PermissionDecision(
            allowed=True,
            requires_confirmation=False,
            execution_identity="bot",
            metadata={
                "policy_subject": {"actor_user_id": "user_2", "actor_open_id": "ou_2", "company_id": "company_1"},
                "policy_scope": {"requested_scope": "self", "resolved_scope": "self"},
                "identity_decision": {"actor_identity": "BOT", "credential_mode": "TENANT_TOKEN"},
                "allowed_resource_types": ["task"],
            },
        ),
        execution=None,
        composed=ComposedAnswer(
            answer="你有 1 条任务。",
            result_context=ResultContext(
                result_type="task_list",
                count=1,
                items=(
                    {
                        "resource_plane": "operational",
                        "resource_type": "task",
                        "title": "私有任务",
                        "visibility_scope": "SELF",
                        "owner_open_id": "ou_1",
                        "allowed_user_ids": ["ou_1"],
                    },
                ),
            ),
        ),
    )

    payload = runtime_result_payload(result)
    policy_filter = payload["metadata"]["policy_result_filter"]

    assert payload["items"] == []
    assert policy_filter["redaction_applied"] is True
    assert policy_filter["resource_filters"][0]["visible"] is False


def test_runtime_result_policy_filter_uses_management_scope_for_department_resources() -> None:
    result = build_runtime_result(
        command_plan=_command_plan("task_query", result_type="task_query", sources=("task",), data_scope="department"),
        permission=PermissionDecision(
            allowed=True,
            requires_confirmation=False,
            execution_identity="bot",
            metadata={
                "policy_subject": {
                    "actor_open_id": "ou_manager",
                    "company_id": "company_1",
                    "departments": ["dept_1"],
                    "management_scope": [{"scope": "DEPARTMENT", "department_id": "dept_1", "department_name": "组织部"}],
                },
                "policy_scope": {"requested_scope": "department", "resolved_scope": "department"},
                "identity_decision": {"actor_identity": "BOT", "credential_mode": "TENANT_TOKEN"},
                "allowed_resource_types": ["task"],
            },
        ),
        execution=None,
        composed=ComposedAnswer(
            answer="部门任务。",
            result_context=ResultContext(
                result_type="task_list",
                count=2,
                items=(
                    {
                        "resource_plane": "operational",
                        "resource_type": "task",
                        "title": "本部门任务",
                        "visibility_scope": "DEPARTMENT",
                        "owner_department_id": "dept_1",
                    },
                    {
                        "resource_plane": "operational",
                        "resource_type": "task",
                        "title": "其他部门任务",
                        "visibility_scope": "DEPARTMENT",
                        "owner_department_id": "dept_2",
                    },
                ),
            ),
        ),
    )

    payload = runtime_result_payload(result)
    policy_filter = payload["metadata"]["policy_result_filter"]

    assert [item["title"] for item in payload["items"]] == ["本部门任务"]
    assert policy_filter["redaction_applied"] is True
    assert policy_filter["resource_filters"][0]["visible"] is True
    assert policy_filter["resource_filters"][1]["visible"] is False


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


def test_workspace_cognitive_aggregation_interaction_payload_hides_source_references() -> None:
    result = build_runtime_result(
        command_plan=_command_plan("task_query", result_type="task_query", sources=("task",), data_scope="department"),
        permission=PermissionDecision(
            allowed=True,
            requires_confirmation=False,
            execution_identity="bot",
            metadata={
                "policy_scope": {"requested_scope": "department", "resolved_scope": "department"},
                "identity_decision": {"actor_identity": "BOT", "credential_mode": "TENANT_TOKEN"},
                "allowed_resource_types": ["task"],
            },
        ),
        execution=None,
        composed=ComposedAnswer(
            answer="部门 Workspace 认知聚合。",
            result_context=ResultContext(
                result_type="workspace_aggregation_summary",
                count=1,
                items=(
                    {
                        "resource_plane": "cognitive",
                        "resource_type": "workspace_aggregation",
                        "title": "Workspace department 认知聚合",
                        "summary": "DEPARTMENT 范围：任务 1 个。",
                        "metrics": {"task_total": 1},
                        "source_event_ids": ["event_1"],
                        "source_object_id": "company_1:department",
                        "source_object_type": "workspace_aggregation",
                        "source_system": "digital_advisor",
                        "raw": {"source_object_id": "event_raw_1", "internal_note": "hidden"},
                    },
                ),
            ),
        ),
    )

    runtime_payload = runtime_result_payload(result)
    interaction_payload = interaction_payload_payload(interaction_payload_from_runtime_result(result))

    assert runtime_payload["metadata"]["policy_result_filter"]["aggregation_only"] is True
    assert runtime_payload["metadata"]["policy_result_filter"]["source_reference_visible"] is False
    assert runtime_payload["metadata"]["policy_result_filter"]["redaction_applied"] is True
    assert runtime_payload["items"] == [
        {
            "resource_plane": "cognitive",
            "resource_type": "workspace_aggregation",
            "title": "Workspace department 认知聚合",
            "summary": "DEPARTMENT 范围：任务 1 个。",
            "metrics": {"task_total": 1},
        }
    ]
    assert interaction_payload["payload_type"] == "summary"
    assert interaction_payload["items"] == runtime_payload["items"]
    assert "source_event_ids" not in interaction_payload["items"][0]
    assert "raw" not in interaction_payload["items"][0]


def test_runtime_v5_workspace_company_query_filters_cognitive_aggregation_before_interaction_payload() -> None:
    class WorkspaceAggregationProvider:
        source = "task"
        _OPERATIONS = {"list_my_tasks": ("task.list_my_tasks", False)}

        def execute(self, request: ProviderRequest) -> ProviderResult:
            return ProviderResult(
                source="task",
                status="success",
                result_type="workspace_aggregation_summary",
                count=1,
                items=(
                    {
                        "resource_plane": "cognitive",
                        "resource_type": "workspace_aggregation",
                        "title": "Workspace company 认知聚合",
                        "summary": "COMPANY 范围：任务 2 个。",
                        "metrics": {"task_total": 2, "overdue_task_count": 1},
                        "source_event_ids": ["event_1", "event_2"],
                        "source_object_id": "company_1:company",
                        "source_object_type": "workspace_aggregation",
                        "source_system": "digital_advisor",
                        "raw": {"source_object_id": "raw_event_1", "private_detail": "hidden"},
                    },
                ),
                metadata={"provider_boundary": "workspace_cognitive_aggregation"},
                answer="COMPANY 范围：任务 2 个，逾期 1 个。",
            )

    result = run_runtime_v5(
        context=_context("查看全公司任务"),
        providers={"task": WorkspaceAggregationProvider()},
    )

    runtime_payload = result.composed.metadata["runtime_result"]
    interaction_payload = interaction_payload_payload(
        interaction_payload_from_runtime_result(runtime_result_from_payload(runtime_payload))
    )

    assert result.intent.intent == "task_query"
    assert result.intent.data_scope == "company"
    assert runtime_payload["result_type"] == "workspace_aggregation_summary"
    assert runtime_payload["metadata"]["scope_context"]["scope"] == "COMPANY"
    assert runtime_payload["metadata"]["policy_result_filter"]["aggregation_only"] is True
    assert runtime_payload["metadata"]["policy_result_filter"]["source_reference_visible"] is False
    item = runtime_payload["items"][0]
    assert item["resource_plane"] == "cognitive"
    assert item["resource_type"] == "workspace_aggregation"
    assert item["title"] == "Workspace company 认知聚合"
    assert item["summary"] == "COMPANY 范围：任务 2 个。"
    assert item["metrics"] == {"task_total": 2, "overdue_task_count": 1}
    assert item["visibility_scope"] == "COMPANY"
    assert item["company_id"] == runtime_payload["metadata"]["company_id"]
    assert "source_event_ids" not in item
    assert "source_object_id" not in item
    assert "source_object_type" not in item
    assert "source_system" not in item
    assert "raw" not in item
    assert interaction_payload["payload_type"] == "summary"
    assert interaction_payload["items"] == runtime_payload["items"]
    assert "source_event_ids" not in interaction_payload["items"][0]
    assert "raw" not in interaction_payload["items"][0]


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


def test_interaction_payload_preserves_contextual_intro() -> None:
    payload = interaction_payload_from_runtime_result(
        RuntimeResult(
            result_type="task_list",
            status="success",
            title="任务",
            summary="你有 2 条任务。",
            contextual_intro="我先按你当前可见范围整理一下任务。",
            followup_suggestions=("第一个详情", "按优先级排一下"),
            target_ui="card",
            metadata={"company_id": "company_1"},
        )
    )

    serialized = interaction_payload_payload(payload)

    assert payload.contextual_intro == "我先按你当前可见范围整理一下任务。"
    assert payload.followup_suggestions == ("第一个详情", "按优先级排一下")
    assert serialized["contextual_intro"] == "我先按你当前可见范围整理一下任务。"
    assert serialized["followup_suggestions"] == ["第一个详情", "按优先级排一下"]


def test_runtime_result_uses_command_objective_as_contextual_intro() -> None:
    command_plan = _command_plan("task_query", result_type="task_list", sources=("task",))
    command_plan = CommandPlan(
        **{
            **command_plan.__dict__,
            "intent_result": IntentResult(
                question_type="query",
                intent="task_query",
                data_scope="self",
                confidence=0.9,
                canonical_question="查看我的任务",
                entities={"command_enrichment": {"objective": "查看当前任务优先级"}},
            ),
        }
    )
    result = build_runtime_result(
        command_plan=command_plan,
        permission=PermissionDecision(allowed=True, requires_confirmation=False, execution_identity="bot"),
        execution=ExecutionResult(
            strategy="task_query",
            status="success",
            provider_results=(),
            result_context=ResultContext(result_type="task_list", count=0, answer="暂无任务。", metadata={"company_id": "company_1"}),
        ),
        composed=ComposedAnswer(
            answer="暂无任务。",
            result_context=ResultContext(result_type="task_list", count=0, answer="暂无任务。", metadata={"company_id": "company_1"}),
        ),
    )

    assert result.contextual_intro == "我先按你的问题整理当前可见结果：查看当前任务优先级。"
    assert result.followup_suggestions == ("第一个详情", "按优先级排一下", "还有哪些快到期")
    assert runtime_result_payload(result)["followup_suggestions"] == ["第一个详情", "按优先级排一下", "还有哪些快到期"]


def test_runtime_result_card_renders_contextual_intro_before_summary() -> None:
    card = build_runtime_result_card(
        {
            "result_type": "task_list",
            "title": "任务",
            "summary": "你有 1 条任务。",
            "contextual_intro": "我先按你当前可见范围整理一下任务。",
            "followup_suggestions": ["第一个详情", "按优先级排一下"],
            "items": [{"title": "跟进客户", "status": "todo"}],
            "actions": [],
        },
        chat_id="oc_1",
    )

    assert card is not None
    elements = card["elements"]
    assert elements[0]["text"]["content"] == "我先按你当前可见范围整理一下任务。"
    assert elements[-1]["elements"][0]["content"] == "可继续问：第一个详情 / 按优先级排一下"


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


def test_runtime_v5_ambiguous_forward_phrase_stays_conversational() -> None:
    result = run_runtime_v5(context=_context("帮忙转一下"), providers={})

    assert result.intent.intent == "smalltalk"
    assert result.intent.missing_params == ()
    assert "发给谁" in result.composed.answer
    assert "必要信息" not in result.composed.answer
    assert "target_type" not in result.composed.answer


def test_runtime_v5_non_work_life_request_does_not_inherit_business_context() -> None:
    result = run_runtime_v5(
        context=_context(
            "我要点外卖，不是任务。",
            result_context=ResultContext(
                result_type="task_list",
                count=1,
                items=({"title": "测试任务", "status": "todo"},),
                answer="你有 1 条任务。",
            ),
        ),
        providers={},
    )

    assert result.intent.intent == "smalltalk"
    assert result.intent.missing_params == ()
    assert "生活需求" in result.composed.answer
    assert "任务查询" not in result.composed.answer


def test_runtime_v5_message_send_missing_params_use_human_labels() -> None:
    answer = compose_answer(
        context=_context("帮我发消息"),
        intent=IntentResult(
            question_type="action",
            intent="message_send",
            data_scope="self",
            missing_params=("target_type", "text"),
            confidence=0.59,
            canonical_question="帮我发消息",
        ),
        permission=PermissionDecision(allowed=True),
        execution=None,
    )

    assert "发送对象" in answer.answer
    assert "消息内容" in answer.answer
    assert "必要信息" not in answer.answer
    assert "target_type" not in answer.answer
