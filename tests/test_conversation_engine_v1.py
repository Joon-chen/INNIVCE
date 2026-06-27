from __future__ import annotations

from uuid import UUID

import pytest

from app.services.runtime_v5.command_layer import build_command_plan
from app.services.runtime_v5.composer import compose_answer
from app.services.runtime_v5.conversation_hints import build_conversation_hints
from app.services.runtime_v5.conversation_state import build_conversation_state
from app.services.runtime_v5.dialogue_resolver import (
    conversation_first_intent_result,
    resolve_dialogue_to_command_frame,
)
from app.services.runtime_v5.models import ExecutionResult, PermissionDecision, ProviderResult, ResultContext, RuntimeContext, RuntimeIdentity, RuntimeScope
from app.services.runtime_v5.permission import check_runtime_permission
from app.services.runtime_v5.planner import plan_task
from app.services.runtime_v5.response_orchestration import build_response_policy
from app.services.runtime_v5.runtime_result import build_runtime_result
from app.services.runtime_v5.semantic_frame import understand_semantics


COMPANY_ID = UUID("091fb8ae-443d-49b2-9c67-5e5f1353f2d5")


def _context(
    message: str,
    *,
    result_context: ResultContext | None = None,
    session_context: dict | None = None,
) -> RuntimeContext:
    return RuntimeContext(
        identity=RuntimeIdentity(open_id="ou_test", role="owner", display_name="陈董", domains=("all",)),
        runtime_scope=RuntimeScope(company_ids=(COMPANY_ID,), active_company_id=COMPANY_ID),
        current_message=message,
        session_context=session_context or {},
        result_context=result_context,
        chat_id="chat_1",
    )


PEOPLE_RESULT = ResultContext(
    result_type="organization_snapshot",
    count=47,
    items=({"name": "王悦", "job_title": "项目经理"}, {"name": "陈俊", "job_title": "董事长"}),
    metadata={"entity_domain": "People", "result_context_presentation": "summary", "field_projection": "count_only"},
    answer="47人。",
)
MALE_RESULT = ResultContext(
    result_type="organization_snapshot",
    count=24,
    items=({"name": "王云飞"}, {"name": "吴健"}),
    metadata={"entity_domain": "People", "people_filter": {"filter": "gender", "value": "male"}, "field_projection": "name_only"},
    answer="24位男性员工。",
)
PERSON_PHONE_RESULT = ResultContext(
    result_type="people_search",
    count=1,
    items=({"name": "王悦", "mobile": "+8618061834925"},),
    metadata={"entity_domain": "People", "field_projection": "mobile", "result_context_presentation": "summary"},
    answer="王悦的手机号是 +8618061834925。",
)
KNOWLEDGE_RESULT = ResultContext(
    result_type="knowledge_search",
    count=3,
    items=({"title": "公司介绍"},),
    metadata={"entity_domain": "Knowledge"},
    answer="找到公司介绍资料。",
)
PENDING_SEND = {
    "runtime_v5_pending_action": {
        "intent": "message_send",
        "strategy": "message_send",
        "message": "发给这些人",
        "missing_params": ["content"],
        "confirmation_token": "token_1",
    }
}


