from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any

from app.services.runtime_v5.models import IntentResult, PlannerResult, RuntimeContext
from app.services.runtime_v5.result_followup import detect_result_followup


@dataclass(frozen=True)
class IntentLayerDecision:
    utterance_type: str
    domain: str
    context_mode: str
    scope: str
    action_type: str
    safety_level: str
    route_path: str
    route_reason: str
    gates: dict[str, Any]


_DOMAIN_BY_INTENT_PREFIX = {
    "approval": "Process",
    "task": "Workspace",
    "tasklist": "Workspace",
    "calendar": "Workspace",
    "meeting": "Workspace",
    "people": "People",
    "department": "People",
    "organization": "People",
    "mail": "Communication",
    "message": "Communication",
    "chat": "Communication",
    "docs": "Knowledge",
    "wiki": "Knowledge",
    "drive": "Knowledge",
    "sheets": "Knowledge",
    "slides": "Knowledge",
    "whiteboard": "Knowledge",
    "base": "Business",
    "risk": "Intelligence",
    "decision": "Intelligence",
    "workspace": "Intelligence",
    "general": "Knowledge",
    "external": "External",
    "runtime": "System",
    "action": "System",
    "governance": "System",
    "smalltalk": "Conversation",
}

_HIGH_SAFETY_INTENTS = {
    "approval_approve",
    "approval_reject",
    "approval_transfer",
    "approval_add_sign",
    "approval_rollback",
    "approval_remind",
    "approval_cancel",
    "approval_cc",
    "message_send",
    "calendar_create",
    "mail_draft_create",
    "mail_send",
    "task_create",
    "task_complete",
    "task_update",
    "task_reopen",
    "task_delete",
    "task_subtask_create",
    "task_comment",
    "task_assign_members",
    "chat_create",
    "chat_auto_join_public",
    "organization_export",
}


def build_intent_layer_decision(
    *,
    context: RuntimeContext,
    intent: IntentResult,
    planner: PlannerResult,
) -> IntentLayerDecision:
    entities = intent.entities if isinstance(intent.entities, dict) else {}
    route_path = _route_path(entities)
    utterance_type = _utterance_type(intent=intent, entities=entities)
    domain = _domain_for_intent(intent.intent)
    domain_gate = _domain_gate(intent=intent, entities=entities, domain=domain, route_path=route_path)
    context_mode = _context_mode(context=context, intent=intent, entities=entities)
    scope_gate = _scope_gate(context=context, intent=intent, entities=entities, route_path=route_path)
    action_type = _action_type(intent=intent, planner=planner, entities=entities)
    safety_level = _safety_level(intent=intent, action_type=action_type, entities=entities)
    action_contract = _action_contract(
        intent=intent,
        planner=planner,
        entities=entities,
        action_type=action_type,
        context_mode=context_mode,
    )
    safety_contract = _safety_contract(
        intent=intent,
        entities=entities,
        action_type=action_type,
        safety_level=safety_level,
        action_contract=action_contract,
    )
    route_reason = _route_reason(entities=entities, route_path=route_path)
    gates = {
        "utterance": {
            "type": utterance_type,
            "question_type": intent.question_type,
            "dialogue_mode": _dialogue_mode(intent),
        },
        "domain": {
            "domain": domain,
            "reason": domain_gate["reason"],
            "source": domain_gate["source"],
            "confidence": domain_gate["confidence"],
            "intent": intent.intent,
            "capability": _capability_for_intent(intent.intent),
            "strategy": planner.strategy,
            "sources": list(planner.sources),
        },
        "context": {
            "mode": context_mode,
            "has_result_context": context.result_context is not None,
            "result_type": getattr(context.result_context, "result_type", ""),
            "result_count": getattr(context.result_context, "count", 0),
        },
        "scope": {
            "scope": scope_gate["scope"],
            "requested_scope": scope_gate["requested_scope"],
            "resolved_scope": scope_gate["resolved_scope"],
            "reason": scope_gate["reason"],
            "source": scope_gate["source"],
            "resource_boundary": scope_gate["resource_boundary"],
            "target": scope_gate["target"],
            "runtime_scope": context.runtime_scope.scope_type,
            "company_id": str(context.runtime_scope.active_company_id or ""),
            "department_id": context.runtime_scope.active_department_id,
        },
        "action": {
            "type": action_type,
            "operation_kind": action_contract["operation_kind"],
            "execution_mode": action_contract["execution_mode"],
            "target_source": action_contract["target_source"],
            "confirmation_hint": action_contract["confirmation_hint"],
            "credential_hint": action_contract["credential_hint"],
            "missing_slots": list(intent.missing_params),
            "target": _target_summary(entities),
            "contract": action_contract,
        },
        "safety": {
            "level": safety_level,
            "confirmation_expected": safety_contract["confirmation_expected"],
            "credential_expected": safety_contract["credential_expected"],
            "risk_reasons": safety_contract["risk_reasons"],
            "blocks_execution": safety_contract["blocks_execution"],
            "needs_clarification": intent.needs_clarification,
            "contract": safety_contract,
        },
        "route": {
            "path": route_path,
            "reason": route_reason,
            "foundation_route": str(entities.get("foundation_route") or ""),
        },
    }
    return IntentLayerDecision(
        utterance_type=utterance_type,
        domain=domain,
        context_mode=context_mode,
        scope=intent.data_scope,
        action_type=action_type,
        safety_level=safety_level,
        route_path=route_path,
        route_reason=route_reason,
        gates=gates,
    )


