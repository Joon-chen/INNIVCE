from __future__ import annotations

from dataclasses import replace
import re
from typing import Any

from app.services.runtime_v5.company_profile_query import looks_like_company_profile_query
from app.services.runtime_v5.command_frame import command_frame_payload
from app.services.runtime_v5.conversation_state import ConversationState, build_conversation_state
from app.services.runtime_v5.models import CommandFrame, IntentResult, RuntimeContext
from app.services.runtime_v5.people_resolver import people_targets_from_result_context
from app.services.runtime_v5.semantic_fields import people_field_label, people_fields_from_text
from app.services.runtime_v5.semantic_understanding import DeterministicSignals, build_deterministic_signals, understand_semantics
from app.services.semantic_protocol import SemanticFrame


def resolve_dialogue_to_command_frame(
    *,
    state: ConversationState,
    semantic_frame: SemanticFrame,
    hints: DeterministicSignals,
) -> CommandFrame:
    """Resolve a semantic utterance into the sole Command Engine output."""

    domain = _domain(state=state, semantic_frame=semantic_frame, hints=hints)
    intent = _intent(domain=domain, state=state, semantic_frame=semantic_frame, hints=hints)
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
    question_type = _question_type(intent=intent, semantic_frame=semantic_frame)
    action_type = _operation_kind(semantic_frame, intent=intent)
    safety_level = _safety_level(intent=intent, question_type=question_type, action_type=action_type)
    gates = _gates(
        domain=domain,
        intent=intent,
        scope=scope,
        state=state,
        semantic_frame=semantic_frame,
        output_contract=output_contract,
        safety_level=safety_level,
    )
    return CommandFrame(
        utterance_type=_utterance_type(semantic_frame),
        dialogue_mode=_dialogue_mode(semantic_frame),
        user_goal=semantic_frame.parameters.get("raw_message", ""),
        intent=intent,
        question_type=question_type,
        domain=domain,
        context_mode=_context_mode(state=state, semantic_frame=semantic_frame),
        capability="",
        skill_intent=intent,
        scope=scope,
        action_type=action_type,
        safety_level=safety_level,
        target=dict(semantic_frame.target),
        params={
            "conversation_first_v1": True,
            "semantic_frame": semantic_frame.payload(),
            "output_contract": output_contract,
            "context_contract": context_contract,
            "domain_query": domain_query,
        },
        missing_slots=_missing_slots(semantic_frame),
        gates=gates,
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
            "intro_intent": "natural_reply" if intent == "smalltalk" else "",
        },
        confidence=semantic_frame.confidence,
        needs_clarification=bool(semantic_frame.ambiguities),
        route_reason="dialogue_resolver",
        route_path="conversation_first_v1",
        rule_candidate={
            "hints": _hints_payload(hints),
            "route_observation": _route_observation(intent=intent, question_type=question_type, scope=scope, domain=domain),
        },
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
        entities.setdefault("command_intent_trace", {"reason": "communication_send_action", "source": "conversation_first_v1"})
        merged_missing = [*missing_params, *action_missing]
        if action_entities.get("target") or action_entities.get("target_type"):
            merged_missing = [key for key in merged_missing if key not in {"target", "target_type"}]
        if action_entities.get("text"):
            merged_missing = [key for key in merged_missing if key != "text"]
        missing_params = tuple(dict.fromkeys(merged_missing))
    elif frame.intent in {"mail_draft_create", "calendar_create", "task_create"}:
        entities.update(_structured_action_entities(context=context, frame=frame))
    if frame.intent == "external_information_query":
        previous_external_query = _previous_external_query(context)
        if previous_external_query:
            entities["external_query"] = f"{previous_external_query} {context.current_message}".strip()
    if frame.intent == "smalltalk" and "person" in missing_params:
        entities.setdefault("fallback_answer", "你说的是哪一位？上一轮结果里有多个人，我不能直接猜。")
        entities.setdefault(
            "command_intent_trace",
            {"reason": "people_pronoun_with_multiple_or_empty_results", "source": "conversation_first_v1"},
        )
    if frame.intent == "smalltalk":
        raw_message = str(context.current_message or "")
        if "转一下" in raw_message:
            entities.setdefault("fallback_answer", "你想让我转发的话，需要先说明发给谁、转什么内容。")
        elif any(token in raw_message for token in ("外卖", "点餐", "奶茶", "咖啡")):
            entities.setdefault("fallback_answer", "这是生活需求，不会继承上一轮业务任务上下文。")
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
    state = build_conversation_state(context)
    signals = build_deterministic_signals(context.current_message, state)
    semantic_frame = understand_semantics(message=context.current_message, state=state, signals=signals)
    return resolve_dialogue_to_command_frame(state=state, semantic_frame=semantic_frame, hints=signals)


def _domain(*, state: ConversationState, semantic_frame: SemanticFrame, hints: DeterministicSignals) -> str:
    if _is_pending_message_send_target_answer(state=state, semantic_frame=semantic_frame):
        return "Communication"
    semantic_domain = _domain_from_semantic_frame(semantic_frame)
    if semantic_domain:
        return semantic_domain
    if hints.domain_hint:
        if hints.domain_hint in {"Mail", "Communication"}:
            return "Communication"
        if hints.domain_hint in {"Task", "Calendar"}:
            return "Workspace"
        return hints.domain_hint
    if state.active_domain:
        return state.active_domain
    return "Conversation"


