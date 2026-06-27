from __future__ import annotations

from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import json
import logging
import os
import re
from typing import Any

from app.core.config import settings
from app.services.llm.call_trace import last_llm_call_trace
from app.services.llm.routing_policy import llm_route_for_task
from app.services.llm.gateway import LLMGateway
from app.services.runtime_v5.command_route_observer import (
    observe_command_route,
    should_force_self_scope,
    should_reject_candidate_route,
)
from app.services.runtime_v5.models import IntentResult, RuntimeContext
from app.services.runtime_v5.planner import strategy_registry
from app.services.user_context_pack import build_user_context_pack


_LLM_ACCEPT_THRESHOLD = 0.72
_logger = logging.getLogger(__name__)
_LLM_OVERRIDEABLE_RULE_INTENTS = {
    "general_query",
    "general_analysis",
    "risk_analysis",
    "decision_advice",
}
_QUESTION_TYPES = {"query", "analysis", "insight", "decision", "action"}
_DATA_SCOPES = {"self", "person", "department", "company", "project", "organization", "external"}
_SCOPE_ALIASES = {
    "me": "self",
    "mine": "self",
    "user": "person",
    "member": "person",
    "team": "department",
    "dept": "department",
    "enterprise": "company",
    "org": "organization",
}


@dataclass(frozen=True)
class LLMCommandIntentCandidate:
    """Structured candidate produced by LLM for the Command Engine.

    The candidate is not executable. It must be validated and converted to
    IntentResult before Runtime or Policy can see it.
    """

    question_type: str
    intent: str
    data_scope: str
    confidence: float
    canonical_question: str
    entities: dict[str, Any] = field(default_factory=dict)
    missing_params: tuple[str, ...] = ()
    clarification: str = ""
    reason: str = ""
    business_domain: str = ""
    capability: str = ""
    objective: str = ""
    constraints: dict[str, Any] = field(default_factory=dict)
    time_range: dict[str, Any] = field(default_factory=dict)
    output_preferences: dict[str, Any] = field(default_factory=dict)
    semantic_tags: tuple[str, ...] = ()
    draft_response_hint: str = ""
    profile_update: dict[str, Any] = field(default_factory=dict)


def llm_command_intent(
    *,
    question: str,
    context: RuntimeContext,
    rule_intent: IntentResult,
    force: bool = False,
) -> IntentResult | None:
    if not force and not _should_try_llm(rule_intent, question=question, context=context):
        _log_command_route(
            question=question,
            rule_intent=rule_intent,
            final_intent=rule_intent,
            llm_status="skipped",
            llm_reason="rule_confident",
        )
        return None
    candidate = llm_command_intent_candidate(question=question, context=context, rule_intent=rule_intent)
    if candidate is None:
        _log_command_route(
            question=question,
            rule_intent=rule_intent,
            final_intent=rule_intent,
            llm_status="unavailable",
            llm_reason="no_candidate",
        )
        return None
    validated = validate_llm_command_intent(candidate, rule_intent=rule_intent, force=force)
    if validated is None:
        _log_command_route(
            question=question,
            rule_intent=rule_intent,
            final_intent=rule_intent,
            llm_status="rejected",
            llm_reason=candidate.reason or "validation_failed",
            candidate=candidate,
        )
        return None
    _log_command_route(
        question=question,
        rule_intent=rule_intent,
        final_intent=validated,
        llm_status="accepted",
        llm_reason=candidate.reason or "accepted",
        candidate=candidate,
    )
    return validated


def llm_command_intent_candidate(
    *,
    question: str,
    context: RuntimeContext,
    rule_intent: IntentResult,
) -> LLMCommandIntentCandidate | None:
    if _running_tests():
        return None
    if not settings.bot_llm_semantics_enabled:
        return None
    prompt = _prompt(question=question, context=context, rule_intent=rule_intent)
    try:
        raw = _complete_command_intent_with_deadline(prompt) or ""
    except Exception:
        return None
    data = _parse_json_object(raw)
    if not data:
        return None
    return _candidate_from_payload(data, fallback_question=question)


