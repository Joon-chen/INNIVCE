from __future__ import annotations

from dataclasses import replace
import re
from typing import Any

from app.services.runtime_v5.command_frame import command_frame_payload
from app.services.runtime_v5.conversation_hints import ConversationHints
from app.services.runtime_v5.conversation_state import ConversationState, build_conversation_state
from app.services.runtime_v5.models import CommandFrame, IntentResult, RuntimeContext
from app.services.runtime_v5.semantic_frame import SemanticFrame


def resolve_dialogue_to_command_frame(
    *,
    state: ConversationState,
    semantic_frame: SemanticFrame,
    hints: ConversationHints,
) -> CommandFrame:
    """Resolve a semantic utterance into the sole Command Engine output."""

    domain = _domain(state=state, semantic_frame=semantic_frame, hints=hints)
    intent = _intent(domain=domain, semantic_frame=semantic_frame, hints=hints)
    output_contract = _output_contract(semantic_frame=semantic_frame, hints=hints)
    context_contract = _context_contract(state=state, semantic_frame=semantic_frame)
    scope = _scope(domain=domain, semantic_frame=semantic_frame)
    domain_query = _domain_query(
        domain=domain,
        intent=intent,
        scope=scope,
        semantic_frame=semantic_frame,
        output_contract=output_contract,
        context_contract=context_contract,
    )
    question_type = "action" if semantic_frame.speech_act == "request_action" else "query"
    return CommandFrame(
        utterance_type=_utterance_type(semantic_frame),
        dialogue_mode=_dialogue_mode(semantic_frame),
        user_goal=semantic_frame.parameters.get("raw_message", ""),
        intent=intent,
        question_type=question_type,
        domain=domain,
        context_mode=_context_mode(state=state, semantic_frame=semantic_frame),
        capability="",
        skill_intent="",
        scope=scope,
        action_type=_operation_kind(semantic_frame),
        safety_level="low" if question_type == "query" else "high",
        target=dict(semantic_frame.target),
        params={
            "conversation_first_v1": True,
            "semantic_frame": semantic_frame.payload(),
            "output_contract": output_contract,
            "context_contract": context_contract,
            "domain_query": domain_query,
        },
        missing_slots=_missing_slots(semantic_frame),
        gates={
            "conversation_state": "consumed",
            "semantic_understanding": "consumed",
            "dialogue_resolver": "resolved",
            "policy": "pending",
            "runtime": "pending",
            "domain": {
                "domain": domain,
                "reason": _domain_reason(domain=domain, intent=intent, semantic_frame=semantic_frame),
                "source": "foundation_rule",
                "confidence": semantic_frame.confidence,
            },
            "scope": {
                "scope": scope,
                "requested_scope": scope,
                "resolved_scope": scope,
                "reason": _scope_reason(domain=domain, scope=scope),
                "source": "conversation_first_v1",
                "resource_boundary": _resource_boundary(domain=domain, scope=scope),
                "target": {"type": scope},
            },
            "context": {"mode": _context_mode(state=state, semantic_frame=semantic_frame)},
            "route": {"foundation_route": _foundation_route(domain=domain, intent=intent, scope=scope)},
        },
        context_used={
            "conversation_state": True,
            "previous_result": bool(state.previous_result_reference.result_type),
            "pending_confirmation": bool(state.pending_confirmation.kind),
            "pending_clarification": bool(state.pending_clarification.kind),
        },
        response_intent={
            "mode": output_contract["mode"],
            "surface": output_contract["surface"],
            "natural_language": True,
            "tone": "natural" if intent == "smalltalk" else "concise",
            "should_render_card": output_contract["surface"] not in {"text"},
            "intro_intent": "",
        },
        confidence=semantic_frame.confidence,
        needs_clarification=bool(semantic_frame.ambiguities),
        route_reason="dialogue_resolver",
        route_path="conversation_first_v1",
        rule_candidate={"hints": _hints_payload(hints)},
    )