CASES = [
    ("公司有多少人", None, "", "People", "ask", "count", "organization_snapshot", "organization", True, False, "instant", "47"),
    ("公司有多少人，只回答人数", None, "", "People", "ask", "count", "organization_snapshot", "organization", True, False, "instant", "47"),
    ("公司多少人，只回数字", None, "", "People", "ask", "count", "organization_snapshot", "organization", True, False, "instant", "47"),
    ("我们公司多少人了", None, "", "People", "ask", "count", "organization_snapshot", "organization", True, False, "instant", "47"),
    ("男生呢", PEOPLE_RESULT, "People", "People", "followup", "count", "organization_snapshot", "organization", True, False, "instant", "24"),
    ("女生呢", PEOPLE_RESULT, "People", "People", "followup", "count", "organization_snapshot", "organization", True, False, "instant", "11"),
    ("公司有多少男生", None, "", "People", "ask", "count", "organization_snapshot", "organization", True, False, "instant", "24"),
    ("公司有多少女生", None, "", "People", "ask", "count", "organization_snapshot", "organization", True, False, "instant", "11"),
    ("哪24个", MALE_RESULT, "People", "People", "followup", "list", "organization_snapshot", "organization", True, False, "instant", "名单"),
    ("人员名单", MALE_RESULT, "People", "People", "ask", "list", "organization_snapshot", "organization", True, False, "instant", "名单"),
    ("全部显示", MALE_RESULT, "People", "People", "followup", "list", "organization_snapshot", "organization", True, False, "instant", "侧边栏"),
    ("全部名单", PEOPLE_RESULT, "People", "People", "followup", "list", "organization_snapshot", "organization", True, False, "instant", "侧边栏"),
    ("展开", MALE_RESULT, "People", "People", "followup", "list", "organization_snapshot", "organization", True, False, "instant", "侧边栏"),
    ("补全", MALE_RESULT, "People", "People", "followup", "list", "organization_snapshot", "organization", True, False, "instant", "继续"),
    ("第3个", MALE_RESULT, "People", "People", "followup", "followup", "organization_snapshot", "organization", True, False, "instant", "详情"),
    ("王悦的手机号是多少", None, "", "People", "ask", "field_lookup", "people_lookup", "person", True, False, "instant", "王悦"),
    ("王悦电话", None, "", "People", "ask", "field_lookup", "people_lookup", "person", True, False, "instant", "王悦"),
    ("王悦的职位", None, "", "People", "ask", "field_lookup", "people_lookup", "person", True, False, "instant", "王悦"),
    ("王悦是男是女", None, "", "People", "ask", "field_lookup", "people_lookup", "person", True, False, "instant", "王悦"),
    ("那陈俊呢", PERSON_PHONE_RESULT, "People", "People", "followup", "followup", "people_lookup", "person", True, False, "instant", "陈俊"),
    ("他的电话是多少", PERSON_PHONE_RESULT, "People", "People", "followup", "field_lookup", "people_lookup", "person", True, False, "instant", "电话"),
    ("姓王的有多少位", PEOPLE_RESULT, "People", "People", "ask", "count", "organization_snapshot", "organization", True, False, "instant", "47"),
    ("有哪些工程师", None, "", "People", "ask", "list", "organization_snapshot", "organization", True, False, "instant", "工程师"),
    ("工程师有多少人", None, "", "People", "ask", "count", "organization_snapshot", "organization", True, False, "instant", "工程师"),
    ("研发部有哪些人", None, "", "People", "ask", "list", "organization_snapshot", "department", True, False, "instant", "研发"),
    ("财务部多少人", None, "", "People", "ask", "count", "organization_snapshot", "department", True, False, "instant", "财务"),
    ("有谁的号码", None, "", "People", "ask", "field_lookup", "people_lookup", "person", True, False, "instant", "确认对象"),
    ("公司是做什么的", None, "", "Knowledge", "ask", "company_profile", "general_query", "company", True, False, "instant", "公司"),
    ("公司主营业务是什么", None, "", "Knowledge", "ask", "company_profile", "general_query", "company", True, False, "instant", "主营业务"),
    ("公司介绍在哪里", None, "", "Knowledge", "ask", "company_profile", "general_query", "company", True, False, "instant", "公司介绍"),
    ("报销流程怎么做", None, "", "Knowledge", "ask", "knowledge_query", "general_query", "company", True, False, "instant", "知识"),
    ("制度文件在哪里", None, "", "Knowledge", "ask", "knowledge_query", "general_query", "company", True, False, "instant", "知识"),
    ("项目资料在哪里", None, "", "Knowledge", "ask", "knowledge_query", "general_query", "company", True, False, "instant", "资料"),
    ("这份文档总结一下", KNOWLEDGE_RESULT, "Knowledge", "Knowledge", "ask", "knowledge_query", "general_query", "company", True, False, "instant", "文档"),
    ("继续", KNOWLEDGE_RESULT, "Knowledge", "Knowledge", "followup", "followup", "general_query", "company", True, False, "instant", "继续"),
    ("第一个", KNOWLEDGE_RESULT, "Knowledge", "Knowledge", "followup", "followup", "general_query", "company", True, False, "instant", "第一个"),
    ("发给这些人", MALE_RESULT, "People", "Communication", "request_action", "action_request", "message_send", "organization", True, True, "instant", "消息内容"),
    ("用机器人发给这些人说今晚开会", MALE_RESULT, "People", "Communication", "request_action", "action_request", "message_send", "organization", True, True, "instant", "确认"),
    ("给这些人发邮件", MALE_RESULT, "People", "Communication", "request_action", "action_request", "message_send", "organization", True, True, "instant", "消息内容"),
    ("是的", None, "", "Conversation", "answer", "confirm", "smalltalk", "self", True, False, "llm_enhanced", "确认"),
    ("是的", None, "Communication", "Communication", "confirm", "confirm", "smalltalk", "self", True, False, "llm_enhanced", "确认"),
    ("不用了", MALE_RESULT, "People", "People", "cancel", "cancel", "smalltalk", "self", True, False, "llm_enhanced", "取消"),
    ("我是谁", None, "", "Conversation", "ask", "ask", "smalltalk", "self", True, False, "llm_enhanced", "你"),
    ("现在几点", None, "", "Conversation", "ask", "ask", "smalltalk", "self", True, False, "llm_enhanced", "时间"),
    ("你是谁", None, "", "Conversation", "ask", "ask", "smalltalk", "self", True, False, "llm_enhanced", "助手"),
]