def intent_layer_payload(decision: IntentLayerDecision | None) -> dict[str, Any]:
    return asdict(decision) if decision is not None else {}


def _utterance_type(*, intent: IntentResult, entities: dict[str, Any]) -> str:
    if entities.get("explicit_command"):
        return "explicit_command"
    if intent.intent == "smalltalk":
        return "conversation"
    if intent.intent in {"runtime_status", "action_trace", "governance_view"}:
        return "system_explanation"
    if intent.question_type == "action":
        return "action_request"
    if intent.question_type in {"analysis", "insight", "decision"}:
        return "cognitive_query"
    return "business_query"


def _dialogue_mode(intent: IntentResult) -> str:
    if intent.needs_clarification:
        return "clarify"
    if intent.intent in {"smalltalk", "runtime_status", "action_trace", "governance_view"}:
        return "answer"
    if intent.question_type == "action":
        return "execute"
    return "present"


def _context_mode(*, context: RuntimeContext, intent: IntentResult, entities: dict[str, Any]) -> str:
    if not context.result_context:
        return "new_question"
    if entities.get("people_targets") or entities.get("people_target_count"):
        return "action_on_people_context"
    followup = detect_result_followup(context.current_message, context.result_context)
    if followup.is_result_followup and not should_start_new_question_over_result_context(
        context.current_message,
        context.result_context,
        followup=followup,
    ):
        return "inherit_result_context"
    if entities.get("foundation_route"):
        return "new_question"
    if intent.intent in {"people_lookup", "department_members", "organization_snapshot", "general_query"}:
        return "new_question"
    if intent.intent in {"message_send", "mail_draft_create", "calendar_create", "task_create"}:
        return "action_on_result_context"
    return "result_followup_candidate"


def should_start_new_question_over_result_context(message: str, result_context: Any = None, *, followup: Any = None) -> bool:
    if followup is not None and getattr(followup, "is_result_followup", False):
        return _looks_like_new_people_question(message)
    if _is_short_result_reference(message):
        return False
    if _looks_like_new_people_question(message):
        return True
    return any(
        token in message
        for token in (
            "组织架构",
            "组织结构",
            "通讯录",
            "有哪些人",
            "都有谁",
            "电话",
            "邮箱",
            "手机号",
            "职位",
            "日程",
            "会议",
            "开会",
            "邮件",
            "待办",
            "任务",
            "审批",
        )
    )


def _is_short_result_reference(message: str) -> bool:
    text = str(message or "").strip()
    if len(text) > 16:
        return False
    return any(term in text for term in ("详情", "明细", "展开", "第一个", "第二个", "第三个", "最后一个", "这些", "他们", "她们"))