def conversation_first_intent_result(
    *,
    context: RuntimeContext,
    frame: CommandFrame,
) -> IntentResult:
    """Adapt the V1 CommandFrame to the existing Runtime V5 planner."""

    entities = _entities_from_frame(frame)
    missing_params = frame.missing_slots
    if frame.intent == "message_send":
        action_entities, action_missing = _communication_action_entities(context)
        entities.update(action_entities)
        merged_missing = [*missing_params, *action_missing]
        if action_entities.get("target") or action_entities.get("target_type"):
            merged_missing = [key for key in merged_missing if key not in {"target", "target_type"}]
        if action_entities.get("text"):
            merged_missing = [key for key in merged_missing if key != "text"]
        missing_params = tuple(dict.fromkeys(merged_missing))
    entities["command_frame"] = command_frame_payload(frame)
    return IntentResult(
        question_type=frame.question_type,  # type: ignore[arg-type]
        intent=frame.intent,
        data_scope=frame.scope,  # type: ignore[arg-type]
        entities=entities,
        missing_params=missing_params,
        confidence=frame.confidence,
        canonical_question=_canonical_question_from_frame(frame=frame, context=context, entities=entities),
    )


def build_conversation_first_frame(context: RuntimeContext) -> CommandFrame:
    from app.services.runtime_v5.conversation_hints import build_conversation_hints
    from app.services.runtime_v5.conversation_state import build_conversation_state
    from app.services.runtime_v5.semantic_frame import understand_semantics

    state = build_conversation_state(context)
    hints = build_conversation_hints(context.current_message, state)
    semantic_frame = understand_semantics(message=context.current_message, state=state, hints=hints)
    return resolve_dialogue_to_command_frame(state=state, semantic_frame=semantic_frame, hints=hints)


def _domain(*, state: ConversationState, semantic_frame: SemanticFrame, hints: ConversationHints) -> str:
    if hints.domain_hint:
        return hints.domain_hint
    if semantic_frame.topic == "people":
        return "People"
    if semantic_frame.topic == "knowledge":
        return "Knowledge"
    if state.active_domain:
        return state.active_domain
    return "Conversation"


def _intent(*, domain: str, semantic_frame: SemanticFrame, hints: ConversationHints) -> str:
    if semantic_frame.speech_act in {"cancel", "answer"}:
        return "smalltalk"
    if semantic_frame.speech_act == "request_action":
        return "message_send" if domain == "Communication" else "smalltalk"
    if domain == "People":
        parameters = semantic_frame.parameters
        previous_result = parameters.get("previous_result") if isinstance(parameters.get("previous_result"), dict) else {}
        if semantic_frame.operation == "field_lookup" and _semantic_scope(semantic_frame) == "organization" and not parameters.get("person_name"):
            return "organization_snapshot"
        if (
            previous_result.get("collection_type") == "department_people"
            and semantic_frame.operation in {"list", "followup", "exists"}
            and not parameters.get("field")
        ):
            return "department_members"
        if parameters.get("person_name"):
            return "people_lookup"
        if parameters.get("current_object") and parameters.get("field"):
            return "people_lookup"
        if (
            parameters.get("organization_unit")
            or hints.scope_hint == "department"
            or previous_result.get("collection_type") == "department_people"
        ):
            return "department_members"
        if semantic_frame.operation in {"count", "list", "followup"}:
            return "organization_snapshot"
        if semantic_frame.operation == "field_lookup":
            return "people_lookup"
        return "organization_snapshot"
    if domain == "Knowledge":
        return "general_query"
    return "smalltalk"


def _communication_action_entities(context: RuntimeContext) -> tuple[dict[str, Any], tuple[str, ...]]:
    # Conversation First owns target/text extraction. The legacy parser is only
    # a compatibility fallback for forms not covered by the semantic contract.
    from app.services.runtime_v5.intent import _recognize_intent_by_rules

    candidate = _recognize_intent_by_rules(context.current_message, context)
    entities = dict(candidate.entities) if candidate.intent == "message_send" else {}
    extracted = _conversation_message_send_entities(context)
    entities = {**entities, **{key: value for key, value in extracted.items() if value not in (None, "")}}
    missing = []
    if not entities.get("target_type") and not entities.get("target"):
        missing.append("target_type")
    if not entities.get("text"):
        missing.append("text")
    if entities.get("target_type") == "people_context" and not entities.get("delivery_mode"):
        missing.append("delivery_mode")
    return entities, tuple(missing)