def _complete_command_intent_with_deadline(prompt: str) -> str | None:
    timeout_seconds = max(0.5, llm_route_for_task("command_intent").latency_budget_ms / 1000.0)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="command-intent-llm")
    future = executor.submit(lambda: LLMGateway().complete_task_text(prompt, task_type="command_intent", temperature=0.0))
    try:
        return future.result(timeout=timeout_seconds)
    except TimeoutError:
        future.cancel()
        return None
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def validate_llm_command_intent(
    candidate: LLMCommandIntentCandidate,
    *,
    rule_intent: IntentResult,
    force: bool = False,
) -> IntentResult | None:
    if not force and not _should_try_llm(rule_intent) and not _smalltalk_profile_candidate(candidate, rule_intent):
        return None
    missing_params = tuple(str(item).strip() for item in candidate.missing_params if str(item).strip())
    guides_clarification = bool(missing_params and candidate.confidence >= 0.35)
    if candidate.confidence < _LLM_ACCEPT_THRESHOLD and not guides_clarification:
        return None
    question_type = candidate.question_type.strip().lower()
    if question_type not in _QUESTION_TYPES:
        return None
    intent = candidate.intent.strip()
    if intent not in strategy_registry():
        return None
    if not force and not _can_override_rule_intent(candidate_intent=intent, rule_intent=rule_intent):
        return None
    data_scope = _normalize_scope(candidate.data_scope)
    if data_scope not in _DATA_SCOPES:
        return None
    if should_reject_candidate_route(
        question=candidate.canonical_question or rule_intent.canonical_question,
        candidate_intent=intent,
        candidate_question_type=question_type,
        candidate_scope=data_scope,
        rule_intent=rule_intent.intent,
    ):
        return None
    if intent == "task_query" and _should_keep_knowledge_query_route(candidate=candidate, rule_intent=rule_intent):
        return None
    if should_force_self_scope(
        question=candidate.canonical_question or rule_intent.canonical_question,
        intent=intent,
        data_scope=data_scope,
    ):
        data_scope = "self"
    if question_type == "action" and rule_intent.question_type != "action":
        return None
    confidence = max(0.0, min(candidate.confidence, 1.0))
    if guides_clarification and confidence >= 0.6:
        confidence = 0.59
    entities = _merged_entities(candidate=candidate, rule_intent=rule_intent)
    draft_response_hint = _safe_draft_response_hint(candidate)
    if draft_response_hint:
        entities["draft_response_hint"] = draft_response_hint
        if intent == "smalltalk":
            entities["fallback_answer"] = draft_response_hint
    if candidate.clarification:
        entities["clarification_prompt"] = candidate.clarification
    command_frame = _command_frame_payload(
        candidate=candidate,
        intent=intent,
        question_type=question_type,
        data_scope=data_scope,
        confidence=confidence,
        missing_params=missing_params,
        force=force,
        rule_intent=rule_intent,
    )
    entities["command_frame"] = command_frame
    entities["command_intent_trace"] = {
        "source": "llm",
        "mode": "llm_first" if force else "fallback",
        "rule_intent": rule_intent.intent,
        "llm_intent": intent,
        "final_intent": intent,
        "confidence": confidence,
        "reason": candidate.reason,
        "command_frame": command_frame,
        "llm_call": last_llm_call_trace(),
        "route_observation": command_frame.get("route_observation", {}),
    }
    return IntentResult(
        question_type=question_type,  # type: ignore[arg-type]
        intent=intent,
        data_scope=data_scope,  # type: ignore[arg-type]
        entities=entities,
        missing_params=missing_params,
        confidence=confidence,
        canonical_question=candidate.canonical_question or rule_intent.canonical_question,
    )


def _smalltalk_profile_candidate(candidate: LLMCommandIntentCandidate, rule_intent: IntentResult) -> bool:
    return (
        rule_intent.intent == "smalltalk"
        and candidate.intent == "smalltalk"
        and bool(candidate.profile_update or candidate.draft_response_hint)
    )


def _should_try_llm(
    rule_intent: IntentResult,
    *,
    question: str = "",
    context: RuntimeContext | None = None,
) -> bool:
    if rule_intent.question_type == "action" and rule_intent.confidence >= 0.75:
        return False
    if rule_intent.intent in {"runtime_status", "governance_view", "action_trace"}:
        return False
    if rule_intent.intent == "smalltalk":
        return _needs_contextual_semantics(question=question, context=context)
    if rule_intent.missing_params or rule_intent.confidence < 0.72:
        return True
    if rule_intent.intent in _LLM_OVERRIDEABLE_RULE_INTENTS:
        return True
    if _needs_contextual_semantics(question=question, context=context):
        return True
    return False


def _needs_contextual_semantics(*, question: str, context: RuntimeContext | None) -> bool:
    compact = re.sub(r"\s+", "", str(question or "").strip())
    if not compact:
        return False
    if len(compact) <= 8 and _has_contextual_marker(compact):
        return True
    if context is not None and context.result_context is not None and _has_contextual_marker(compact):
        return True
    return False