def _looks_like_new_people_question(message: str) -> bool:
    compact = re.sub(r"\s+", "", str(message or ""))
    if not compact:
        return False
    if any(token in compact for token in ("这些人", "他们", "她们", "上面", "刚才", "上一轮")):
        return False
    if _looks_like_people_count_or_metric_question(compact):
        return True
    if any(token in compact for token in ("电话", "号码", "手机号", "邮箱")) and re.search(r"[\u4e00-\u9fffA-Za-z·.\-]{2,32}(?:的)?(?:电话|号码|手机号|邮箱)", compact):
        return True
    if any(token in compact for token in ("岗位", "职位", "职务")) and re.search(r"[\u4e00-\u9fffA-Za-z·.\-]{2,32}(?:的)?(?:岗位|职位|职务)", compact):
        return True
    if any(token in compact for token in ("男还是女", "女还是男", "性别")) and re.search(r"[\u4e00-\u9fffA-Za-z·.\-]{2,32}(?:是)?(?:男还是女|女还是男|什么性别|性别)", compact):
        return True
    if any(token in compact for token in ("部门", "事业部", "财务部", "研发部", "测试部", "运营部", "销售部")) and any(
        token in compact for token in ("多少人", "几个人", "几位", "有哪些人", "都有谁", "名单")
    ):
        return True
    return False


def _looks_like_people_count_or_metric_question(compact: str) -> bool:
    scope_signal = any(token in compact for token in ("公司", "我们公司", "全公司", "通讯录", "组织", "企业"))
    metric_signal = any(token in compact for token in ("多少人", "多少位", "几个人", "几位", "人数", "男生", "男性", "女生", "女性"))
    if scope_signal and metric_signal:
        return True
    if any(token in compact for token in ("有多少男生", "多少男生", "有多少女生", "多少女生", "男生有多少", "女生有多少")):
        return True
    return False


def _action_type(*, intent: IntentResult, planner: PlannerResult, entities: dict[str, Any]) -> str:
    if intent.question_type != "action":
        return "read"
    if intent.intent == "mail_draft_create":
        return "draft"
    if intent.intent == "message_send":
        return "send"
    if planner.strategy.endswith("_create") or planner.strategy.endswith("_update") or planner.strategy.endswith("_delete"):
        return "write"
    return "write"


def _safety_level(*, intent: IntentResult, action_type: str, entities: dict[str, Any]) -> str:
    if intent.needs_clarification:
        return "clarify"
    if action_type == "draft":
        return "medium"
    if intent.intent in _HIGH_SAFETY_INTENTS or action_type in {"write", "send"}:
        return "high"
    if entities.get("explicit_command"):
        return "system"
    return "low"


def _action_contract(
    *,
    intent: IntentResult,
    planner: PlannerResult,
    entities: dict[str, Any],
    action_type: str,
    context_mode: str,
) -> dict[str, Any]:
    operation_kind = _operation_kind(intent=intent, action_type=action_type)
    execution_mode = _execution_mode(intent=intent, entities=entities, action_type=action_type)
    target_source = _action_target_source(entities=entities, context_mode=context_mode)
    confirmation_hint = _confirmation_hint(intent=intent, action_type=action_type, entities=entities)
    credential_hint = _credential_hint(intent=intent, planner=planner, action_type=action_type)
    return {
        "operation_kind": operation_kind,
        "action_type": action_type,
        "execution_mode": execution_mode,
        "target_source": target_source,
        "confirmation_hint": confirmation_hint,
        "credential_hint": credential_hint,
        "people_target_count": int(entities.get("people_target_count") or len(entities.get("people_targets") or [])),
        "delivery_mode": str(entities.get("delivery_mode") or ""),
        "strategy": planner.strategy,
        "sources": list(planner.sources),
    }


def _operation_kind(*, intent: IntentResult, action_type: str) -> str:
    if action_type == "read":
        return "read"
    if intent.intent == "mail_draft_create":
        return "draft"
    if intent.intent == "message_send":
        return "send"
    if intent.intent.startswith("approval_"):
        return "approval_action"
    if intent.intent.startswith("calendar_"):
        return "schedule_write"
    if intent.intent.startswith("task_"):
        return "workspace_write"
    if intent.intent.startswith("chat_"):
        return "chat_write"
    return "write"