def _conversation_message_send_entities(context: RuntimeContext) -> dict[str, Any]:
    message = str(context.current_message or "").strip()
    compact = message.replace(" ", "")
    entities: dict[str, Any] = {}
    text = _message_send_text(message)
    if text:
        entities["text"] = text
    state = build_conversation_state(context)
    active_person = state.active_object if state.active_object.get("type") == "person" else {}
    if any(token in compact for token in ("给他", "发给他", "通知他", "给她", "发给她", "通知她")):
        name = str(active_person.get("name") or "").strip()
        if name:
            entities["target_type"] = "person"
            entities["target"] = name
    elif any(token in compact for token in ("这些人", "这些同事", "上面这些人")) and state.previous_result_reference.has_items:
        entities["target_type"] = "people_context"
        entities["target"] = "previous_result"
        delivery_mode = _message_delivery_mode(compact)
        if delivery_mode:
            entities["delivery_mode"] = delivery_mode
    elif any(token in compact for token in ("当前会话", "这个群", "本群", "这里")):
        entities["target_type"] = "current_chat"
        entities["target"] = "current_chat"
    else:
        chat_target = _message_chat_target(compact)
        if chat_target:
            entities["target_type"] = "chat"
            entities["target"] = chat_target
        else:
            person_target = _message_person_target(compact)
            if person_target:
                entities["target_type"] = "person"
                entities["target"] = person_target
    return entities


def _message_send_text(message: str) -> str:
    text = str(message or "").strip()
    for separator in ("说：", "说:", "：", ":"):
        if separator in text:
            return text.rsplit(separator, 1)[1].strip()
    return ""


def _message_chat_target(compact: str) -> str:
    match = re.search(r"(?:发给|发到|发送到|拉群后发到|发条信息到)(?P<target>[\u4e00-\u9fffA-Za-z0-9_-]{2,30}?)群", compact)
    if not match:
        return ""
    return match.group("target").strip()


def _message_person_target(compact: str) -> str:
    patterns = (
        r"(?:给|发给|通知)(?P<target>[\u4e00-\u9fff]{2,4}?)(?:发消息|发信息|说|:|：)",
        r"(?:给|发给|通知)(?P<target>[\u4e00-\u9fff]{2,4}?)(?:$|发|说|:|：)",
    )
    for pattern in patterns:
        match = re.search(pattern, compact)
        if match:
            target = match.group("target")
            if target not in {"这些人", "这个群", "当前会话"}:
                return target
    return ""


def _message_delivery_mode(compact: str) -> str:
    if any(token in compact for token in ("机器人通知", "用机器人", "机器人发", "系统通知", "自动通知", "大飞哥通知")):
        return "bot_multi_notify"
    if any(token in compact for token in ("替我发", "用我", "以我的名义", "我发给", "分别发", "单独发", "单独发送", "私聊发")):
        return "user_multi_private"
    if any(token in compact for token in ("拉群", "建群", "建个群", "创建群", "群里发", "发到群")):
        return "create_group_then_send"
    return ""


def _output_contract(*, semantic_frame: SemanticFrame, hints: ConversationHints) -> dict[str, str]:
    mode = semantic_frame.requested_output or "natural_text"
    if mode in {"count", "numeric_only"}:
        surface = "text"
    elif mode in {"name_only", "full_list", "detail"}:
        surface = "text"
    elif mode == "sidepanel":
        surface = "sidepanel"
    else:
        surface = "text"
    return {
        "mode": mode,
        "surface": surface,
        "list_delivery": "sidepanel" if surface == "sidepanel" or mode in {"full_list", "detail"} else "summary_text",
        "template_policy": "no_standard_template_for_text",
    }