def _domain_from_semantic_frame(semantic_frame: SemanticFrame) -> str:
    if semantic_frame.topic == "people":
        return "People"
    if semantic_frame.topic == "knowledge":
        return "Knowledge"
    if semantic_frame.topic == "mail":
        return "Communication"
    if semantic_frame.topic == "task":
        return "Workspace"
    if semantic_frame.topic == "calendar":
        return "Workspace"
    if semantic_frame.topic == "approval":
        return "Process"
    if semantic_frame.topic == "external":
        return "External"
    if semantic_frame.topic == "system":
        return "System"
    if semantic_frame.topic == "conversation":
        return "Conversation"
    return ""


def _intent(*, domain: str, state: ConversationState, semantic_frame: SemanticFrame, hints: DeterministicSignals) -> str:
    if semantic_frame.speech_act in {"cancel", "answer", "confirm"}:
        return "smalltalk"
    if _looks_like_organization_export(semantic_frame):
        return "organization_export"
    if semantic_frame.speech_act == "request_action" and not _is_read_only_result_presentation_request(semantic_frame):
        if domain == "Communication":
            if semantic_frame.topic == "mail":
                return "mail_draft_create"
            return "message_send"
        if domain == "Workspace":
            return "calendar_create" if semantic_frame.topic == "calendar" else "task_create"
        if domain == "Process":
            return "approval_query"
        if domain == "Mail":
            return "mail_draft_create"
        return "smalltalk"
    if _is_pending_message_send_target_answer(state=state, semantic_frame=semantic_frame):
        return "message_send"
    if domain == "People":
        parameters = semantic_frame.parameters
        if "missing_person_target" in semantic_frame.ambiguities:
            return "smalltalk"
        previous_result = parameters.get("previous_result") if isinstance(parameters.get("previous_result"), dict) else {}
        if semantic_frame.operation == "field_lookup" and _semantic_scope(semantic_frame) == "organization" and not parameters.get("person_name"):
            return "organization_snapshot"
        if (
            previous_result.get("collection_type") == "department_people"
            and parameters.get("organization_relation") == "leader"
        ):
            return "department_members"
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
    if domain == "Communication" and semantic_frame.topic == "mail":
        return "mail_query"
    if domain == "Communication":
        return "chat_search"
    if domain == "Workspace":
        return "calendar_query" if semantic_frame.topic == "calendar" else "task_query"
    if domain == "Process":
        if _looks_like_approval_detail(semantic_frame=semantic_frame, state=state):
            return "approval_detail"
        return "approval_query"
    if domain == "External":
        return "external_information_query"
    return "smalltalk"


def _question_type(*, intent: str, semantic_frame: SemanticFrame) -> str:
    if intent in {"message_send", "mail_draft_create", "task_create", "calendar_create", "organization_export"}:
        return "action"
    if semantic_frame.speech_act == "request_action" and not _is_read_only_result_presentation_request(semantic_frame):
        return "action"
    return "query"


def _is_read_only_result_presentation_request(semantic_frame: SemanticFrame) -> bool:
    if semantic_frame.topic != "people":
        return False
    if semantic_frame.operation not in {"list", "followup", "count", "exists", "action_request"}:
        return False
    if semantic_frame.requested_output not in {"name_only", "full_list", "detail", "sidepanel", "natural_text"}:
        return False
    target_kind = str(semantic_frame.target.get("kind") or "")
    target_reference = str(semantic_frame.target.get("reference") or "")
    has_previous_result = isinstance(semantic_frame.parameters.get("previous_result"), dict)
    return target_kind == "previous_result" or target_reference == "previous_result" or has_previous_result


def _is_pending_message_send_target_answer(*, state: ConversationState, semantic_frame: SemanticFrame) -> bool:
    pending_intent = state.pending_confirmation.intent or state.pending_clarification.intent
    if pending_intent != "message_send":
        return False
    raw_message = str(semantic_frame.parameters.get("raw_message") or "")
    compact = re.sub(r"\s+", "", raw_message)
    return _references_previous_collection(compact=compact, state=state)


def _communication_action_entities(context: RuntimeContext) -> tuple[dict[str, Any], tuple[str, ...]]:
    extracted = _conversation_message_send_entities(context)
    entities = _merge_action_entities(extracted=extracted, previous=_pending_action_entities(context.session_context))
    missing = []
    if not entities.get("target_type") and not entities.get("target"):
        missing.append("target_type")
    if not entities.get("text"):
        missing.append("text")
    if entities.get("target_type") == "people_context" and not entities.get("delivery_mode"):
        missing.append("delivery_mode")
    return entities, tuple(missing)