def _has_contextual_marker(text: str) -> bool:
    return any(token in text for token in ("这个", "那个", "这些", "那些", "刚才", "上面", "继续", "展开", "第一个", "第二个"))


def _log_command_route(
    *,
    question: str,
    rule_intent: IntentResult,
    final_intent: IntentResult,
    llm_status: str,
    llm_reason: str,
    candidate: LLMCommandIntentCandidate | None = None,
) -> None:
    if _running_tests():
        return
    _logger.info(
        "command_intent_route question=%r rule=%s/%s/%s/%s llm_status=%s llm_candidate=%s/%s/%s final=%s/%s/%s/%s reason=%s",
        question[:120],
        rule_intent.intent,
        rule_intent.question_type,
        rule_intent.data_scope,
        round(rule_intent.confidence, 3),
        llm_status,
        candidate.intent if candidate else "",
        candidate.data_scope if candidate else "",
        round(candidate.confidence, 3) if candidate else "",
        final_intent.intent,
        final_intent.question_type,
        final_intent.data_scope,
        round(final_intent.confidence, 3),
        llm_reason[:160],
    )


def _can_override_rule_intent(*, candidate_intent: str, rule_intent: IntentResult) -> bool:
    if candidate_intent == rule_intent.intent:
        return True
    return rule_intent.intent in _LLM_OVERRIDEABLE_RULE_INTENTS or rule_intent.confidence < 0.72


def _should_keep_knowledge_query_route(*, candidate: LLMCommandIntentCandidate, rule_intent: IntentResult) -> bool:
    question = f"{candidate.canonical_question} {candidate.objective} {candidate.reason} {rule_intent.canonical_question}"
    compact = re.sub(r"\s+", "", question.lower())
    if not compact:
        return False
    if not any(term in compact for term in ("流程", "制度", "规范", "手册", "模板", "sop", "说明", "指南", "资料", "文档", "知识库", "wiki")):
        return False
    if any(term in compact for term in ("我的任务", "我的待办", "任务负荷", "待办负荷", "延期任务", "到期任务", "任务有哪些", "待办有哪些")):
        return False
    return rule_intent.intent == "general_query" or rule_intent.entities.get("foundation_route") == "knowledge.general"


def _candidate_from_payload(data: dict[str, Any], *, fallback_question: str) -> LLMCommandIntentCandidate | None:
    try:
        confidence = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    entities = data.get("entities") if isinstance(data.get("entities"), dict) else {}
    missing = data.get("missing_params") if isinstance(data.get("missing_params"), list) else []
    return LLMCommandIntentCandidate(
        question_type=str(data.get("question_type") or "").strip(),
        intent=str(data.get("intent") or "").strip(),
        data_scope=str(data.get("data_scope") or "").strip(),
        entities=entities,
        missing_params=tuple(str(item) for item in missing),
        clarification=str(data.get("clarification") or "").strip()[:300],
        confidence=max(0.0, min(confidence, 1.0)),
        canonical_question=str(data.get("canonical_question") or fallback_question).strip()[:300],
        reason=str(data.get("reason") or "").strip()[:300],
        business_domain=str(data.get("business_domain") or "").strip()[:80],
        capability=str(data.get("capability") or "").strip()[:120],
        objective=str(data.get("objective") or "").strip()[:200],
        constraints=data.get("constraints") if isinstance(data.get("constraints"), dict) else {},
        time_range=data.get("time_range") if isinstance(data.get("time_range"), dict) else {},
        output_preferences=data.get("output_preferences") if isinstance(data.get("output_preferences"), dict) else {},
        semantic_tags=tuple(str(item).strip()[:80] for item in data.get("semantic_tags", []) if str(item).strip()) if isinstance(data.get("semantic_tags"), list) else (),
        draft_response_hint=str(data.get("draft_response_hint") or "").strip()[:500],
        profile_update=data.get("profile_update") if isinstance(data.get("profile_update"), dict) else {},
    )


def _merged_entities(*, candidate: LLMCommandIntentCandidate, rule_intent: IntentResult) -> dict[str, Any]:
    entities = dict(rule_intent.entities)
    entities.update(candidate.entities)
    enrichment = _command_enrichment(candidate)
    if enrichment:
        entities["command_enrichment"] = enrichment
    profile_update = _safe_profile_update(candidate.profile_update)
    if profile_update:
        entities["profile_update_candidate"] = profile_update
    return entities