def _execution_mode(*, intent: IntentResult, entities: dict[str, Any], action_type: str) -> str:
    if action_type == "read":
        return "bot_read"
    if intent.intent == "message_send":
        delivery_mode = str(entities.get("delivery_mode") or "").strip()
        if delivery_mode == "bot_multi_notify":
            return "bot_notify"
        if delivery_mode in {"user_direct", "user_multi_send", "user_multi_private"}:
            return "user_delegated_send"
        if delivery_mode == "create_group_then_send":
            return "create_group_then_send"
        return "unresolved_delivery_mode"
    if intent.intent == "mail_draft_create":
        return "user_draft"
    if intent.intent == "mail_send":
        return "user_send"
    if action_type == "draft":
        return "user_draft"
    return "user_write"


def _action_target_source(*, entities: dict[str, Any], context_mode: str) -> str:
    if entities.get("people_targets") or entities.get("people_target_count"):
        return "people_result_context"
    if str(entities.get("target_type") or "") == "people_context":
        return "people_result_context"
    if context_mode in {"action_on_people_context", "action_on_result_context"}:
        return "result_context"
    if any(key in entities for key in ("to", "attendee_ids", "members", "target_user_id", "chat_id", "approval_id", "task_id")):
        return "explicit_target"
    return "implicit_or_missing"


def _confirmation_hint(*, intent: IntentResult, action_type: str, entities: dict[str, Any]) -> str:
    if action_type == "send" and not str(entities.get("delivery_mode") or "").strip():
        return "clarify_delivery_mode"
    if intent.needs_clarification or intent.missing_params:
        return "clarify_before_confirmation"
    if action_type == "read":
        return "not_required"
    if action_type == "draft":
        return "confirm_draft_creation"
    if action_type == "send":
        return "confirm_before_send"
    return "confirm_before_write"


def _credential_hint(*, intent: IntentResult, planner: PlannerResult, action_type: str) -> str:
    if action_type == "read":
        if planner.strategy in {"mail_query", "mail_search", "mail_get_message"}:
            return "user_resource_or_authorized_fallback"
        return "tenant_or_bot_read"
    if intent.intent == "message_send":
        return "depends_on_delivery_mode"
    if intent.intent.startswith("mail_"):
        return "user_token_required"
    if intent.intent.startswith("calendar_") or intent.intent.startswith("task_") or intent.intent.startswith("approval_"):
        return "user_token_required"
    return "authorization_required_for_write"


def _safety_contract(
    *,
    intent: IntentResult,
    entities: dict[str, Any],
    action_type: str,
    safety_level: str,
    action_contract: dict[str, Any],
) -> dict[str, Any]:
    risk_reasons = _risk_reasons(
        intent=intent,
        entities=entities,
        action_type=action_type,
        action_contract=action_contract,
    )
    confirmation_expected = action_contract["confirmation_hint"] not in {"not_required"}
    credential_expected = action_contract["credential_hint"] not in {"tenant_or_bot_read"}
    return {
        "level": safety_level,
        "confirmation_expected": confirmation_expected,
        "confirmation_hint": action_contract["confirmation_hint"],
        "credential_expected": credential_expected,
        "credential_hint": action_contract["credential_hint"],
        "risk_reasons": risk_reasons,
        "blocks_execution": bool(intent.needs_clarification or intent.missing_params),
    }