def _context_contract(*, state: ConversationState, semantic_frame: SemanticFrame) -> dict[str, Any]:
    return {
        "active_domain": state.active_domain,
        "active_topic": state.active_topic,
        "previous_result_type": state.previous_result_reference.result_type,
        "previous_collection": state.previous_result_reference.collection_type,
        "previous_target_label": state.previous_result_reference.target_label,
        "previous_count": state.previous_result_reference.count,
        "pending_confirmation": bool(state.pending_confirmation.kind),
        "pending_clarification": bool(state.pending_clarification.kind),
        "speech_act": semantic_frame.speech_act,
    }


def _scope(*, domain: str, semantic_frame: SemanticFrame) -> str:
    if domain == "Knowledge":
        return "company"
    if domain == "Communication":
        previous_result = semantic_frame.parameters.get("previous_result") if isinstance(semantic_frame.parameters.get("previous_result"), dict) else {}
        if previous_result.get("result_type") == "organization_snapshot":
            return "organization"
        return "self"
    previous_result = semantic_frame.parameters.get("previous_result") if isinstance(semantic_frame.parameters.get("previous_result"), dict) else {}
    if domain == "People" and previous_result.get("collection_type") == "department_people" and semantic_frame.operation in {"list", "followup", "exists"}:
        return "department"
    if semantic_frame.parameters.get("person_name"):
        return "person"
    scope = str(semantic_frame.parameters.get("scope_hint") or "")
    if scope in {"organization", "company_people", "filtered_people"}:
        return "organization"
    if scope in {"department", "department_people"}:
        return "department"
    if scope == "person" or semantic_frame.operation == "field_lookup":
        return "person"
    if domain == "People" and semantic_frame.operation in {"list", "count", "followup"}:
        return "organization"
    return "self"


def _semantic_scope(semantic_frame: SemanticFrame) -> str:
    scope = str(semantic_frame.parameters.get("scope_hint") or semantic_frame.target.get("scope") or "").strip()
    if scope:
        return scope
    kind = str(semantic_frame.target.get("kind") or "").strip()
    if kind == "organization_unit":
        return "department"
    if kind == "person":
        return "person"
    return ""


def _operation_kind(semantic_frame: SemanticFrame) -> str:
    if semantic_frame.speech_act == "request_action":
        return "send"
    return "read"


def _context_mode(*, state: ConversationState, semantic_frame: SemanticFrame) -> str:
    if state.previous_result_reference.result_type and semantic_frame.parameters.get("previous_result"):
        return "inherit_result_context"
    if semantic_frame.speech_act in {"followup", "answer", "confirm"}:
        return "inherit_conversation_state"
    return "new_question"


def _utterance_type(semantic_frame: SemanticFrame) -> str:
    if semantic_frame.speech_act == "request_action":
        return "action_request"
    if semantic_frame.topic in {"people", "knowledge"}:
        return "business_query"
    return "conversation"


def _dialogue_mode(semantic_frame: SemanticFrame) -> str:
    if semantic_frame.ambiguities:
        return "clarify"
    if semantic_frame.speech_act == "request_action":
        return "execute"
    if semantic_frame.speech_act in {"cancel", "answer"}:
        return "answer"
    return "present"


def _missing_slots(semantic_frame: SemanticFrame) -> tuple[str, ...]:
    missing: list[str] = []
    if "missing_person_target" in semantic_frame.ambiguities:
        missing.append("person")
    if "missing_action_target" in semantic_frame.ambiguities:
        missing.append("target")
    return tuple(missing)