def _command_enrichment(candidate: LLMCommandIntentCandidate) -> dict[str, Any]:
    enrichment: dict[str, Any] = {}
    for key in ("business_domain", "capability", "objective", "reason"):
        value = str(getattr(candidate, key) or "").strip()
        if value:
            enrichment[key] = value
    if candidate.constraints:
        enrichment["constraints"] = _safe_dict(candidate.constraints)
    if candidate.time_range:
        enrichment["time_range"] = _safe_dict(candidate.time_range)
    if candidate.output_preferences:
        enrichment["output_preferences"] = _safe_dict(candidate.output_preferences)
    if candidate.semantic_tags:
        enrichment["semantic_tags"] = list(candidate.semantic_tags[:8])
    return enrichment


def _command_frame_payload(
    *,
    candidate: LLMCommandIntentCandidate,
    intent: str,
    question_type: str,
    data_scope: str,
    confidence: float,
    missing_params: tuple[str, ...],
    force: bool,
    rule_intent: IntentResult,
) -> dict[str, Any]:
    route_observation = observe_command_route(
        question=candidate.canonical_question,
        intent=intent,
        question_type=question_type,
        data_scope=data_scope,
        confidence=confidence,
        route_source="llm_first" if force else "llm_fallback",
    )
    return {
        "utterance_type": _utterance_type(question_type=question_type, intent=intent),
        "dialogue_mode": _dialogue_mode(intent=intent, question_type=question_type, missing_params=missing_params),
        "user_goal": candidate.objective or candidate.canonical_question,
        "intent": intent,
        "question_type": question_type,
        "domain": candidate.business_domain,
        "capability": candidate.capability or intent,
        "skill_intent": intent,
        "scope": data_scope,
        "target": _safe_dict(candidate.entities),
        "params": {
            "constraints": _safe_dict(candidate.constraints),
            "time_range": _safe_dict(candidate.time_range),
            "output_preferences": _safe_dict(candidate.output_preferences),
        },
        "missing_slots": list(missing_params),
        "context_used": {
            "conversation": bool(_has_contextual_marker(candidate.canonical_question)),
            "profile": True,
            "operational": intent not in {"smalltalk", "runtime_status", "action_trace"},
            "cognitive": question_type in {"analysis", "insight", "decision"},
        },
        "response_intent": {
            "tone": _response_tone(intent=intent, question_type=question_type, missing_params=missing_params),
            "should_render_card": intent not in {"smalltalk", "runtime_status", "action_trace"},
            "intro_intent": _intro_intent(intent=intent, question_type=question_type, missing_params=missing_params),
        },
        "profile_update": _safe_profile_update(candidate.profile_update),
        "draft_response_hint": _safe_draft_response_hint(candidate),
        "confidence": confidence,
        "needs_clarification": bool(missing_params),
        "route_reason": candidate.reason,
        "route_path": "llm_first" if force else "llm_fallback",
        "route_observation": route_observation,
        "rule_candidate": {
            "intent": rule_intent.intent,
            "question_type": rule_intent.question_type,
            "scope": rule_intent.data_scope,
            "confidence": rule_intent.confidence,
        },
    }


def _utterance_type(*, question_type: str, intent: str) -> str:
    if intent == "smalltalk":
        return "conversation"
    if question_type == "action":
        return "action_request"
    if question_type in {"analysis", "insight", "decision"}:
        return "cognitive_query"
    return "business_query"


def _dialogue_mode(*, intent: str, question_type: str, missing_params: tuple[str, ...]) -> str:
    if missing_params:
        return "clarify"
    if intent in {"smalltalk", "runtime_status", "action_trace", "governance_view"}:
        return "answer"
    if question_type == "action":
        return "execute"
    return "present"


def _response_tone(*, intent: str, question_type: str, missing_params: tuple[str, ...]) -> str:
    if missing_params:
        return "guided"
    if intent == "smalltalk":
        return "natural"
    if question_type == "action":
        return "careful"
    return "concise"


def _intro_intent(*, intent: str, question_type: str, missing_params: tuple[str, ...]) -> str:
    if missing_params:
        return "ask_missing_slots"
    if intent == "smalltalk":
        return "natural_reply"
    if question_type == "action":
        return "confirm_or_report_action"
    return "summarize_result"


def _safe_draft_response_hint(candidate: LLMCommandIntentCandidate) -> str:
    text = str(candidate.draft_response_hint or "").strip()
    if not text:
        return ""
    forbidden = (
        "我已查询",
        "已查询到",
        "我已创建",
        "我已发送",
        "我已审批",
        "已完成审批",
        "我已经完成",
    )
    if any(item in text for item in forbidden):
        return ""
    return text[:500]