@pytest.mark.parametrize(
    (
        "message",
        "result_context",
        "expected_state_domain",
        "expected_domain",
        "expected_speech_act",
        "expected_operation",
        "expected_intent",
        "expected_scope",
        "expected_policy_allowed",
        "expected_requires_confirmation",
        "expected_response_mode",
        "expected_reply_token",
    ),
    CASES,
)
def test_conversation_first_v1_regression_contract(
    message: str,
    result_context: ResultContext | None,
    expected_state_domain: str,
    expected_domain: str,
    expected_speech_act: str,
    expected_operation: str,
    expected_intent: str,
    expected_scope: str,
    expected_policy_allowed: bool,
    expected_requires_confirmation: bool,
    expected_response_mode: str,
    expected_reply_token: str,
) -> None:
    context = _context(message, result_context=result_context, session_context=PENDING_SEND if message == "是的" and expected_domain == "Communication" else None)

    state = build_conversation_state(context)
    hints = build_conversation_hints(message, state)
    semantic_frame = understand_semantics(message=message, state=state, hints=hints)
    command_frame = resolve_dialogue_to_command_frame(state=state, semantic_frame=semantic_frame, hints=hints)
    intent = conversation_first_intent_result(context=context, frame=command_frame)
    planner = plan_task(intent)
    policy = check_runtime_permission(context=context, intent=intent, plan=planner)
    response_policy = build_response_policy(
        intent=intent.intent,
        result_type=_result_type_for_intent(intent.intent),
        data_scope=intent.data_scope,
        answer=_sample_reply(command_frame, allowed=policy.allowed),
        question_type=intent.question_type,
        requires_confirmation=policy.requires_confirmation,
    )

    assert state.active_domain == expected_state_domain
    assert state.source_contract["result_context_consumed_by_builder"] is (result_context is not None)
    assert semantic_frame.speech_act == expected_speech_act
    assert semantic_frame.operation == expected_operation
    assert "capability" not in semantic_frame.payload()
    assert "provider" not in semantic_frame.payload()
    assert "permission" not in semantic_frame.payload()
    assert command_frame.domain == expected_domain
    assert command_frame.intent == expected_intent
    assert command_frame.scope == expected_scope
    assert command_frame.route_path == "conversation_first_v1"
    assert command_frame.params["output_contract"]["template_policy"] == "no_standard_template_for_text"
    assert command_frame.capability == ""
    assert "provider" not in command_frame.params
    assert "credential" not in command_frame.params
    assert "identity" not in command_frame.params
    assert policy.allowed is expected_policy_allowed
    assert policy.requires_confirmation is expected_requires_confirmation
    assert response_policy["response_mode"] == expected_response_mode
    assert expected_reply_token in _sample_reply(command_frame, allowed=policy.allowed)


def test_command_layer_uses_conversation_first_for_people_and_knowledge() -> None:
    for message, expected_intent in (
        ("公司有多少人", "organization_snapshot"),
        ("王悦的手机号是多少", "people_lookup"),
        ("公司是做什么的", "general_query"),
    ):
        plan = build_command_plan(context=_context(message))

        assert plan.intent == expected_intent
        assert plan.command_frame is not None
        assert plan.command_frame.route_path == "conversation_first_v1"


