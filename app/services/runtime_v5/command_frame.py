from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any

from app.services.runtime_v5.command_route_observer import observe_command_route
from app.services.runtime_v5.intent_layers import build_intent_layer_decision
from app.services.runtime_v5.models import CommandFrame, IntentResult, PlannerResult, RuntimeContext


_DOMAIN_BY_INTENT_PREFIX = {
    "approval": "Process",
    "task": "Workspace",
    "calendar": "Workspace",
    "meeting": "Workspace",
    "people": "People",
    "mail": "Communication",
    "message": "Communication",
    "docs": "Knowledge",
    "wiki": "Knowledge",
    "drive": "Knowledge",
    "sheets": "Knowledge",
    "bitable": "Business",
    "risk": "Intelligence",
    "decision": "Intelligence",
    "workspace": "Intelligence",
}


def build_command_frame(
    *,
    context: RuntimeContext,
    intent: IntentResult,
    planner: PlannerResult,
) -> CommandFrame:
    existing = _existing_command_frame(intent)
    if existing is not None:
        layers = build_intent_layer_decision(context=context, intent=intent, planner=planner)
        return replace(
            existing,
            context_mode=existing.context_mode or layers.context_mode,
            action_type=existing.action_type or layers.action_type,
            safety_level=existing.safety_level or layers.safety_level,
            domain=_normalize_domain(existing.domain) or layers.domain,
            gates=existing.gates or layers.gates,
        )

    domain = _domain_for_intent(intent.intent)
    capability = _capability_for_intent(intent.intent)
    layers = build_intent_layer_decision(context=context, intent=intent, planner=planner)
    dialogue_mode = _dialogue_mode(intent)
    context_used = {
        "conversation": bool(context.chat_id or context.result_context),
        "profile": bool(context.profile),
        "operational": planner.strategy not in {"smalltalk", "runtime_status", "action_trace"},
        "cognitive": intent.question_type in {"analysis", "insight", "decision"},
    }
    response_intent = {
        "tone": _response_tone(intent),
        "should_render_card": _should_render_card(intent=intent, planner=planner),
        "intro_intent": _intro_intent(intent),
    }
    route_observation = observe_command_route(
        question=context.current_message or intent.canonical_question,
        intent=intent.intent,
        question_type=intent.question_type,
        data_scope=intent.data_scope,
        confidence=intent.confidence,
        route_source=_route_path(intent),
    )
    return CommandFrame(
        utterance_type=_utterance_type(intent),
        dialogue_mode=dialogue_mode,
        user_goal=intent.canonical_question or context.current_message,
        intent=intent.intent,
        question_type=intent.question_type,
        domain=layers.domain or domain,
        context_mode=layers.context_mode,
        capability=capability,
        skill_intent=intent.intent,
        scope=intent.data_scope,
        action_type=layers.action_type,
        safety_level=layers.safety_level,
        target=_target_from_entities(intent.entities),
        params=_params_from_entities(intent.entities),
        missing_slots=intent.missing_params,
        gates=layers.gates,
        context_used=context_used,
        response_intent=response_intent,
        draft_response_hint=str(intent.entities.get("draft_response_hint") or ""),
        confidence=intent.confidence,
        needs_clarification=intent.needs_clarification,
        route_reason=layers.route_reason or "rule_or_explicit_command",
        route_path=layers.route_path or _route_path(intent),
        rule_candidate={
            "intent": intent.intent,
            "question_type": intent.question_type,
            "scope": intent.data_scope,
            "confidence": intent.confidence,
            "route_observation": route_observation,
        },
    )


def command_frame_payload(frame: CommandFrame | None) -> dict[str, Any]:
    if frame is None:
        return {}
    payload = asdict(frame)
    payload["missing_slots"] = list(frame.missing_slots)
    return payload


def intent_with_command_frame(intent: IntentResult, frame: CommandFrame) -> IntentResult:
    from dataclasses import replace

    entities = dict(intent.entities)
    entities["command_frame"] = command_frame_payload(frame)
    return replace(intent, entities=entities)


def _existing_command_frame(intent: IntentResult) -> CommandFrame | None:
    entities = intent.entities if isinstance(intent.entities, dict) else {}
    raw = entities.get("command_frame")
    if not isinstance(raw, dict):
        return None
    return CommandFrame(
        utterance_type=str(raw.get("utterance_type") or _utterance_type(intent)),
        dialogue_mode=str(raw.get("dialogue_mode") or _dialogue_mode(intent)),
        user_goal=str(raw.get("user_goal") or intent.canonical_question or ""),
        intent=str(raw.get("intent") or intent.intent),
        question_type=str(raw.get("question_type") or intent.question_type),
        domain=str(raw.get("domain") or _domain_for_intent(intent.intent)),
        context_mode=str(raw.get("context_mode") or ""),
        capability=str(raw.get("capability") or _capability_for_intent(intent.intent)),
        skill_intent=str(raw.get("skill_intent") or raw.get("intent") or intent.intent),
        scope=str(raw.get("scope") or intent.data_scope),
        action_type=str(raw.get("action_type") or ""),
        safety_level=str(raw.get("safety_level") or ""),
        target=raw.get("target") if isinstance(raw.get("target"), dict) else {},
        params=raw.get("params") if isinstance(raw.get("params"), dict) else {},
        missing_slots=tuple(str(item) for item in raw.get("missing_slots", ()) if str(item).strip()) if isinstance(raw.get("missing_slots"), list) else intent.missing_params,
        gates=raw.get("gates") if isinstance(raw.get("gates"), dict) else {},
        context_used=raw.get("context_used") if isinstance(raw.get("context_used"), dict) else {},
        response_intent=raw.get("response_intent") if isinstance(raw.get("response_intent"), dict) else {},
        draft_response_hint=str(raw.get("draft_response_hint") or ""),
        confidence=float(raw.get("confidence") or intent.confidence),
        needs_clarification=bool(raw.get("needs_clarification", intent.needs_clarification)),
        route_reason=str(raw.get("route_reason") or ""),
        route_path=str(raw.get("route_path") or "natural_language"),
        rule_candidate=raw.get("rule_candidate") if isinstance(raw.get("rule_candidate"), dict) else {},
    )