def _risk_reasons(
    *,
    intent: IntentResult,
    entities: dict[str, Any],
    action_type: str,
    action_contract: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    if intent.needs_clarification or intent.missing_params:
        reasons.append("missing_or_ambiguous_slots")
    if intent.intent in _HIGH_SAFETY_INTENTS or action_type in {"write", "send"}:
        reasons.append("high_risk_action")
    if action_type == "draft":
        reasons.append("draft_creates_external_artifact")
    if action_contract.get("target_source") == "people_result_context":
        reasons.append("uses_people_result_context")
    if int(action_contract.get("people_target_count") or 0) > 1:
        reasons.append("multiple_people_targets")
    if action_contract.get("execution_mode") in {"bot_notify", "user_delegated_send"}:
        reasons.append("message_delivery")
    if not str(entities.get("delivery_mode") or "").strip() and intent.intent == "message_send":
        reasons.append("delivery_mode_unresolved")
    return reasons


def _route_path(entities: dict[str, Any]) -> str:
    trace = entities.get("command_intent_trace") if isinstance(entities.get("command_intent_trace"), dict) else {}
    if trace.get("source") == "llm":
        return str(trace.get("mode") or "llm")
    if trace.get("source") == "candidate_arbiter":
        return "candidate_arbiter"
    frame = entities.get("command_frame") if isinstance(entities.get("command_frame"), dict) else {}
    if str(frame.get("route_path") or "").startswith("explicit_command"):
        return str(frame.get("route_path"))
    if entities.get("foundation_route"):
        return "foundation_rule"
    return "rule"


def _route_reason(*, entities: dict[str, Any], route_path: str) -> str:
    frame = entities.get("command_frame") if isinstance(entities.get("command_frame"), dict) else {}
    if frame.get("route_reason"):
        return str(frame.get("route_reason"))
    trace = entities.get("command_intent_trace") if isinstance(entities.get("command_intent_trace"), dict) else {}
    if trace.get("reason"):
        return str(trace.get("reason"))
    if entities.get("foundation_route"):
        return f"foundation_route:{entities.get('foundation_route')}"
    return route_path


def _domain_for_intent(intent: str) -> str:
    prefix = str(intent or "").split("_", 1)[0]
    return _DOMAIN_BY_INTENT_PREFIX.get(prefix, "")


def _domain_gate(*, intent: IntentResult, entities: dict[str, Any], domain: str, route_path: str) -> dict[str, Any]:
    return {
        "domain": domain,
        "reason": _domain_reason(intent=intent, entities=entities),
        "source": _domain_source(entities=entities, route_path=route_path),
        "confidence": intent.confidence,
    }


def _domain_reason(*, intent: IntentResult, entities: dict[str, Any]) -> str:
    if entities.get("explicit_command"):
        family = entities.get("explicit_command") if isinstance(entities.get("explicit_command"), dict) else {}
        return f"explicit_command:{family.get('family') or 'system'}"
    foundation_route = str(entities.get("foundation_route") or "").strip()
    if foundation_route:
        return f"foundation_route:{foundation_route}"
    knowledge_context = str(entities.get("knowledge_context") or "").strip()
    if intent.intent == "general_query" and knowledge_context:
        return f"knowledge_context:{knowledge_context}"
    if intent.intent == "smalltalk":
        return "conversation_boundary"
    if intent.intent.startswith("task_") or intent.intent.startswith("calendar_"):
        return "workspace_signal"
    if intent.intent.startswith("approval_"):
        return "process_signal"
    if intent.intent in {"people_lookup", "department_members", "organization_snapshot"}:
        return "people_signal"
    if intent.intent.startswith("mail_") or intent.intent.startswith("message_") or intent.intent.startswith("chat_"):
        return "communication_signal"
    if intent.intent in {"runtime_status", "action_trace", "governance_view"}:
        return "system_signal"
    if intent.intent == "external_information_query":
        return "external_information_signal"
    return "intent_prefix"


def _domain_source(*, entities: dict[str, Any], route_path: str) -> str:
    if entities.get("explicit_command"):
        return "explicit_command"
    if entities.get("foundation_route"):
        return "foundation_rule"
    trace = entities.get("command_intent_trace") if isinstance(entities.get("command_intent_trace"), dict) else {}
    if trace.get("source") == "llm":
        return "llm_candidate"
    return route_path or "rule"


def _scope_gate(*, context: RuntimeContext, intent: IntentResult, entities: dict[str, Any], route_path: str) -> dict[str, Any]:
    resolved_scope = _resolved_scope(intent.data_scope)
    target = _scope_target(context=context, entities=entities, resolved_scope=resolved_scope)
    return {
        "scope": resolved_scope,
        "requested_scope": intent.data_scope,
        "resolved_scope": resolved_scope,
        "reason": _scope_reason(intent=intent, entities=entities, resolved_scope=resolved_scope),
        "source": _scope_source(entities=entities, route_path=route_path),
        "resource_boundary": _resource_boundary(intent=intent, entities=entities, resolved_scope=resolved_scope),
        "target": target,
    }


def _resolved_scope(data_scope: str) -> str:
    normalized = str(data_scope or "").strip().lower()
    if normalized in {"self", "person", "department", "company", "organization", "external"}:
        return normalized
    return "self"


def _scope_reason(*, intent: IntentResult, entities: dict[str, Any], resolved_scope: str) -> str:
    if entities.get("explicit_command"):
        return "control_plane_scope"
    foundation_route = str(entities.get("foundation_route") or "")
    if foundation_route.startswith("people.department"):
        return "department_resource_signal"
    if foundation_route.startswith("people.aggregate"):
        return "organization_resource_signal"
    if foundation_route.startswith("people.person"):
        return "person_resource_signal"
    if foundation_route.startswith("communication.mail"):
        return "personal_mailbox_signal"
    if foundation_route.startswith("communication.im"):
        return "personal_chat_signal"
    if foundation_route.startswith("knowledge."):
        return "company_knowledge_signal"
    if intent.intent.startswith("task_") or intent.intent.startswith("calendar_"):
        return f"{resolved_scope}_workspace_signal"
    if intent.intent.startswith("approval_"):
        return f"{resolved_scope}_process_signal"
    if resolved_scope == "external":
        return "external_information_signal"
    return f"{resolved_scope}_scope_signal"


def _scope_source(*, entities: dict[str, Any], route_path: str) -> str:
    if entities.get("explicit_command"):
        return "explicit_command"
    if entities.get("foundation_route"):
        return "foundation_rule"
    trace = entities.get("command_intent_trace") if isinstance(entities.get("command_intent_trace"), dict) else {}
    if trace.get("source") == "llm":
        return "llm_candidate"
    return route_path or "rule"


def _resource_boundary(*, intent: IntentResult, entities: dict[str, Any], resolved_scope: str) -> str:
    foundation_route = str(entities.get("foundation_route") or "")
    if foundation_route.startswith("communication.mail"):
        return "personal_mailbox"
    if foundation_route.startswith("communication.im"):
        return "personal_chat_membership"
    if foundation_route.startswith("knowledge."):
        return "enterprise_knowledge"
    if intent.intent in {"people_lookup", "department_members", "organization_snapshot", "organization_export"}:
        return "enterprise_directory"
    if resolved_scope == "self":
        return "personal_workspace"
    if resolved_scope in {"person", "department", "company", "organization"}:
        return "enterprise_workspace"
    if resolved_scope == "external":
        return "public_information"
    return "workspace"


def _scope_target(*, context: RuntimeContext, entities: dict[str, Any], resolved_scope: str) -> dict[str, Any]:
    if resolved_scope == "self":
        return {
            "type": "self",
            "open_id": context.identity.open_id,
            "user_id": context.identity.user_id,
        }
    if resolved_scope == "person":
        return {
            "type": "person",
            "user_id": str(entities.get("target_user_id") or entities.get("user_id") or ""),
            "open_id": str(entities.get("target_open_id") or entities.get("open_id") or ""),
            "keyword": str(entities.get("keyword") or entities.get("target") or ""),
        }
    if resolved_scope == "department":
        return {
            "type": "department",
            "department_id": str(
                entities.get("target_department_id")
                or entities.get("department_id")
                or context.runtime_scope.active_department_id
                or ""
            ),
            "keyword": str(entities.get("keyword") or ""),
        }
    if resolved_scope in {"company", "organization"}:
        return {
            "type": resolved_scope,
            "company_id": str(context.runtime_scope.active_company_id or ""),
        }
    if resolved_scope == "external":
        return {"type": "external"}
    return {"type": resolved_scope}


def _capability_for_intent(intent: str) -> str:
    if not intent:
        return ""
    parts = intent.split("_")
    return "_".join(parts[:2]) if len(parts) >= 2 else intent


def _target_summary(entities: dict[str, Any]) -> dict[str, Any]:
    keys = {
        "approval_id",
        "task_id",
        "calendar_id",
        "department_id",
        "target_user_id",
        "target_department_id",
        "target_type",
        "target",
        "keyword",
    }
    summary = {key: value for key, value in entities.items() if key in keys and isinstance(value, (str, int, float, bool))}
    if "people_target_count" in entities:
        summary["people_target_count"] = entities.get("people_target_count")
    return summary