def _merge_action_entities(*, extracted: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    entities = {key: value for key, value in extracted.items() if value not in (None, "", [], {})}
    has_explicit_target = _has_explicit_new_action_target(entities, previous)
    for key, value in previous.items():
        if value in (None, "", [], {}) or key in entities:
            continue
        if has_explicit_target and key in _ACTION_TARGET_KEYS:
            continue
        if key in {"target", "target_type"} and entities.get("target_type"):
            continue
        entities[key] = value
    return entities


def _has_explicit_new_action_target(entities: dict[str, Any], previous: dict[str, Any]) -> bool:
    target_type = str(entities.get("target_type") or "").strip()
    if not target_type:
        return False
    if _same_action_target(entities, previous):
        return False
    if target_type == "people_context":
        return bool(entities.get("organization_unit"))
    return target_type in {"person", "chat", "current_chat"}


def _same_action_target(entities: dict[str, Any], previous: dict[str, Any]) -> bool:
    target_type = str(entities.get("target_type") or "").strip()
    previous_type = str(previous.get("target_type") or "").strip()
    if not target_type or target_type != previous_type:
        return False
    current_target = str(entities.get("organization_unit") or entities.get("target_name") or entities.get("target") or "").strip()
    previous_target = str(previous.get("organization_unit") or previous.get("target_name") or previous.get("target") or "").strip()
    if target_type == "people_context":
        current_core = _collection_label_core(current_target)
        previous_core = _collection_label_core(previous_target)
        return bool(current_core and previous_core and current_core == previous_core)
    return bool(current_target and previous_target and current_target == previous_target)


_ACTION_TARGET_KEYS = frozenset(
    {
        "target",
        "target_type",
        "target_open_id",
        "target_name",
        "people_targets",
        "people_target_count",
        "organization_unit",
        "delivery_mode",
    }
)


def _conversation_message_send_entities(context: RuntimeContext) -> dict[str, Any]:
    message = str(context.current_message or "").strip()
    compact = message.replace(" ", "")
    entities: dict[str, Any] = {}
    text = _message_send_text(message)
    if text:
        entities["text"] = text
    state = build_conversation_state(context)
    active_person = state.active_object if state.active_object.get("type") == "person" else {}
    if _references_previous_collection(compact=compact, state=state):
        entities["target_type"] = "people_context"
        entities["target"] = _previous_collection_target_label(compact=compact, state=state) or "previous_result"
        people_targets = people_targets_from_result_context(context.result_context)
        if not people_targets:
            people_targets = _people_targets_from_pending_action(context.session_context)
        if people_targets:
            entities["people_targets"] = [dict(item) for item in people_targets]
            entities["people_target_count"] = str(len(people_targets))
        delivery_mode = _message_delivery_mode(compact)
        if delivery_mode:
            entities["delivery_mode"] = delivery_mode
        return entities
    organization_people_target = _message_organization_people_target(compact)
    if organization_people_target:
        entities["target_type"] = "people_context"
        entities["target"] = organization_people_target
        entities["organization_unit"] = organization_people_target
        if _organization_target_matches_previous_result(organization_people_target, state, context.result_context):
            people_targets = people_targets_from_result_context(context.result_context)
            if people_targets:
                entities["people_targets"] = [dict(item) for item in people_targets]
                entities["people_target_count"] = str(len(people_targets))
        delivery_mode = _message_delivery_mode(compact)
        if delivery_mode:
            entities["delivery_mode"] = delivery_mode
        return entities
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
        entities["execution_identity"] = "bot"
    else:
        chat_target = _message_chat_target(compact)
        if chat_target:
            entities["target_type"] = "chat"
            entities["target"] = chat_target
            entities["execution_identity"] = "bot"
        else:
            person_target = _message_person_target(compact)
            if person_target:
                entities["target_type"] = "person"
                entities["target"] = person_target
    return entities


def _pending_action_entities(session_context: dict[str, Any]) -> dict[str, Any]:
    for payload in _pending_action_entity_payloads(session_context):
        if payload:
            return payload
    return {}


def _people_targets_from_pending_action(session_context: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    for payload in _pending_action_entity_payloads(session_context):
        targets = payload.get("people_targets")
        if isinstance(targets, (list, tuple)):
            items = tuple(dict(item) for item in targets if isinstance(item, dict))
            if items:
                return items
    return ()


def _pending_action_entity_payloads(session_context: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    payloads: list[dict[str, Any]] = []
    for key in ("runtime_v5_pending_action", "runtime_v5_confirmed_action_entities"):
        payload = session_context.get(key)
        entities = payload.get("entities") if isinstance(payload, dict) and key == "runtime_v5_pending_action" else payload
        if isinstance(entities, dict):
            payloads.append(entities)
    state = session_context.get("runtime_v5_state")
    actions = state.get("actions") if isinstance(state, dict) and isinstance(state.get("actions"), list) else []
    for action in reversed(actions):
        if not isinstance(action, dict):
            continue
        metadata = action.get("metadata") if isinstance(action.get("metadata"), dict) else {}
        pending = metadata.get("pending_action") if isinstance(metadata.get("pending_action"), dict) else {}
        entities = pending.get("entities") if isinstance(pending.get("entities"), dict) else {}
        if entities:
            payloads.append(entities)
    return tuple(payloads)


def _references_previous_collection(*, compact: str, state: ConversationState) -> bool:
    if not state.previous_result_reference.has_items:
        return False
    if any(token in compact for token in ("他们", "她们", "这些人", "这些同事", "这几位", "这几个人", "上面这些人", "刚才这些人")):
        return True
    target_label = str(state.previous_result_reference.target_label or "").strip()
    if not target_label:
        return False
    label_core = _collection_label_core(target_label)
    if not label_core or label_core not in compact:
        return False
    count = state.previous_result_reference.count
    count_markers = {f"{count}人", f"{count}位", f"{count}个"} if count else set()
    collection_words = ("人", "同事", "成员", "人员", "名单", "这几位", "这几个人")
    return any(marker in compact for marker in count_markers) or any(word in compact for word in collection_words)


def _collection_label_core(value: str) -> str:
    label = str(value or "").strip()
    label = re.sub(r"(部门|事业部|中心|团队|小组|组|部)$", "", label)
    return label.strip()


def _previous_collection_target_label(*, compact: str, state: ConversationState) -> str:
    target_label = str(state.previous_result_reference.target_label or "").strip()
    label_core = _collection_label_core(target_label)
    return target_label if label_core and label_core in compact else ""


def _organization_target_matches_previous_result(target: str, state: ConversationState, result_context: Any) -> bool:
    target_core = _collection_label_core(target)
    if not target_core:
        return False
    candidates = [str(state.previous_result_reference.target_label or "")]
    metadata = getattr(result_context, "metadata", {}) if result_context is not None else {}
    if isinstance(metadata, dict):
        resolution = metadata.get("organization_resolution") if isinstance(metadata.get("organization_resolution"), dict) else {}
        candidates.extend(
            [
                str(metadata.get("keyword") or ""),
                str(metadata.get("resolved_department_name") or ""),
                str(resolution.get("query") or ""),
                str(resolution.get("resolved_name") or ""),
            ]
        )
    for candidate in candidates:
        previous_core = _collection_label_core(candidate)
        if previous_core and (target_core == previous_core or target_core in previous_core or previous_core in target_core):
            return True
    return False


def _message_send_text(message: str) -> str:
    text = str(message or "").strip()
    for separator in ("说：", "说:", "：", ":"):
        if separator in text:
            return text.rsplit(separator, 1)[1].strip()
    return ""


def _message_chat_target(compact: str) -> str:
    match = re.search(r"(?:发(?:条|个)?(?:消息|信息)?(?:给|到)|发送(?:给|到)|通知|拉群后发到)(?P<target>[\u4e00-\u9fffA-Za-z0-9_-]{2,30}?)群", compact)
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


def _message_organization_people_target(compact: str) -> str:
    match = re.search(
        r"(?:给|发给|通知)(?P<target>[\u4e00-\u9fffA-Za-z0-9]{1,30}(?:事业部|部门|中心|团队|小组|组|部))(?:的)?(?:人员|成员|同事|人)",
        compact,
    )
    if not match:
        return ""
    return match.group("target").strip()


def _message_delivery_mode(compact: str) -> str:
    if any(token in compact for token in ("机器人通知", "用机器人", "机器人发", "系统通知", "自动通知", "大飞哥通知")):
        return "bot_multi_notify"
    if any(token in compact for token in ("替我发", "用我", "以我的名义", "我发给", "分别发", "单独发", "单独发送", "私聊发")):
        return "user_multi_private"
    if any(token in compact for token in ("拉群", "建群", "建个群", "创建群", "群里发", "发到群")):
        return "create_group_then_send"
    return ""


def _output_contract(*, semantic_frame: SemanticFrame, hints: DeterministicSignals) -> dict[str, str]:
    mode = semantic_frame.requested_output or "natural_text"
    if mode in {"count", "numeric_only"}:
        surface = "text"
    elif mode in {"name_only", "full_list", "detail"}:
        surface = "text"
    elif mode == "sidepanel":
        surface = "sidepanel"
    else:
        surface = "text"
    list_delivery = "sidepanel" if surface == "sidepanel" or mode in {"full_list", "detail"} else "summary_text"
    if semantic_frame.parameters.get("presentation_preference") == "inline_text":
        list_delivery = "inline_text"
    return {
        "mode": mode,
        "surface": surface,
        "list_delivery": list_delivery,
        "template_policy": "no_standard_template_for_text",
    }


def _gates(
    *,
    domain: str,
    intent: str,
    scope: str,
    state: ConversationState,
    semantic_frame: SemanticFrame,
    output_contract: dict[str, str],
    safety_level: str,
) -> dict[str, Any]:
    context_mode = _context_mode(state=state, semantic_frame=semantic_frame)
    action_gate = _action_gate(intent=intent, state=state, semantic_frame=semantic_frame)
    risk_reasons = _risk_reasons(intent=intent, action_gate=action_gate)
    blocks_execution = action_gate.get("confirmation_hint") == "clarify_delivery_mode"
    return {
        "conversation_state": "consumed",
        "semantic_understanding": "consumed",
        "dialogue_resolver": "resolved",
        "policy": "pending",
        "runtime": "pending",
        "utterance": {"type": _utterance_type(semantic_frame)},
        "domain": {
            "domain": domain,
            "reason": _domain_reason(domain=domain, intent=intent, semantic_frame=semantic_frame),
            "source": _gate_source(domain=domain),
            "confidence": semantic_frame.confidence,
        },
        "scope": {
            "scope": scope,
            "requested_scope": scope,
            "resolved_scope": scope,
            "reason": _scope_reason(domain=domain, scope=scope, semantic_frame=semantic_frame),
            "source": "conversation_first_v1",
            "resource_boundary": _resource_boundary(domain=domain, scope=scope, semantic_frame=semantic_frame),
            "target": {"type": scope},
        },
        "context": {
            "mode": context_mode,
            "result_type": state.previous_result_reference.result_type,
            "previous_collection": state.previous_result_reference.collection_type,
        },
        "action": action_gate,
        "safety": {
            "level": safety_level,
            "confirmation_expected": intent in {"message_send", "mail_draft_create", "task_create", "calendar_create", "organization_export"},
            "blocks_execution": blocks_execution,
            "risk_reasons": risk_reasons,
        },
        "route": {
            "path": "conversation_first_v1",
            "foundation_route": _foundation_route(domain=domain, intent=intent, scope=scope, semantic_frame=semantic_frame),
        },
    }


def _action_gate(*, intent: str, state: ConversationState, semantic_frame: SemanticFrame) -> dict[str, Any]:
    if intent not in {"message_send", "mail_draft_create", "task_create", "calendar_create", "organization_export"}:
        operation_kind = _operation_kind(semantic_frame, intent=intent)
        return {"type": operation_kind, "operation_kind": operation_kind}
    target: dict[str, Any] = {}
    if state.previous_result_reference.has_items:
        target["people_target_count"] = str(state.previous_result_reference.count)
    if intent == "mail_draft_create":
        return {
            "operation_kind": "draft",
            "execution_mode": "user_draft",
            "target": target,
            "target_source": "people_result_context" if state.previous_result_reference.has_items else "",
            "confirmation_hint": "confirm_draft_creation",
            "credential_hint": "user_token_required",
        }
    delivery_mode = _delivery_mode_from_message(str(semantic_frame.parameters.get("raw_message") or ""))
    if intent == "message_send":
        confirmation_hint = "confirm_before_send" if delivery_mode else "clarify_delivery_mode"
        return {
            "operation_kind": "send",
            "execution_mode": delivery_mode or "unresolved_delivery_mode",
            "target": target,
            "target_source": "people_result_context" if state.previous_result_reference.has_items else "",
            "confirmation_hint": confirmation_hint,
            "credential_hint": "depends_on_delivery_mode",
        }
    return {
        "operation_kind": "write",
        "execution_mode": "user_action",
        "target": target,
        "target_source": "people_result_context" if state.previous_result_reference.has_items else "",
        "confirmation_hint": "confirm_before_execute",
        "credential_hint": "user_token_required",
    }


def _delivery_mode_from_message(message: str) -> str:
    compact = re.sub(r"\s+", "", str(message or ""))
    mode = _message_delivery_mode(compact)
    return {
        "bot_multi_notify": "bot_notify",
        "user_multi_private": "user_delegated_send",
        "create_group_then_send": "create_group_then_send",
    }.get(mode, mode)


def _risk_reasons(*, intent: str, action_gate: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if action_gate.get("target_source") == "people_result_context":
        reasons.append("uses_people_result_context")
    target = action_gate.get("target") if isinstance(action_gate.get("target"), dict) else {}
    try:
        if int(target.get("people_target_count") or 0) > 1:
            reasons.append("multiple_people_targets")
    except ValueError:
        pass
    if action_gate.get("confirmation_hint") == "clarify_delivery_mode":
        reasons.append("delivery_mode_unresolved")
    if intent == "mail_draft_create":
        reasons.append("draft_creates_external_artifact")
    return reasons


def _context_contract(*, state: ConversationState, semantic_frame: SemanticFrame) -> dict[str, Any]:
    current_person = ""
    current_requested_field = ""
    if state.previous_result_reference.object_type == "person":
        current_person = str(state.active_object.get("name") or "")
    if semantic_frame.parameters.get("person_name"):
        current_person = str(semantic_frame.parameters.get("person_name") or "")
    if semantic_frame.parameters.get("field") or semantic_frame.parameters.get("inherited_field_projection"):
        current_requested_field = str(semantic_frame.parameters.get("field") or semantic_frame.parameters.get("inherited_field_projection") or "")
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
        "current_person": current_person,
        "current_requested_field": current_requested_field,
    }


def _scope(*, domain: str, semantic_frame: SemanticFrame) -> str:
    if _looks_like_organization_export(semantic_frame):
        return "organization"
    if domain == "Conversation":
        return "self"
    if domain == "External":
        return "external"
    if domain == "Knowledge":
        return "company"
    if domain == "Process":
        return "self"
    if domain in {"Communication", "Workspace"}:
        scope_hint = str(semantic_frame.parameters.get("scope_hint") or "")
        if scope_hint in {"company", "department", "self"}:
            return scope_hint
        if domain == "Communication":
            previous_result = semantic_frame.parameters.get("previous_result") if isinstance(semantic_frame.parameters.get("previous_result"), dict) else {}
            if previous_result.get("result_type") == "organization_snapshot" and semantic_frame.speech_act == "request_action":
                return "organization"
        return "self"
    previous_result = semantic_frame.parameters.get("previous_result") if isinstance(semantic_frame.parameters.get("previous_result"), dict) else {}
    if domain == "People" and previous_result.get("collection_type") == "department_people" and semantic_frame.operation in {"list", "followup", "exists"}:
        return "department"
    if semantic_frame.parameters.get("person_name"):
        return "person"
    scope = str(semantic_frame.parameters.get("scope_hint") or "")
    if scope in {"organization", "company", "company_people", "filtered_people"}:
        return "organization"
    if scope in {"department", "department_people"}:
        return "department"
    if domain == "People" and (scope == "person" or semantic_frame.operation == "field_lookup"):
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


def _operation_kind(semantic_frame: SemanticFrame, *, intent: str) -> str:
    if intent == "mail_draft_create":
        return "draft"
    if semantic_frame.speech_act == "request_action" and intent in {"task_create", "calendar_create", "organization_export"}:
        return "write"
    if intent == "message_send":
        return "send"
    return "read"


def _context_mode(*, state: ConversationState, semantic_frame: SemanticFrame) -> str:
    if (
        semantic_frame.speech_act == "request_action"
        and state.previous_result_reference.has_items
        and not _is_read_only_result_presentation_request(semantic_frame)
    ):
        return "action_on_people_context"
    if semantic_frame.parameters.get("person_name") and semantic_frame.speech_act != "followup":
        return "new_question"
    if state.previous_result_reference.result_type and semantic_frame.parameters.get("previous_result"):
        return "inherit_result_context"
    if semantic_frame.speech_act in {"followup", "answer", "confirm"}:
        return "inherit_conversation_state"
    return "new_question"


def _utterance_type(semantic_frame: SemanticFrame) -> str:
    if semantic_frame.speech_act == "request_action" and not _is_read_only_result_presentation_request(semantic_frame):
        return "action_request"
    if semantic_frame.topic not in {"conversation"}:
        return "business_query"
    return "conversation"


def _dialogue_mode(semantic_frame: SemanticFrame) -> str:
    if semantic_frame.ambiguities:
        return "clarify"
    if semantic_frame.speech_act == "request_action" and not _is_read_only_result_presentation_request(semantic_frame):
        return "execute"
    if semantic_frame.topic == "conversation":
        return "answer"
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
        entities["foundation_route"] = "knowledge.company_profile" if semantic.get("operation") == "company_profile" else "knowledge.general"
    if frame.intent in {"department_members", "organization_snapshot"}:
        entities["view"] = "people_aggregate" if frame.intent == "organization_snapshot" else "department_members"
        entities["foundation_route"] = "people.aggregate" if frame.intent == "organization_snapshot" else "people.department_members"
        mode = str(output_contract.get("mode") or "")
        raw_message = str(frame.user_goal or "")
        field = str(parameters.get("field") or "")
        filters = domain_query.get("filters") if isinstance(domain_query.get("filters"), dict) else {}
        if filters.get("field_present"):
            entities["people_query_mode"] = "list"
        elif _filters_include_gender(parameters):
            entities["people_query_mode"] = "gender_list" if mode in {"name_only", "full_list", "detail", "sidepanel"} else "gender_count"
        elif mode in {"name_only", "full_list", "detail", "sidepanel"} or _looks_like_title_field(field):
            entities["people_query_mode"] = "title_list" if _looks_like_title_query(raw_message) else "list"
        elif mode in {"numeric_only", "count", "short_answer"}:
            entities["people_query_mode"] = "title_count" if _looks_like_title_query(raw_message) else "count_only"
        if parameters.get("organization_relation"):
            entities["organization_relation"] = parameters["organization_relation"]
    if frame.intent == "people_lookup":
        entities["foundation_route"] = "people.person"
        entities.setdefault(
            "command_intent_trace",
            {
                "reason": "exact_people_field_query",
                "source": "candidate_arbiter",
                "candidates": [{"domain": "People"}, {"domain": "Conversation"}],
            },
        )
    if frame.intent == "external_information_query":
        raw_message = str(frame.user_goal or "")
        entities["requires_realtime"] = True
        entities["boundary"] = "external_public_information"
        entities["external_category"] = "weather_realtime" if "天气" in raw_message else "local_realtime" if "附近" in raw_message else "public_realtime"
        entities["external_query"] = raw_message
    if frame.intent == "organization_export":
        raw_message = str(frame.user_goal or "")
        entities["target"] = "new_base"
        token_match = re.search(r"\b(?P<token>bascn-[A-Za-z0-9_-]+)\b", raw_message)
        if token_match:
            entities["app_token"] = token_match.group("token")
            entities.pop("target", None)
        entities["organization_export"] = True
        entities["foundation_route"] = "people.organization_export"
    return entities


def _looks_like_organization_export(semantic_frame: SemanticFrame) -> bool:
    raw = str(semantic_frame.parameters.get("raw_message") or "")
    compact = re.sub(r"\s+", "", raw)
    if not any(marker in compact for marker in ("组织架构", "组织结构")):
        return False
    return any(token in compact for token in ("创建", "新建", "建一个", "建个", "放进去", "写入", "导出", "发给我", "发我"))


def _looks_like_approval_detail(*, semantic_frame: SemanticFrame, state: ConversationState) -> bool:
    raw = str(semantic_frame.parameters.get("raw_message") or "")
    compact = re.sub(r"\s+", "", raw)
    if "审批" not in compact or not any(token in compact for token in ("详情", "详细", "打开", "看看", "看")):
        return False
    return state.previous_result_reference.result_type in {"approval_list", "approval_detail", "approval_query"}


def _route_observation(*, intent: str, question_type: str, scope: str, domain: str) -> dict[str, Any]:
    return {
        "intent": intent,
        "question_type": question_type,
        "data_scope": scope,
        "route_source": "conversation_first_v1",
        "route_family": domain.lower() if domain else "",
        "interaction_kind": "conversation_feedback" if intent == "smalltalk" else ("action" if question_type == "action" else "business_query"),
        "misroute_risk": False,
        "risk_reasons": [],
        "denoise_action": "none",
    }


def _previous_external_query(context: RuntimeContext) -> str:
    metadata = context.result_context.metadata if context.result_context is not None and isinstance(context.result_context.metadata, dict) else {}
    return str(metadata.get("external_query") or "").strip()


def _structured_action_entities(*, context: RuntimeContext, frame: CommandFrame) -> dict[str, Any]:
    people_targets = people_targets_from_result_context(context.result_context)
    entities: dict[str, Any] = {}
    if people_targets:
        entities["people_targets"] = [dict(item) for item in people_targets]
    if frame.intent == "mail_draft_create":
        emails = [str(item.get("email") or "").strip() for item in people_targets if str(item.get("email") or "").strip()]
        if emails:
            entities["to"] = ", ".join(emails)
        subject, body = _mail_subject_body(context.current_message)
        if subject:
            entities["subject"] = subject
        if body:
            entities["body"] = body
    elif frame.intent == "calendar_create":
        attendee_ids = [str(item.get("open_id") or "").strip() for item in people_targets if str(item.get("open_id") or "").strip()]
        if attendee_ids:
            entities["attendee_ids"] = attendee_ids
            entities["user_id_type"] = "open_id"
        summary = _calendar_summary(context.current_message)
        if summary:
            entities["summary"] = summary
        start = _calendar_start_hint(context.current_message)
        if start:
            entities["start"] = start
            entities["end"] = f"{start}后"
    elif frame.intent == "task_create":
        member_ids = [str(item.get("open_id") or "").strip() for item in people_targets if str(item.get("open_id") or "").strip()]
        if member_ids:
            entities["members"] = member_ids
            entities["user_id_type"] = "open_id"
        summary = _task_summary(context.current_message)
        if summary:
            entities["summary"] = summary
    return entities


def _mail_subject_body(message: str) -> tuple[str, str]:
    text = str(message or "")
    subject = ""
    body = ""
    subject_match = re.search(r"主题[:：]\s*(?P<subject>.*?)(?:\s*正文[:：]|$)", text)
    if subject_match:
        subject = subject_match.group("subject").strip()
    body_match = re.search(r"正文[:：]\s*(?P<body>.*)$", text)
    if body_match:
        body = body_match.group("body").strip()
    return subject, body


def _task_summary(message: str) -> str:
    text = str(message or "").strip()
    for separator in ("任务：", "任务:", "：", ":"):
        if separator in text:
            return text.rsplit(separator, 1)[1].strip()
    text = re.sub(r"^给这些人创建任务", "", text).strip("：:，, ")
    text = re.sub(r"^创建一个?任务", "", text).strip("：:，, ")
    return text


def _calendar_summary(message: str) -> str:
    text = str(message or "")
    match = re.search(r"主题[:：]\s*(?P<summary>.*)$", text)
    if match:
        return match.group("summary").strip()
    if "会议" in text or "开会" in text:
        return "会议"
    return ""


def _calendar_start_hint(message: str) -> str:
    text = str(message or "")
    match = re.search(r"(今天|明天|后天)?\s*([0-2]?\d)点", text)
    if not match:
        return ""
    day = match.group(1) or ""
    hour = match.group(2)
    return f"{day}{hour}点"


def _filters_include_gender(parameters: dict[str, Any]) -> bool:
    filters = parameters.get("filters") if isinstance(parameters.get("filters"), dict) else {}
    return bool(filters.get("gender") or filters.get("filter") == "gender")


def _canonical_question_from_frame(*, frame: CommandFrame, context: RuntimeContext, entities: dict[str, Any]) -> str:
    if frame.intent != "people_lookup":
        return frame.user_goal or context.current_message
    keyword = str(entities.get("keyword") or "").strip()
    field = str(entities.get("people_query_field") or "").strip()
    if keyword and field:
        return f"{keyword}的{people_field_label(field)}"
    return frame.user_goal or context.current_message


def _looks_like_title_query(message: str) -> bool:
    return any(
        token in str(message or "")
        for token in ("董事长", "负责人", "岗位", "职位", "工程师", "经理", "主管", "总监", "专员", "销售", "财务", "测试", "运营", "人事", "人力资源", "HR", "研发")
    )


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
    operation_kind = "send" if intent == "message_send" or (semantic_frame.speech_act == "request_action" and not _is_read_only_result_presentation_request(semantic_frame)) else "read"
    fields: list[str] = []
    if domain == "People":
        fields.extend(people_fields_from_text(str(semantic_frame.parameters.get("raw_message") or "")))
    field = semantic_frame.parameters.get("field")
    if domain == "People" and isinstance(field, str) and field and field not in fields:
        fields.append(field)
    inherited_field = semantic_frame.parameters.get("inherited_field_projection")
    if domain == "People" and not fields and isinstance(inherited_field, str) and inherited_field and inherited_field not in fields:
        fields.append(inherited_field)
    person_name = str(
        semantic_frame.parameters.get("person_name")
        or (
            semantic_frame.parameters.get("current_object", {}).get("name")
            if isinstance(semantic_frame.parameters.get("current_object"), dict)
            else ""
        )
        or ""
    ).strip()
    if domain == "People" and intent == "organization_snapshot":
        subject: Any = {"type": "organization"}
    elif domain == "People" and intent == "people_lookup" and person_name:
        subject = {"type": "person", "name": person_name}
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
            else person_name
            or semantic_frame.target.get("value")
            or ""
        )
    filters = semantic_frame.parameters.get("filters") if isinstance(semantic_frame.parameters.get("filters"), dict) else {}
    raw_message = str(semantic_frame.parameters.get("raw_message") or "")
    if domain == "People" and any(token in raw_message for token in ("有谁的号码", "谁的号码", "有谁的电话", "谁的电话")):
        filters = {**filters, "field_present": "mobile"}
    relation = str(semantic_frame.parameters.get("organization_relation") or "").strip()
    if relation:
        filters = {**filters, "organization_relation": relation}
    if domain == "People" and intent == "organization_snapshot" and (_looks_like_title_field(str(field or "")) or _looks_like_title_query(raw_message)):
        filters = {**filters, "query_mode": "title_list"}
    output_mode = output_contract["mode"]
    if filters.get("field_present"):
        output_mode = "list"
    elif fields:
        output_mode = "answer"
    elif output_mode == "sidepanel":
        output_mode = "list"
    return {
        "domain": domain.lower(),
        "operation_kind": operation_kind,
        "subject": subject,
        "filters": filters,
        "fields": fields,
        "scope": scope,
        "context_ref": context_contract,
        "output_mode": output_mode,
        "presentation_hint": "" if operation_kind == "send" else ("sidepanel" if output_contract["surface"] == "sidepanel" else "text"),
        "risk_hint": "high" if operation_kind == "send" else "low",
        "evidence_requirement": "source" if fields else "",
    }


def _resource_boundary(*, domain: str, scope: str, semantic_frame: SemanticFrame | None = None) -> str:
    if domain == "People":
        return "enterprise_directory"
    if domain == "Knowledge":
        return "enterprise_knowledge"
    if domain == "Communication" and semantic_frame is not None and semantic_frame.topic == "mail":
        return "personal_mailbox"
    if domain == "Workspace":
        return "personal_productivity"
    if domain == "Communication":
        return "communication_action"
    if domain == "Process":
        return "approval_task"
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
    if domain == "Knowledge" and (semantic_frame.operation == "company_profile" or looks_like_company_profile_query(_semantic_raw_message(semantic_frame))):
        return "foundation_route:knowledge.company_profile"
    if domain == "Knowledge":
        return "foundation_route:knowledge.general"
    if domain == "Communication" and semantic_frame.topic == "mail":
        return "foundation_route:communication.mail"
    if domain == "Communication":
        return f"foundation_route:communication.{intent}"
    if domain == "Workspace":
        return "workspace_signal"
    if domain == "Process":
        return "foundation_route:process.approval"
    if domain == "External":
        return "external_information_signal"
    if domain == "Conversation":
        return "conversation_boundary"
    return f"conversation_first:{semantic_frame.topic}"


def _scope_reason(*, domain: str, scope: str, semantic_frame: SemanticFrame | None = None) -> str:
    if domain == "People" and scope == "person":
        return "person_resource_signal"
    if domain == "People" and scope == "department":
        return "department_resource_signal"
    if domain == "People" and scope == "organization":
        return "organization_resource_signal"
    if domain == "Knowledge" and scope == "company":
        return "company_knowledge_signal"
    if domain == "Communication" and semantic_frame is not None and semantic_frame.topic == "mail":
        return "personal_mailbox_signal"
    if domain == "Workspace":
        return f"{scope}_workspace_signal" if scope in {"company", "department"} else "personal_productivity_signal"
    if domain == "External":
        return "external_information_signal"
    if domain == "Process":
        return "approval_scope_signal"
    return f"conversation_first:{scope}"


def _foundation_route(*, domain: str, intent: str, scope: str, semantic_frame: SemanticFrame | None = None) -> str:
    if domain == "People" and intent == "people_lookup":
        return "people.person"
    if domain == "People" and scope == "department":
        return "people.department_members"
    if domain == "People":
        return "people.organization"
    if domain == "Knowledge" and semantic_frame is not None and (
        semantic_frame.operation == "company_profile" or looks_like_company_profile_query(_semantic_raw_message(semantic_frame))
    ):
        return "knowledge.company_profile"
    if domain == "Knowledge":
        return "knowledge.general"
    if domain == "Communication":
        return f"communication.{intent}"
    if domain == "Workspace":
        return f"workspace.{intent}"
    if domain == "Process":
        return f"process.{intent}"
    if domain == "External":
        return "external.information"
    return ""


def _semantic_raw_message(semantic_frame: SemanticFrame) -> str:
    parameters = semantic_frame.parameters if isinstance(semantic_frame.parameters, dict) else {}
    return str(parameters.get("raw_message") or "")


def _gate_source(*, domain: str) -> str:
    if domain in {"Conversation", "Workspace"}:
        return "rule"
    return "foundation_rule"


def _safety_level(*, intent: str, question_type: str, action_type: str) -> str:
    if intent == "mail_draft_create":
        return "medium"
    if question_type == "action" or action_type in {"send", "write"}:
        return "high"
    return "low"


def _hints_payload(hints: DeterministicSignals) -> dict[str, Any]:
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