def _dialogue_mode(intent: IntentResult) -> str:
    if intent.needs_clarification:
        return "clarify"
    if intent.intent in {"smalltalk", "runtime_status", "action_trace", "governance_view"}:
        return "answer"
    if intent.question_type == "action":
        return "execute"
    return "present"


def _utterance_type(intent: IntentResult) -> str:
    if intent.intent == "smalltalk":
        return "conversation"
    if intent.intent == "runtime_status":
        return "system_explanation"
    if intent.question_type == "action":
        return "action_request"
    if intent.question_type in {"analysis", "insight", "decision"}:
        return "cognitive_query"
    return "business_query"


def _route_path(intent: IntentResult) -> str:
    entities = intent.entities if isinstance(intent.entities, dict) else {}
    trace = entities.get("command_intent_trace") if isinstance(entities.get("command_intent_trace"), dict) else {}
    if trace.get("source") == "llm":
        return str(trace.get("mode") or "llm")
    if trace.get("source") == "candidate_arbiter":
        return "candidate_arbiter"
    frame = entities.get("command_frame") if isinstance(entities.get("command_frame"), dict) else {}
    if str(frame.get("route_path", "")) == "explicit_command":
        return "explicit_command"
    return "rule"


def _domain_for_intent(intent: str) -> str:
    prefix = str(intent or "").split("_", 1)[0]
    return _DOMAIN_BY_INTENT_PREFIX.get(prefix, "")


def _normalize_domain(domain: str) -> str:
    aliases = {
        "workspace": "Workspace",
        "people": "People",
        "communication": "Communication",
        "knowledge": "Knowledge",
        "process": "Process",
        "system": "System",
        "conversation": "Conversation",
        "external": "External",
        "intelligence": "Intelligence",
        "business": "Business",
    }
    text = str(domain or "").strip()
    return aliases.get(text.lower(), text)


def _capability_for_intent(intent: str) -> str:
    if not intent:
        return ""
    parts = intent.split("_")
    if len(parts) >= 2:
        return "_".join(parts[:2])
    return intent


def _target_from_entities(entities: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in entities.items()
        if key
        in {
            "approval_id",
            "task_id",
            "calendar_id",
            "department_id",
            "target_user_id",
            "target_department_id",
            "object_id",
        }
        and isinstance(value, (str, int, float, bool))
    }


def _params_from_entities(entities: dict[str, Any]) -> dict[str, Any]:
    allowed = {"time_range", "output_preferences", "constraints", "query", "comment", "title", "due", "domain_query"}
    return {key: value for key, value in entities.items() if key in allowed}


def _response_tone(intent: IntentResult) -> str:
    if intent.intent == "smalltalk":
        return "natural"
    if intent.needs_clarification:
        return "guided"
    if intent.question_type == "action":
        return "careful"
    return "concise"


def _should_render_card(*, intent: IntentResult, planner: PlannerResult) -> bool:
    domain_query = intent.entities.get("domain_query") if isinstance(intent.entities.get("domain_query"), dict) else {}
    if domain_query.get("presentation_hint") == "text":
        return False
    if intent.intent == "general_query" and intent.entities.get("knowledge_context") == "company_profile":
        return False
    if intent.intent == "general_query" and intent.data_scope == "company" and _looks_like_company_profile_question(intent.canonical_question):
        return False
    return planner.strategy not in {"smalltalk", "runtime_status", "action_trace"}


def _looks_like_company_profile_question(text: str) -> bool:
    compact = str(text or "").replace(" ", "").lower()
    if not any(token in compact for token in ("公司", "企业", "主营", "业务", "做什么", "干什么")):
        return False
    return any(token in compact for token in ("做什么", "干什么", "主营业务", "主要业务", "业务范围", "公司介绍", "介绍一下"))


def _intro_intent(intent: IntentResult) -> str:
    if intent.needs_clarification:
        return "ask_missing_slots"
    if intent.intent == "smalltalk":
        return "natural_reply"
    if intent.question_type == "action":
        return "confirm_or_report_action"
    return "summarize_result"