def _entities_from_frame(frame: CommandFrame) -> dict[str, Any]:
    semantic = frame.params.get("semantic_frame") if isinstance(frame.params.get("semantic_frame"), dict) else {}
    parameters = semantic.get("parameters") if isinstance(semantic.get("parameters"), dict) else {}
    output_contract = frame.params.get("output_contract") if isinstance(frame.params.get("output_contract"), dict) else {}
    domain_query = frame.params.get("domain_query") if isinstance(frame.params.get("domain_query"), dict) else {}
    entities: dict[str, Any] = {
        "conversation_first_v1": True,
        "query": frame.user_goal,
        "output_preferences": output_contract,
        "domain_query": domain_query,
    }
    if parameters.get("field"):
        entities["people_query_field"] = parameters["field"]
    elif parameters.get("inherited_field_projection"):
        entities["people_query_field"] = parameters["inherited_field_projection"]
    person_name = parameters.get("person_name") or (
        parameters.get("current_object", {}).get("name") if isinstance(parameters.get("current_object"), dict) else ""
    )
    if frame.intent == "department_members":
        target = semantic.get("target") if isinstance(semantic.get("target"), dict) else {}
        previous_result = parameters.get("previous_result") if isinstance(parameters.get("previous_result"), dict) else {}
        organization_unit = parameters.get("organization_unit") or previous_result.get("target_label") or target.get("value") or ""
        if organization_unit:
            entities["keyword"] = organization_unit
    elif person_name:
        entities["keyword"] = person_name
    if isinstance(parameters.get("filters"), dict):
        entities["people_filter"] = parameters["filters"]
    if frame.intent == "general_query":
        entities["knowledge_context"] = "company_profile" if semantic.get("operation") == "company_profile" else "general"
        entities["foundation_route"] = "knowledge.general"
    if frame.intent in {"department_members", "organization_snapshot"}:
        entities["view"] = "people_aggregate" if frame.intent == "organization_snapshot" else "department_members"
        entities["foundation_route"] = "people.aggregate" if frame.intent == "organization_snapshot" else "people.department_members"
        mode = str(output_contract.get("mode") or "")
        raw_message = str(frame.user_goal or "")
        field = str(parameters.get("field") or "")
        if _filters_include_gender(parameters):
            entities["people_query_mode"] = "gender_list" if mode in {"name_only", "full_list", "detail", "sidepanel"} else "gender_count"
        elif mode in {"name_only", "full_list", "detail", "sidepanel"} or _looks_like_title_field(field):
            entities["people_query_mode"] = "title_list" if _looks_like_title_query(raw_message) else "list"
        elif mode in {"numeric_only", "count", "short_answer"}:
            entities["people_query_mode"] = "title_count" if _looks_like_title_query(raw_message) else "count_only"
    if frame.intent == "people_lookup":
        entities["foundation_route"] = "people.person"
    return entities


def _filters_include_gender(parameters: dict[str, Any]) -> bool:
    filters = parameters.get("filters") if isinstance(parameters.get("filters"), dict) else {}
    return bool(filters.get("gender") or filters.get("filter") == "gender")


def _canonical_question_from_frame(*, frame: CommandFrame, context: RuntimeContext, entities: dict[str, Any]) -> str:
    if frame.intent != "people_lookup":
        return frame.user_goal or context.current_message
    keyword = str(entities.get("keyword") or "").strip()
    field = str(entities.get("people_query_field") or "").strip()
    if keyword and field:
        field_text = {
            "mobile": "手机号",
            "email": "邮箱",
            "title": "岗位",
            "gender": "性别",
            "profile": "信息",
        }.get(field, "信息")
        return f"{keyword}的{field_text}"
    return frame.user_goal or context.current_message


def _looks_like_title_query(message: str) -> bool:
    return any(token in str(message or "") for token in ("董事长", "负责人", "岗位", "职位", "工程师", "经理", "主管", "总监", "销售", "财务", "测试", "运营", "人事", "研发"))


def _looks_like_title_field(value: str) -> bool:
    return _looks_like_title_query(value)