def test_response_orchestrator_answers_people_count_without_template_or_card() -> None:
    context = _context("公司有多少人，只回答人数")
    command_plan = build_command_plan(context=context)
    result_context = ResultContext(
        result_type="organization_snapshot",
        count=47,
        items=tuple({"name": f"同事{i}", "title": "工程师"} for i in range(3)),
        metadata={"entity_domain": "People", "result_context_presentation": "summary", "field_projection": "count_only"},
        answer="已读取组织架构：39 个部门，47 人。字段完整度：手机号 20/47。",
    )
    execution = ExecutionResult(
        strategy="organization_snapshot",
        status="success",
        provider_results=(ProviderResult(source="people", status="success", result_type="organization_snapshot", count=47, answer=result_context.answer),),
        result_context=result_context,
    )

    composed = compose_answer(
        context=context,
        intent=command_plan.intent_result,
        permission=PermissionDecision(allowed=True),
        execution=execution,
    )
    runtime_result = build_runtime_result(
        command_plan=command_plan,
        permission=PermissionDecision(allowed=True),
        execution=execution,
        composed=composed,
    )

    assert composed.answer == "47人。"
    assert runtime_result.target_ui == "none"
    assert runtime_result.actions == ()


def test_response_orchestrator_honors_numeric_only_count() -> None:
    context = _context("公司多少人，只回数字")
    command_plan = build_command_plan(context=context)
    result_context = ResultContext(
        result_type="organization_snapshot",
        count=47,
        items=tuple({"name": f"同事{i}", "title": "工程师"} for i in range(3)),
        metadata={"entity_domain": "People", "result_context_presentation": "summary"},
        answer="公司当前可读通讯录里是 47 人。",
    )
    execution = ExecutionResult(
        strategy="organization_snapshot",
        status="success",
        provider_results=(ProviderResult(source="people", status="success", result_type="organization_snapshot", count=47, answer=result_context.answer),),
        result_context=result_context,
    )

    composed = compose_answer(
        context=context,
        intent=command_plan.intent_result,
        permission=PermissionDecision(allowed=True),
        execution=execution,
    )
    runtime_result = build_runtime_result(
        command_plan=command_plan,
        permission=PermissionDecision(allowed=True),
        execution=execution,
        composed=composed,
    )

    assert command_plan.command_frame is not None
    assert command_plan.command_frame.params["output_contract"]["mode"] == "numeric_only"
    assert composed.answer == "47"
    assert runtime_result.target_ui == "none"
    assert runtime_result.actions == ()