def _safe_dict(value: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            safe[key[:80]] = item
        elif isinstance(item, list):
            safe[key[:80]] = [entry for entry in item if isinstance(entry, (str, int, float, bool))][:12]
    return safe


def _safe_profile_update(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    update: dict[str, Any] = {}
    preferred_address = str(value.get("preferred_address") or "").strip()
    if preferred_address and len(preferred_address) <= 30:
        update["preferred_address"] = preferred_address
    if "avoid_direct_name" in value:
        update["avoid_direct_name"] = bool(value.get("avoid_direct_name"))
    tone_tips = str(value.get("tone_tips") or "").strip()
    if tone_tips and len(tone_tips) <= 200:
        update["tone_tips"] = tone_tips
    style = str(value.get("style") or "").strip()
    if style in {"professional", "casual", "formal", "direct", "warm"}:
        update["style"] = style
    verbosity = str(value.get("verbosity") or "").strip()
    if verbosity in {"concise", "balanced", "detailed"}:
        update["verbosity"] = verbosity
    return update


def _normalize_scope(value: str) -> str:
    normalized = value.strip().lower()
    return _SCOPE_ALIASES.get(normalized, normalized)


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _running_tests() -> bool:
    return bool(os.getenv("PYTEST_CURRENT_TEST"))


def _prompt(*, question: str, context: RuntimeContext, rule_intent: IntentResult) -> str:
    strategies = _strategy_prompt_summary()
    user_context = build_user_context_pack(runtime_context=context, question=question, purpose="command")
    return f"""Role: V5 Command Engine. JSON only.
Goal: select intent, scope/slots, and safe draft_response_hint.
Never execute, pick provider/token, decide permission, or claim completed data.

Capability and skill summary:
{strategies}

Types: query, analysis, insight, decision, action
Scopes: self, person, department, company, project, organization, external

Rule:
intent={rule_intent.intent}; question_type={rule_intent.question_type}; scope={rule_intent.data_scope}; confidence={rule_intent.confidence}

{user_context.prompt_sections()}

Routing:
- Action only for explicit create/send/complete/approve/reject; otherwise keep query/analysis/conversation.
- Chat/identity/time/preference/feedback/follow-up without concrete business operation => smalltalk + natural draft_response_hint.
- Preference corrections => profile_update (address/style/tone/verbosity); do not change facts or permissions.
- Slowness/wrong route/boundary => runtime_status or general_analysis.
- Weather/news/websites/prices/laws/public current info => external_information_query.
	- Company profile/business => general_query + knowledge_context company_profile; policy/process/SOP/templates/manuals/project documents/how-to knowledge => general_query + knowledge_context general. Workspace needs explicit task/calendar/project/workload/deadline/schedule signal.
- people_lookup only for concrete person lookup. Missing scope/object/time => missing_params; never invent.

Question:
{question[:360]}

Schema:
{{"question_type":"query","intent":"task_query","data_scope":"company","entities":{{}},"missing_params":[],"canonical_question":"...","confidence":0.0,"reason":"...","business_domain":"","capability":"","objective":"","draft_response_hint":"","profile_update":{{"preferred_address":"","avoid_direct_name":false,"tone_tips":"","style":"","verbosity":""}}}}"""


def _compact_context_quality(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    return {
        "turns": payload.get("included_turn_count", 0),
        "result": bool(payload.get("result_included", False)),
        "signals": payload.get("signal_count", 0),
        "truncated": bool(payload.get("truncated", False)),
    }


def _conversation_pack_quality(pack: Any) -> dict[str, Any]:
    quality = getattr(pack, "quality", None)
    if callable(quality):
        return _compact_context_quality(quality())
    return _compact_context_quality({})


def _strategy_prompt_summary() -> str:
    available = set(strategy_registry().keys())
    groups = {
        "conversation": ("smalltalk", "runtime_status", "action_trace"),
        "workspace": ("task_query", "task_create", "task_complete", "calendar_query", "calendar_create"),
        "process": ("approval_query", "approval_detail", "approval_approve", "approval_reject", "approval_transfer", "approval_add_sign"),
        "people": ("people_lookup", "department_members", "organization_snapshot"),
        "communication": ("message_query", "message_send", "mail_query", "mail_search", "mail_draft_create"),
        "knowledge": ("docs_read", "docs_edit", "wiki_search", "drive_list"),
        "business_intelligence": ("general_query", "general_analysis", "risk_analysis", "decision_advice"),
        "external_information": ("external_information_query",),
    }
    lines = []
    for domain, intents in groups.items():
        visible = [item for item in intents if item in available]
        if visible:
            lines.append(f"{domain}: {', '.join(visible)}")
    return "\n".join(lines)