def _domain_query(
    *,
    domain: str,
    intent: str,
    scope: str,
    semantic_frame: SemanticFrame,
    output_contract: dict[str, str],
    context_contract: dict[str, Any],
) -> dict[str, Any]:
    operation_kind = "send" if semantic_frame.speech_act == "request_action" else "read"
    fields: list[str] = []
    field = semantic_frame.parameters.get("field")
    if isinstance(field, str) and field:
        fields.append(field)
    if domain == "People" and intent == "organization_snapshot":
        subject: Any = {"type": "organization"}
    else:
        subject = (
            {
                "type": "group",
                "department": semantic_frame.parameters.get("organization_unit")
                or semantic_frame.parameters.get("previous_result_target")
                or semantic_frame.target.get("value")
                or "",
            }
            if domain == "People" and intent == "department_members"
            else semantic_frame.parameters.get("person_name")
            or (
                semantic_frame.parameters.get("current_object", {}).get("name")
                if isinstance(semantic_frame.parameters.get("current_object"), dict)
                else ""
            )
            or semantic_frame.target.get("value")
            or ""
        )
    filters = semantic_frame.parameters.get("filters") if isinstance(semantic_frame.parameters.get("filters"), dict) else {}
    if domain == "People" and intent == "organization_snapshot" and _looks_like_title_field(str(field or "")):
        filters = {**filters, "query_mode": "title_list"}
    return {
        "domain": domain.lower(),
        "operation_kind": operation_kind,
        "subject": subject,
        "filters": filters,
        "fields": fields,
        "scope": scope,
        "context_ref": context_contract,
        "output_mode": "answer" if fields else output_contract["mode"],
        "presentation_hint": "" if operation_kind == "send" else ("sidepanel" if output_contract["surface"] == "sidepanel" else "text"),
        "risk_hint": "high" if operation_kind == "send" else "low",
        "evidence_requirement": "source_field" if fields else "",
    }


def _resource_boundary(*, domain: str, scope: str) -> str:
    if domain == "People":
        return "enterprise_directory"
    if domain == "Knowledge":
        return "enterprise_knowledge"
    if domain == "Communication":
        return "communication_action"
    if scope == "external":
        return "public_information"
    return "conversation"


def _domain_reason(*, domain: str, intent: str, semantic_frame: SemanticFrame) -> str:
    if domain == "People" and intent == "people_lookup":
        return "foundation_route:people.person"
    if domain == "People" and intent == "department_members":
        return "foundation_route:people.department_members"
    if domain == "People":
        return "foundation_route:people.organization_snapshot"
    if domain == "Knowledge" and semantic_frame.operation == "company_profile":
        return "foundation_route:knowledge.company_profile"
    if domain == "Knowledge":
        return "foundation_route:knowledge.general"
    return f"conversation_first:{semantic_frame.topic}"


def _scope_reason(*, domain: str, scope: str) -> str:
    if domain == "People" and scope == "person":
        return "person_resource_signal"
    if domain == "People" and scope == "department":
        return "department_resource_signal"
    if domain == "People" and scope == "organization":
        return "organization_resource_signal"
    if domain == "Knowledge" and scope == "company":
        return "company_knowledge_signal"
    return f"conversation_first:{scope}"


def _foundation_route(*, domain: str, intent: str, scope: str) -> str:
    if domain == "People" and intent == "people_lookup":
        return "people.person"
    if domain == "People" and scope == "department":
        return "people.department_members"
    if domain == "People":
        return "people.organization"
    if domain == "Knowledge":
        return "knowledge.general"
    return ""


def _hints_payload(hints: ConversationHints) -> dict[str, Any]:
    return {
        "domain_hint": hints.domain_hint,
        "operation_hint": hints.operation_hint,
        "requested_output_hint": hints.requested_output_hint,
        "target_hint": hints.target_hint,
        "scope_hint": hints.scope_hint,
        "is_confirmation_word": hints.is_confirmation_word,
        "is_cancel_word": hints.is_cancel_word,
        "is_followup_reference": hints.is_followup_reference,
        "is_action_request": hints.is_action_request,
        "keywords": list(hints.keywords),
    }


def intent_with_conversation_first_frame(context: RuntimeContext, intent: IntentResult) -> IntentResult:
    """Attach a Conversation First frame to a legacy intent without changing it."""

    frame = build_conversation_first_frame(context)
    entities = dict(intent.entities)
    entities["conversation_first_v1_frame"] = command_frame_payload(frame)
    return replace(intent, entities=entities)
