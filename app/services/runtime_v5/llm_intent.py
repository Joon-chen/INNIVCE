from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import os
import re
from typing import Any

from app.core.config import settings
from app.services.llm.gateway import LLMGateway
from app.services.runtime_v5.models import IntentResult, RuntimeContext
from app.services.runtime_v5.planner import strategy_registry


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


def llm_command_intent(
    *,
    question: str,
    context: RuntimeContext,
    rule_intent: IntentResult,
) -> IntentResult | None:
    if not _should_try_llm(rule_intent):
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
    validated = validate_llm_command_intent(candidate, rule_intent=rule_intent)
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
        raw = LLMGateway().complete_text(prompt, temperature=0.0) or ""
    except Exception:
        return None
    data = _parse_json_object(raw)
    if not data:
        return None
    return _candidate_from_payload(data, fallback_question=question)


def validate_llm_command_intent(
    candidate: LLMCommandIntentCandidate,
    *,
    rule_intent: IntentResult,
) -> IntentResult | None:
    if not _should_try_llm(rule_intent):
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
    if not _can_override_rule_intent(candidate_intent=intent, rule_intent=rule_intent):
        return None
    data_scope = _normalize_scope(candidate.data_scope)
    if data_scope not in _DATA_SCOPES:
        return None
    if question_type == "action" and rule_intent.question_type != "action":
        return None
    confidence = max(0.0, min(candidate.confidence, 1.0))
    if guides_clarification and confidence >= 0.6:
        confidence = 0.59
    entities = _merged_entities(candidate=candidate, rule_intent=rule_intent)
    if candidate.clarification:
        entities["clarification_prompt"] = candidate.clarification
    entities["command_intent_trace"] = {
        "source": "llm",
        "rule_intent": rule_intent.intent,
        "llm_intent": intent,
        "final_intent": intent,
        "confidence": confidence,
        "reason": candidate.reason,
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


def _should_try_llm(rule_intent: IntentResult) -> bool:
    if rule_intent.question_type == "action" and rule_intent.confidence >= 0.75:
        return False
    if rule_intent.intent in {"runtime_status", "governance_view", "action_trace"}:
        return False
    if rule_intent.intent in _LLM_OVERRIDEABLE_RULE_INTENTS or rule_intent.confidence < 0.72:
        return True
    return rule_intent.question_type in {"query", "analysis", "insight", "decision"}


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
    )


def _merged_entities(*, candidate: LLMCommandIntentCandidate, rule_intent: IntentResult) -> dict[str, Any]:
    entities = dict(rule_intent.entities)
    entities.update(candidate.entities)
    enrichment = _command_enrichment(candidate)
    if enrichment:
        entities["command_enrichment"] = enrichment
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
    strategies = ", ".join(sorted(strategy_registry().keys()))
    return f"""你是 Digital Advisor V5 的 Command Engine 意图解析器。
你只输出结构化 Intent Candidate，不执行工具，不选择 Provider，不做权限判断，不生成用户回复。

可用 intent 必须严格来自以下列表：
{strategies}

允许 question_type：query, analysis, insight, decision, action
允许 data_scope：self, person, department, company, project, organization, external

当前规则解析：
- intent: {rule_intent.intent}
- question_type: {rule_intent.question_type}
- data_scope: {rule_intent.data_scope}
- confidence: {rule_intent.confidence}

当前上下文：
- role: {context.identity.role}
- domains: {list(context.identity.domains or ())}
- active_company_id: {context.runtime_scope.active_company_id or ""}

	判断原则：
	1. 优先理解用户真实业务意图和查询范围。
	2. 不要把查询改成动作；只有用户明确要求创建、发送、完成、审批等写操作，才输出 action。
	3. 不要输出 Provider、Tool、API、credential 或执行身份字段。
	4. 你可以补充业务语义字段：business_domain、capability、objective、constraints、time_range、output_preferences、semantic_tags。
	5. 规则已经高置信命中具体业务 intent 时，除非用户表达明显不是这个业务，否则保持相同 intent，只做语义补充。
	6. 寒暄、询问你是谁、询问当前用户是谁、当前时间/日期等非业务对话，输出 intent=smalltalk。
	7. 不确定时降低 confidence，不要编造参数。

用户问题：{question[:500]}

只返回 JSON：
	{{"question_type":"query","intent":"task_query","data_scope":"company","entities":{{}},"missing_params":[],"clarification":"","canonical_question":"...","confidence":0.0,"reason":"...","business_domain":"Workspace","capability":"task_query","objective":"查看公司任务负荷","constraints":{{"status":"open"}},"time_range":{{"preset":"this_week"}},"output_preferences":{{"detail_level":"summary","group_by":"owner"}},"semantic_tags":["workload","risk"]}}"""