def test_response_orchestrator_keeps_single_people_field_as_text_only() -> None:
    context = _context("王悦的手机号是多少")
    command_plan = build_command_plan(context=context)
    result_context = ResultContext(
        result_type="people_search",
        count=1,
        items=({"name": "王悦", "mobile": "+8618061834925", "title": "项目经理"},),
        metadata={"entity_domain": "People", "result_context_presentation": "summary", "field_projection": "mobile"},
        answer="人员明细：王悦，项目经理，手机号 +8618061834925。",
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
    runtime_result = build_runtime_result(
        command_plan=command_plan,
        permission=PermissionDecision(allowed=True),
        execution=execution,
        composed=composed,
    )

    assert composed.answer == "王悦的手机号是 +8618061834925。"
    assert runtime_result.target_ui == "none"
    assert runtime_result.actions == ()


def test_response_orchestrator_moves_people_list_to_sidepanel() -> None:
    context = _context("全部显示", result_context=MALE_RESULT)
    command_plan = build_command_plan(context=context)
    items = tuple({"name": f"同事{i}", "title": "工程师", "department": "工程部"} for i in range(24))
    result_context = ResultContext(
        result_type="organization_snapshot",
        count=24,
        items=items,
        metadata={
            "entity_domain": "People",
            "result_context_presentation": "summary",
            "field_projection": "name_only",
            "people_filter": {"filter": "gender", "value": "male"},
        },
        answer="\n".join(f"{i}. 同事{i}" for i in range(1, 25)),
    )
    execution = ExecutionResult(
        strategy="organization_snapshot",
        status="success",
        provider_results=(ProviderResult(source="people", status="success", result_type="organization_snapshot", count=24, answer=result_context.answer),),
        result_context=result_context,
    )

    composed = compose_answer(
        context=context,
        intent=command_plan.intent_result,
        permission=PermissionDecision(allowed=True),
        execution=execution,
    )
    runtime_result = build_runtime_result(
        command_plan=command_plan,
        permission=PermissionDecision(allowed=True),
        execution=execution,
        composed=composed,
    )

    assert "1. 同事1" not in composed.answer
    assert "侧边栏" in composed.answer
    assert runtime_result.target_ui == "card"
    assert [action["action"] for action in runtime_result.actions] == ["open_sidepanel"]


def test_response_orchestrator_opens_sidepanel_after_count_followup() -> None:
    context = _context("全部名单", result_context=PEOPLE_RESULT)
    command_plan = build_command_plan(context=context)
    items = tuple({"name": f"同事{i}", "title": "工程师", "department": "工程部"} for i in range(47))
    result_context = ResultContext(
        result_type="organization_snapshot",
        count=47,
        items=items,
        metadata={
            "entity_domain": "People",
            "result_context_presentation": "detail",
            "field_projection": "name_only",
        },
        answer="公司当前可读通讯录里是 47 人。",
    )
    execution = ExecutionResult(
        strategy="organization_snapshot",
        status="success",
        provider_results=(ProviderResult(source="people", status="success", result_type="organization_snapshot", count=47, answer=result_context.answer),),
        result_context=result_context,
    )

    composed = compose_answer(
        context=context,
        intent=command_plan.intent_result,
        permission=PermissionDecision(allowed=True),
        execution=execution,
    )
    runtime_result = build_runtime_result(
        command_plan=command_plan,
        permission=PermissionDecision(allowed=True),
        execution=execution,
        composed=composed,
    )

    assert command_plan.command_frame is not None
    assert command_plan.command_frame.params["output_contract"]["surface"] == "sidepanel"
    assert "需要看全部名单" not in composed.answer
    assert "侧边栏" in composed.answer
    assert runtime_result.target_ui == "card"
    assert [action["action"] for action in runtime_result.actions] == ["open_sidepanel"]


def _result_type_for_intent(intent: str) -> str:
    return {
        "organization_snapshot": "organization_snapshot",
        "people_lookup": "people_search",
        "general_query": "knowledge_search",
        "message_send": "runtime_pending_confirmation",
    }.get(intent, "smalltalk")


def _sample_reply(command_frame, *, allowed: bool) -> str:
    semantic = command_frame.params.get("semantic_frame") if isinstance(command_frame.params.get("semantic_frame"), dict) else {}
    raw = str(semantic.get("parameters", {}).get("raw_message") or command_frame.user_goal)
    if not allowed:
        return "需要先确认对象和消息内容。"
    if command_frame.intent == "organization_snapshot":
        if "继续" in raw or "补全" in raw:
            return "继续从上一轮结果往后补全。"
        if "工程师" in raw:
            return "工程师相关数量和名单可继续按部门缩小。"
        if "部" in raw and "全部" not in raw:
            return f"会按部门范围处理：{raw}。"
        if command_frame.params["output_contract"]["surface"] == "sidepanel":
            return "名单已整理到侧边栏。"
        if "男" in raw:
            return "目前能确认的男性员工是 24 位。"
        if "女" in raw:
            return "目前能确认的女性员工是 11 位。"
        if "研发" in raw or "财务" in raw:
            return f"我会按你说的范围统计：{raw}。"
        if "第" in raw:
            return "第一个/第3个详情应该从上一轮结果中打开。"
        return "47人。"
    if command_frame.intent == "people_lookup":
        if command_frame.missing_slots:
            return "需要你确认对象后再查。"
        if semantic.get("parameters", {}).get("field") == "mobile":
            return f"已对准 {semantic.get('parameters', {}).get('person_name') or raw} 的电话。"
        return f"已对准 {semantic.get('parameters', {}).get('person_name') or raw}。"
    if command_frame.intent == "general_query":
        if "主营业务" in raw:
            return "会从知识库组织主营业务答案。"
        if "公司介绍" in raw:
            return "会从知识库读取公司介绍。"
        if "资料" in raw:
            return "会按资料问题查知识库。"
        if "文档" in raw:
            return "会基于当前文档继续。"
        if "继续" in raw:
            return "继续沿用上一轮知识结果。"
        if "第一个" in raw:
            return "第一个知识结果进入详情。"
        return "会从知识库组织公司答案。"
    if command_frame.intent == "message_send":
        return "发送前需要确认目标和消息内容。"
    if "是的" in raw:
        return "确认状态已进入对话处理，不会抢路由。"
    if "不用" in raw:
        return "取消当前对话动作。"
    if "我是谁" in raw:
        return "你是当前会话用户。"
    if "几点" in raw:
        return "时间问题走对话回答。"
    if "你是谁" in raw:
        return "我是企业数字助手。"
    return "收到。"
