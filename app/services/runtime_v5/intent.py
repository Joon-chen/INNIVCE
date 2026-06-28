from __future__ import annotations

from datetime import datetime, timedelta
import re
from typing import Any
from zoneinfo import ZoneInfo

from app.services.runtime_v5.explicit_command import explicit_command_intent
from app.services.runtime_v5.domain_query import intent_with_domain_query
from app.services.runtime_v5.intent_candidates import IntentCandidate, arbitrate_intent_candidates
from app.services.runtime_v5.interaction_intent import classify_interaction_intent
from app.services.runtime_v5.llm_intent import llm_command_intent
from app.services.runtime_v5.models import IntentResult, RuntimeContext
from app.services.runtime_v5.people_resolver import people_targets_from_result_context, references_people_context
from app.services.runtime_v5.response_classification import classify_response_request


_APP_TOKEN_PATTERN = re.compile(r"\b(bascn[-A-Za-z0-9_]+)\b")
_DOC_TOKEN_PATTERN = re.compile(r"\b((?:doccn|doxcn|docxcn)[-A-Za-z0-9_]+)\b")
_WIKI_SPACE_PATTERN = re.compile(r"\b(wksp[-A-Za-z0-9_]+)\b")
_SLIDES_URL_TOKEN_PATTERN = re.compile(r"/slides/([A-Za-z0-9_-]+)")
_SLIDES_TOKEN_PATTERN = re.compile(r"\b(slides[A-Za-z0-9_-]{8,})\b")
_WHITEBOARD_TOKEN_PATTERN = re.compile(r"\b(wbcn[A-Za-z0-9_-]+)\b")
_TASK_GUID_PATTERN = re.compile(r"\b([A-Za-z0-9_-]{8,})\b")
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_LOCAL_TZ = ZoneInfo("Asia/Shanghai")
_APPROVAL_CURRENT_KEY = "runtime_v5_current_approval_item"
_LLM_TRIAGE_INTENTS = {
    "smalltalk",
    "people_lookup",
    "message_send",
    "external_information_query",
    "general_query",
    "general_analysis",
    "decision_advice",
    "risk_analysis",
}


def recognize_intent(question: str, context: RuntimeContext) -> IntentResult:
    explicit_intent = explicit_command_intent(question, context)
    if explicit_intent is not None:
        return explicit_intent
    conversation_first = _conversation_first_primary_intent(question=question, context=context)
    if conversation_first is not None:
        return intent_with_domain_query(conversation_first, context)
    rule_intent = _recognize_intent_by_rules(question, context)
    force_llm = _requires_llm_command_triage(question=question, rule_intent=rule_intent)
    llm_intent = None
    if force_llm or _allows_llm_command_fallback(question=question, rule_intent=rule_intent):
        llm_intent = llm_command_intent(question=question, context=context, rule_intent=rule_intent, force=force_llm)
    if llm_intent is not None:
        return intent_with_domain_query(llm_intent, context)
    if force_llm and _should_degrade_on_llm_miss(rule_intent):
        return intent_with_domain_query(_degraded_command_triage_intent(question=question, rule_intent=rule_intent), context)
    return intent_with_domain_query(rule_intent, context)


def _conversation_first_primary_intent(*, question: str, context: RuntimeContext) -> IntentResult | None:
    if question.strip() != str(context.current_message or "").strip():
        return None
    if _reserved_non_v1_or_action_surface(question=question, context=context):
        return None
    try:
        from app.services.runtime_v5.dialogue_resolver import build_conversation_first_frame, conversation_first_intent_result
    except Exception:
        return None
    frame = build_conversation_first_frame(context)
    if frame.domain not in {"People", "Knowledge", "Communication"}:
        return None
    if frame.domain == "Communication" and frame.intent != "message_send":
        return None
    if frame.question_type == "action" and frame.intent != "message_send":
        return None
    if frame.intent == "smalltalk":
        return None
    if frame.needs_clarification or frame.missing_slots:
        return None
    if frame.confidence < 0.72:
        return None
    return conversation_first_intent_result(context=context, frame=frame)


def _reserved_non_v1_or_action_surface(*, question: str, context: RuntimeContext) -> bool:
    """Keep V1 limited to read-only People/Knowledge while other domains migrate."""

    compact = re.sub(r"\s+", "", str(question or "").lower())
    if not compact:
        return False
    if context.result_context is not None:
        metadata = context.result_context.metadata if isinstance(context.result_context.metadata, dict) else {}
        if metadata.get("execution_status") == "clarification":
            return True
    if _is_im_send(compact):
        return False
    if _reserved_operational_surface(compact):
        return True
    if _has_any(compact, ("拉群", "建群", "群发")):
        return True
    if _has_any(compact, ("群", "群聊")) and not _has_any(compact, ("发给", "发到", "发送给", "发消息", "发送消息")):
        return True
    if context.session_context and any(str(key).startswith("runtime_v5_pending") for key in context.session_context):
        return False
    return False


def _reserved_operational_surface(compact: str) -> bool:
    if _has_any(compact, ("写封邮件", "写邮件", "发邮件", "邮件")) and _has_any(compact, ("给", "发", "写", "主题", "正文")):
        return True
    if _has_any(compact, ("邮件", "邮箱", "收件箱")):
        return True
    return _has_any(compact, ("任务", "待办", "审批", "日程", "会议", "开会", "安排"))


def _requires_llm_command_triage(*, question: str, rule_intent: IntentResult) -> bool:
    text = str(question or "").strip()
    if not text:
        return False
    if text.startswith("/"):
        return False
    if rule_intent.intent == "smalltalk" and not _smalltalk_needs_command_llm(text):
        return False
    if rule_intent.entities.get("foundation_route") and rule_intent.confidence >= 0.84 and not rule_intent.missing_params:
        return False
    if rule_intent.intent in _DETERMINISTIC_RULE_LOCK_INTENTS and rule_intent.confidence >= 0.8 and not rule_intent.missing_params:
        return False
    if rule_intent.question_type == "action" and rule_intent.confidence >= 0.75:
        return False
    if (
        rule_intent.intent not in _LLM_TRIAGE_INTENTS
        and not rule_intent.missing_params
        and rule_intent.confidence >= 0.8
        and rule_intent.data_scope == "self"
    ):
        return False
    return True


_DETERMINISTIC_RULE_LOCK_INTENTS = {
    "company_intro",
    "external_information_query",
}


def _allows_llm_command_fallback(*, question: str, rule_intent: IntentResult) -> bool:
    text = str(question or "").strip()
    if not text or text.startswith("/"):
        return False
    if rule_intent.intent in _DETERMINISTIC_RULE_LOCK_INTENTS and rule_intent.confidence >= 0.8 and not rule_intent.missing_params:
        return False
    if rule_intent.question_type == "action" and rule_intent.confidence >= 0.75:
        return False
    if rule_intent.intent == "smalltalk" and not _smalltalk_needs_command_llm(text):
        return False
    return True


def _smalltalk_needs_command_llm(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return False
    if len(compact) <= 8 and any(token in compact for token in ("这个", "那个", "这些", "那些", "刚才", "上面", "继续")):
        return True
    return any(token in compact for token in ("之前告诉过你", "刚才告诉过你", "不是告诉你"))


def _should_degrade_on_llm_miss(rule_intent: IntentResult) -> bool:
    return rule_intent.intent in {
        "people_lookup",
        "message_send",
    } or bool(rule_intent.missing_params)


def _degraded_command_triage_intent(*, question: str, rule_intent: IntentResult) -> IntentResult:
    if rule_intent.intent == "general_analysis" and _has_any(question, ("慢", "耗时", "卡", "延迟", "为什么这么慢")):
        intent = "runtime_status"
        question_type = "query"
    else:
        intent = "smalltalk"
        question_type = "query"
    return IntentResult(
        question_type=question_type,  # type: ignore[arg-type]
        intent=intent,
        data_scope="self",
        entities={
            "fallback_answer": _degraded_command_triage_answer(rule_intent),
            "command_frame": {
                "utterance_type": "system_explanation" if intent == "runtime_status" else "conversation",
                "intent": intent,
                "question_type": question_type,
                "scope": "self",
                "confidence": 0.5,
                "needs_clarification": True,
                "route_path": "llm_first_degraded",
                "route_reason": "command_llm_unavailable_or_timeout",
                "rule_candidate": {
                    "intent": rule_intent.intent,
                    "question_type": rule_intent.question_type,
                    "scope": rule_intent.data_scope,
                    "confidence": rule_intent.confidence,
                },
            },
            "command_intent_trace": {
                "source": "degraded",
                "mode": "llm_first_degraded",
                "rule_intent": rule_intent.intent,
                "final_intent": intent,
                "reason": "command_llm_unavailable_or_timeout",
            },
        },
        missing_params=(),
        confidence=0.5,
        canonical_question=question,
    )


def _degraded_command_triage_answer(rule_intent: IntentResult) -> str:
    if rule_intent.intent == "people_lookup":
        return "这句话可能是在问企业上下文，也可能是在查具体人员。为了避免误查通讯录，我先不调用人员查询；请直接说要查哪个人，或换成更明确的问题。"
    if rule_intent.intent == "message_send":
        return "这句话可能是在说上下文，不一定是要发送消息。为了避免误发，我先不执行发送；如果要发消息，请明确说“发给谁”和“发什么”。"
    if rule_intent.intent == "external_information_query":
        return "这类问题需要外部实时信息能力；当前还没有接入实时联网查询，所以我不能可靠回答。"
    if rule_intent.question_type == "action":
        return "这句话像是要执行动作，但我还没确认清楚要改什么、发给谁或作用在哪个对象上，所以先不动真实数据。你可以直接把动作、对象和内容连在一起说。"
    if rule_intent.data_scope in {"company", "department", "person", "organization"}:
        return "我还没把你要看的范围和对象对准，所以先不查业务数据。你可以直接说“查我的/部门/公司”的哪类信息，或者点名对象。"
    return "我在。你可以继续自然说，我会根据上下文判断；如果是要查数据或执行动作，把对象和范围带上会更准。"


def _recognize_intent_by_rules(question: str, context: RuntimeContext) -> IntentResult:
    current_message = context.current_message or ""
    text = question if question.strip() == current_message.strip() else f"{question} {current_message}"
    text = text.strip().lower()

    if _is_external_information_followup(text, context):
        return IntentResult(
            question_type="query",
            intent="external_information_query",
            data_scope="external",
            entities=_external_information_entities(question, context),
            missing_params=(),
            confidence=0.78,
            canonical_question=question,
        )

    contextual_intent = _contextual_followup_intent(question=question, text=text, context=context)
    if contextual_intent is not None:
        return contextual_intent

    candidate_intent = _arbitrated_command_intent(question=question, text=text, context=context)
    if candidate_intent is not None:
        return candidate_intent

    if _is_organization_export(text):
        app_token = _extract_app_token(text)
        entities = {"app_token": app_token, "target": "existing_base"} if app_token else {"target": "new_base"}
        entities.update(_result_delivery_target_params(question, context))
        return IntentResult(
            question_type="action",
            intent="organization_export",
            data_scope="organization",
            entities=entities,
            missing_params=(),
            confidence=0.92,
            canonical_question=question,
        )

    embedded_people_intent = _embedded_people_lookup_intent(question=question, text=text)
    if embedded_people_intent is not None:
        return embedded_people_intent

    if _is_non_work_conversation(text):
        return IntentResult(
            question_type="query",
            intent="smalltalk",
            data_scope="self",
            entities={"fallback_answer": _non_work_conversation_answer(text)},
            missing_params=(),
            confidence=0.9,
            canonical_question=question,
        )

    ambiguous_operation_answer = _ambiguous_operation_conversation_answer(text, context)
    if ambiguous_operation_answer:
        return IntentResult(
            question_type="query",
            intent="smalltalk",
            data_scope="self",
            entities={"fallback_answer": ambiguous_operation_answer},
            missing_params=(),
            confidence=0.86,
            canonical_question=question,
        )

    foundation_intent = _foundation_data_source_intent(question=question, text=text)
    if foundation_intent is not None:
        return foundation_intent

    if _is_smalltalk(text):
        return IntentResult(
            question_type="query",
            intent="smalltalk",
            data_scope="self",
            missing_params=(),
            confidence=0.95,
            canonical_question=question,
        )

    if _is_action_trace_query(text):
        return IntentResult(
            question_type="query",
            intent="action_trace",
            data_scope="self",
            missing_params=(),
            confidence=0.9,
            canonical_question=question,
        )

    if _is_governance_view_query(text):
        return IntentResult(
            question_type="query",
            intent="governance_view",
            data_scope="self",
            missing_params=(),
            confidence=0.9,
            canonical_question=question,
        )

    if _is_runtime_status_query(text):
        return IntentResult(
            question_type="query",
            intent="runtime_status",
            data_scope="self",
            missing_params=(),
            confidence=0.9,
            canonical_question=question,
        )

    if _is_external_information_query(text):
        return IntentResult(
            question_type="query",
            intent="external_information_query",
            data_scope="external",
            entities=_external_information_entities(question, context),
            missing_params=(),
            confidence=0.82,
            canonical_question=question,
        )
    if _is_docs_read(text):
        document_id = _extract_doc_token(question)
        return IntentResult(
            question_type="query",
            intent="docs_read",
            data_scope="company",
            entities={"document_id": document_id} if document_id else {},
            missing_params=() if document_id else ("document_id",),
            confidence=0.88 if document_id else 0.65,
            canonical_question=question,
        )

    if _is_wiki_search(text):
        space_id = _extract_wiki_space_id(question)
        return IntentResult(
            question_type="query",
            intent="wiki_search",
            data_scope="company",
            entities={"space_id": space_id} if space_id else {},
            missing_params=(),
            confidence=0.86,
            canonical_question=question,
        )

    if _is_drive_list(text):
        return IntentResult(
            question_type="query",
            intent="drive_list",
            data_scope="company",
            entities=_drive_list_params(question),
            missing_params=(),
            confidence=0.84,
            canonical_question=question,
        )

    if _is_slides_read(text):
        slides_token = _extract_slides_token(question)
        return IntentResult(
            question_type="query",
            intent="slides_read",
            data_scope="company",
            entities={"xml_presentation_id": slides_token} if slides_token else {},
            missing_params=() if slides_token else ("xml_presentation_id",),
            confidence=0.86 if slides_token else 0.64,
            canonical_question=question,
        )

    if _is_whiteboard_read(text):
        whiteboard_token = _extract_whiteboard_token(question)
        return IntentResult(
            question_type="query",
            intent="whiteboard_read",
            data_scope="company",
            entities={"whiteboard_token": whiteboard_token} if whiteboard_token else {},
            missing_params=() if whiteboard_token else ("whiteboard_token",),
            confidence=0.86 if whiteboard_token else 0.64,
            canonical_question=question,
        )

    if _is_vc_meeting_search(text):
        return IntentResult(
            question_type="query",
            intent="vc_meeting_search",
            data_scope="company",
            entities={"page_size": 20},
            missing_params=(),
            confidence=0.84,
            canonical_question=question,
        )

    if _is_attendance_query(text):
        return IntentResult(
            question_type="query",
            intent="attendance_query",
            data_scope="self",
            entities={},
            missing_params=(),
            confidence=0.84,
            canonical_question=question,
        )

    if _is_okr_query(text):
        return IntentResult(
            question_type="query",
            intent="okr_query",
            data_scope="self",
            entities={},
            missing_params=(),
            confidence=0.84,
            canonical_question=question,
        )

    if _is_organization_export(text):
        app_token = _extract_app_token(text)
        entities = {"app_token": app_token, "target": "existing_base"} if app_token else {"target": "new_base"}
        entities.update(_result_delivery_target_params(question, context))
        return IntentResult(
            question_type="action",
            intent="organization_export",
            data_scope="organization",
            entities=entities,
            missing_params=(),
            confidence=0.92,
            canonical_question=question,
        )

    if _is_approval_transfer(text, context):
        params = _approval_ref(question, context)
        entities = {**params, "comment": _approval_comment(question, default="转交处理")}
        target = _approval_target_keyword(question)
        if target:
            entities["target_keyword"] = target
        missing = [] if params.get("item") else ["approval_item"]
        missing.extend([] if entities.get("transfer_user_id") or entities.get("target_keyword") else ["transfer_user_id"])
        return IntentResult(
            question_type="action",
            intent="approval_transfer",
            data_scope="self",
            entities=entities,
            missing_params=tuple(missing),
            confidence=0.86 if params.get("item") else 0.62,
            canonical_question=question,
        )

    if _is_approval_add_sign(text, context):
        params = _approval_ref(question, context)
        entities = {**params, "comment": _approval_comment(question, default="请协助审批")}
        target = _approval_target_keyword(question)
        if target:
            entities["target_keyword"] = target
        missing = [] if params.get("item") else ["approval_item"]
        missing.extend([] if entities.get("add_sign_user_ids") or entities.get("target_keyword") else ["add_sign_user_ids"])
        return IntentResult(
            question_type="action",
            intent="approval_add_sign",
            data_scope="self",
            entities=entities,
            missing_params=tuple(missing),
            confidence=0.86 if params.get("item") else 0.62,
            canonical_question=question,
        )

    if _is_approval_rollback(text, context):
        params = _approval_ref(question, context)
        missing = [] if params.get("item") else ["approval_item"]
        missing.extend([] if params.get("node_ids") else ["node_ids"])
        return IntentResult(
            question_type="action",
            intent="approval_rollback",
            data_scope="self",
            entities={**params, "comment": _approval_comment(question, default="退回补充")},
            missing_params=tuple(missing),
            confidence=0.82 if params.get("item") else 0.62,
            canonical_question=question,
        )

    if _is_approval_remind(text, context):
        params = _approval_ref(question, context)
        return IntentResult(
            question_type="action",
            intent="approval_remind",
            data_scope="self",
            entities={**params, "comment": _approval_comment(question, default="请尽快处理")},
            missing_params=() if params.get("item") else ("approval_item",),
            confidence=0.84 if params.get("item") else 0.62,
            canonical_question=question,
        )

    if _is_approval_cancel(text, context):
        params = _approval_ref(question, context)
        return IntentResult(
            question_type="action",
            intent="approval_cancel",
            data_scope="self",
            entities={**params, "comment": _approval_comment(question, default="撤回审批")},
            missing_params=() if params.get("item") else ("approval_item",),
            confidence=0.84 if params.get("item") else 0.62,
            canonical_question=question,
        )

    if _is_approval_cc(text, context):
        params = _approval_ref(question, context)
        entities = {**params, "comment": _approval_comment(question, default="抄送知会")}
        target = _approval_target_keyword(question)
        if target:
            entities["target_keyword"] = target
        missing = [] if params.get("item") else ["approval_item"]
        missing.extend([] if entities.get("cc_user_ids") or entities.get("target_keyword") else ["cc_user_ids"])
        return IntentResult(
            question_type="action",
            intent="approval_cc",
            data_scope="self",
            entities=entities,
            missing_params=tuple(missing),
            confidence=0.84 if params.get("item") else 0.62,
            canonical_question=question,
        )

    if _is_approval_initiated(text):
        return IntentResult(
            question_type="query",
            intent="approval_initiated",
            data_scope="self",
            entities={"page_size": 20},
            missing_params=(),
            confidence=0.84,
            canonical_question=question,
        )

    if _is_approval_approve(text, context):
        params = _approval_ref(question, context)
        return IntentResult(
            question_type="action",
            intent="approval_approve",
            data_scope="self",
            entities={**params, "comment": _approval_comment(question, default="同意")},
            missing_params=() if params.get("item") else ("approval_item",),
            confidence=0.9 if params.get("item") else 0.62,
            canonical_question=question,
        )

    if _is_approval_reject(text, context):
        params = _approval_ref(question, context)
        return IntentResult(
            question_type="action",
            intent="approval_reject",
            data_scope="self",
            entities={**params, "comment": _approval_comment(question, default="拒绝")},
            missing_params=() if params.get("item") else ("approval_item",),
            confidence=0.9 if params.get("item") else 0.62,
            canonical_question=question,
        )

    if _is_approval_detail(text, context):
        params = _approval_ref(question, context)
        return IntentResult(
            question_type="query",
            intent="approval_detail",
            data_scope="self",
            entities=params,
            missing_params=() if params.get("item") else ("approval_item",),
            confidence=0.86 if params.get("item") else 0.62,
            canonical_question=question,
        )

    if _is_approval_query(text):
        return IntentResult(
            question_type="query",
            intent="approval_query",
            data_scope="self" if _has_any(text, ("我", "我的", "待我", "需要我")) else "company",
            confidence=0.9,
            canonical_question=question,
        )

    if _is_task_create(text):
        people_targets = people_targets_from_result_context(context.result_context)
        member_params = _people_context_task_member_params(question, people_targets)
        return IntentResult(
            question_type="action",
            intent="task_create",
            data_scope="self",
            entities={"summary": _task_summary(question), **member_params},
            missing_params=(),
            confidence=0.86,
            canonical_question=question,
        )

    if _is_calendar_create(text):
        time_params = _calendar_time_params(question)
        people_targets = people_targets_from_result_context(context.result_context)
        attendee_params = _people_context_attendee_params(question, people_targets)
        return IntentResult(
            question_type="action",
            intent="calendar_create",
            data_scope="self",
            entities={"summary": _calendar_summary(question), **time_params, **attendee_params},
            missing_params=tuple(key for key in ("start", "end") if not time_params.get(key)),
            confidence=0.84,
            canonical_question=question,
        )

    if _is_task_complete(text):
        task_ref = _task_ref(question, context)
        return IntentResult(
            question_type="action",
            intent="task_complete",
            data_scope="self",
            entities=task_ref,
            missing_params=() if task_ref.get("task_guid") else ("task_guid",),
            confidence=0.88 if task_ref.get("task_guid") else 0.62,
            canonical_question=question,
        )

    if _is_task_search(text):
        return IntentResult(
            question_type="query",
            intent="task_search",
            data_scope="self",
            entities={"keyword": _task_search_keyword(question)},
            missing_params=(),
            confidence=0.84,
            canonical_question=question,
        )

    if _is_department_members_query(text):
        return IntentResult(
            question_type="query",
            intent="department_members",
            data_scope="department",
            entities={"keyword": _department_keyword(question), "foundation_route": "people.department_members"},
            missing_params=(),
            confidence=0.86,
            canonical_question=question,
        )

    if _is_task_query(text):
        return IntentResult(
            question_type="query",
            intent="task_query",
            data_scope=_query_data_scope(text),
            entities={"page_size": 20},
            missing_params=(),
            confidence=0.84,
            canonical_question=question,
        )

    if _is_calendar_query(text):
        return IntentResult(
            question_type="query",
            intent="calendar_query",
            data_scope=_query_data_scope(text),
            entities=_calendar_query_params(question),
            missing_params=(),
            confidence=0.84,
            canonical_question=question,
        )

    if _is_mail_draft_create(text):
        draft_params = _mail_draft_params(question, context)
        return IntentResult(
            question_type="action",
            intent="mail_draft_create",
            data_scope="self",
            entities=draft_params,
            missing_params=tuple(key for key in ("to", "subject", "body") if not draft_params.get(key)),
            confidence=0.86,
            canonical_question=question,
        )

    if _is_mail_search(text):
        return IntentResult(
            question_type="query",
            intent="mail_search",
            data_scope="self",
            entities={"query": _mail_search_keyword(question), "page_size": 20},
            missing_params=(),
            confidence=0.84,
            canonical_question=question,
        )

    if _is_mail_query(text):
        return IntentResult(
            question_type="query",
            intent="mail_query",
            data_scope="self",
            entities={"page_size": 20},
            missing_params=(),
            confidence=0.84,
            canonical_question=question,
        )

    if _is_im_send(text):
        send_params = _im_send_params(question, context)
        missing = [key for key in ("target_type", "text") if not send_params.get(key)]
        if send_params.get("target_type") == "people_context" and not send_params.get("delivery_mode"):
            missing.append("delivery_mode")
        return IntentResult(
            question_type="action",
            intent="message_send",
            data_scope="self",
            entities=send_params,
            missing_params=tuple(missing),
            confidence=0.88,
            canonical_question=question,
        )

    if _is_im_chat_search(text):
        return IntentResult(
            question_type="query",
            intent="chat_search",
            data_scope="self",
            entities={"query": _im_chat_query(question), "page_size": 20},
            missing_params=(),
            confidence=0.84,
            canonical_question=question,
        )

    if _is_im_message_query(text):
        params = _im_message_query_params(question)
        return IntentResult(
            question_type="query",
            intent="message_query",
            data_scope="self",
            entities=params,
            missing_params=tuple(key for key in ("chat_id",) if not params.get(key)),
            confidence=0.8,
            canonical_question=question,
        )

    if _is_people_aggregate_query(text):
        return IntentResult(
            question_type="query",
            intent="organization_snapshot",
            data_scope="organization",
            entities={
                "view": "people_aggregate",
                "query": question.strip(),
                "people_query_mode": _people_query_mode(text),
                "foundation_route": "people.aggregate",
            },
            missing_params=(),
            confidence=0.88,
            canonical_question=question,
        )

    if _has_any(text, ("组织架构", "组织结构", "通讯录")):
        if _is_people_aggregate_query(text):
            return IntentResult(
                question_type="query",
                intent="organization_snapshot",
                data_scope="organization",
                entities={
                    "view": "people_aggregate",
                    "query": question.strip(),
                    "people_query_mode": _people_query_mode(text),
                    "foundation_route": "people.aggregate",
                },
                confidence=0.88,
                canonical_question=question,
            )
        return IntentResult(
            question_type="query",
            intent="organization_snapshot",
            data_scope="organization",
            entities={"view": "organization_snapshot"},
            confidence=0.88,
            canonical_question=question,
        )

    if _is_people_lookup(text):
        return IntentResult(
            question_type="query",
            intent="people_lookup",
            data_scope="person",
            entities={"keyword": _people_keyword(question), "people_query_field": _people_query_field(question)},
            confidence=0.86,
            canonical_question=question,
        )

    if _has_any(text, ("风险", "异常", "预警", "隐患")):
        return IntentResult(
            question_type="insight",
            intent="risk_analysis",
            data_scope="company",
            confidence=0.82,
            canonical_question=question,
        )

    if _is_decision_question(text):
        return IntentResult(
            question_type="decision",
            intent="decision_advice",
            data_scope="company",
            entities={"query": question.strip()},
            confidence=0.78,
            canonical_question=question,
        )

    if _is_analysis_question(text):
        return IntentResult(
            question_type="analysis",
            intent="general_analysis",
            data_scope="company",
            entities={"query": question.strip()},
            confidence=0.78,
            canonical_question=question,
        )

    if _is_company_intro_query(text):
        return IntentResult(
            question_type="query",
            intent="general_query",
            data_scope="company",
            entities={"query": question.strip(), "knowledge_context": "company_profile"},
            confidence=0.82,
            canonical_question=question,
        )

    return IntentResult(
        question_type="query",
        intent="general_query",
        data_scope="company",
        confidence=0.55,
        canonical_question=question,
    )


def _is_company_intro_query(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "").lower())
    if not compact:
        return False
    if _has_any(
        compact,
        (
            "公司介绍",
            "公司情况",
            "主营业务",
            "主要业务",
            "公司主营",
            "业务范围",
            "公司业务",
            "做什么业务",
            "是什么公司",
        ),
    ):
        return True
    if "公司" in compact and _has_any(
        compact,
        (
            "做什么",
            "干什么",
            "业务是什么",
            "业务有哪些",
            "靠什么赚钱",
            "收入来源",
        ),
    ):
        return True
    return False


def _foundation_data_source_intent(*, question: str, text: str) -> IntentResult | None:
    if _is_department_members_query(text):
        return IntentResult(
            question_type="query",
            intent="department_members",
            data_scope="department",
            entities={"keyword": _department_keyword(question), "foundation_route": "people.department_members"},
            missing_params=(),
            confidence=0.86,
            canonical_question=question,
        )
    if _is_people_aggregate_query(text):
        return IntentResult(
            question_type="query",
            intent="organization_snapshot",
            data_scope="organization",
            entities={
                "view": "people_aggregate",
                "query": question.strip(),
                "people_query_mode": _people_query_mode(text),
                "foundation_route": "people.aggregate",
            },
            missing_params=(),
            confidence=0.88,
            canonical_question=question,
        )
    if _is_named_person_lookup_query(question=question, text=text):
        return IntentResult(
            question_type="query",
            intent="people_lookup",
            data_scope="person",
            entities={"keyword": _people_keyword(question), "people_query_field": _people_query_field(question), "foundation_route": "people.person"},
            missing_params=(),
            confidence=0.86,
            canonical_question=question,
        )
    if _is_foundation_mail_query(text):
        return IntentResult(
            question_type="query",
            intent="mail_query",
            data_scope="self",
            entities={"page_size": 20, "view": _foundation_query_view(text), "foundation_route": "communication.mail"},
            missing_params=(),
            confidence=0.84,
            canonical_question=question,
        )
    if _is_foundation_chat_query(text):
        view = _foundation_query_view(text)
        return IntentResult(
            question_type="query",
            intent="chat_search",
            data_scope="self",
            entities={"query": _im_chat_query(question) if view == "search" else "", "page_size": 20, "view": view, "foundation_route": "communication.im.chat"},
            missing_params=(),
            confidence=0.84,
            canonical_question=question,
        )
    if _is_company_intro_query(text):
        return IntentResult(
            question_type="query",
            intent="general_query",
            data_scope="company",
            entities={"query": question.strip(), "knowledge_context": "company_profile", "foundation_route": "knowledge.company_profile"},
            missing_params=(),
            confidence=0.82,
            canonical_question=question,
        )
    if _is_foundation_knowledge_query(text):
        return IntentResult(
            question_type="query",
            intent="general_query",
            data_scope="company",
            entities={"query": question.strip(), "knowledge_context": "general", "foundation_route": "knowledge.general"},
            missing_params=(),
            confidence=0.84,
            canonical_question=question,
        )
    return None


def _arbitrated_command_intent(*, question: str, text: str, context: RuntimeContext) -> IntentResult | None:
    candidates = _intent_candidates(question=question, text=text, context=context)
    if not candidates:
        return None
    if all(candidate.domain == "Conversation" for candidate in candidates):
        return None
    arbitration = arbitrate_intent_candidates(tuple(candidates))
    return arbitration.selected


def _intent_candidates(*, question: str, text: str, context: RuntimeContext) -> list[IntentCandidate]:
    candidates: list[IntentCandidate] = []
    export_intent = _organization_export_intent(question=question, text=text, context=context)
    if export_intent is not None:
        candidates.append(
            IntentCandidate(
                intent=export_intent,
                domain="People",
                source="rule.organization_export",
                confidence=0.92,
                evidence=("export_signal",),
                route_reason="explicit_export_action",
            )
        )
    candidates.extend(_action_intent_candidates(question=question, text=text, context=context))
    embedded_people_intent = _embedded_people_lookup_intent(question=question, text=text)
    if embedded_people_intent is not None:
        candidates.append(
            IntentCandidate(
                intent=embedded_people_intent,
                domain="People",
                source="rule.embedded_people_lookup",
                confidence=0.9,
                evidence=("named_person", "requested_field"),
                route_reason="exact_people_field_query",
            )
        )
    foundation_intent = _foundation_data_source_intent(question=question, text=text)
    if foundation_intent is not None:
        candidates.append(
            IntentCandidate(
                intent=foundation_intent,
                domain=_candidate_domain(foundation_intent),
                source="rule.foundation_source",
                confidence=foundation_intent.confidence,
                evidence=tuple(filter(None, (str(foundation_intent.entities.get("foundation_route") or ""),))),
                route_reason="foundation_domain_query",
            )
        )
    if _is_non_work_conversation(text):
        candidates.append(
            IntentCandidate(
                intent=IntentResult(
                    question_type="query",
                    intent="smalltalk",
                    data_scope="self",
                    entities={"fallback_answer": _non_work_conversation_answer(text)},
                    missing_params=(),
                    confidence=0.9,
                    canonical_question=question,
                ),
                domain="Conversation",
                source="rule.non_work_conversation",
                confidence=0.9,
                evidence=("conversation_signal",),
                route_reason="non_work_conversation",
            )
        )
    return candidates


def _action_intent_candidates(*, question: str, text: str, context: RuntimeContext) -> list[IntentCandidate]:
    candidates: list[IntentCandidate] = []
    if _is_mail_draft_create(text):
        draft_params = _mail_draft_params(question, context)
        intent = IntentResult(
            question_type="action",
            intent="mail_draft_create",
            data_scope="self",
            entities=draft_params,
            missing_params=tuple(key for key in ("to", "subject", "body") if not draft_params.get(key)),
            confidence=0.86,
            canonical_question=question,
        )
        candidates.append(
            IntentCandidate(
                intent=intent,
                domain="Communication",
                source="rule.mail_draft_action",
                confidence=intent.confidence,
                evidence=("draft_signal",),
                route_reason="communication_draft_action",
            )
        )
    if _is_im_send(text):
        send_params = _im_send_params(question, context)
        missing = [key for key in ("target_type", "text") if not send_params.get(key)]
        if send_params.get("target_type") == "people_context" and not send_params.get("delivery_mode"):
            missing.append("delivery_mode")
        if "target_type" in missing and "text" in missing and not _has_strong_im_send_signal(text):
            return candidates
        intent = IntentResult(
            question_type="action",
            intent="message_send",
            data_scope="self",
            entities=send_params,
            missing_params=tuple(missing),
            confidence=0.88,
            canonical_question=question,
        )
        candidates.append(
            IntentCandidate(
                intent=intent,
                domain="Communication",
                source="rule.message_send_action",
                confidence=intent.confidence,
                evidence=("send_signal",),
                route_reason="communication_send_action",
                missing_slots=tuple(missing),
            )
        )
    return candidates


def _has_strong_im_send_signal(text: str) -> bool:
    return _has_any(
        text,
        (
            "发消息",
            "发送消息",
            "发条信息",
            "发条消息",
            "发个信息",
            "发个消息",
            "发给",
            "发到",
            "发送给",
            "转发给",
        ),
    )


def _organization_export_intent(*, question: str, text: str, context: RuntimeContext) -> IntentResult | None:
    if not _is_organization_export(text):
        return None
    app_token = _extract_app_token(text)
    entities = {"app_token": app_token, "target": "existing_base"} if app_token else {"target": "new_base"}
    entities.update(_result_delivery_target_params(question, context))
    return IntentResult(
        question_type="action",
        intent="organization_export",
        data_scope="organization",
        entities=entities,
        missing_params=(),
        confidence=0.92,
        canonical_question=question,
    )


def _candidate_domain(intent: IntentResult) -> str:
    if intent.intent in {"people_lookup", "department_members", "organization_snapshot", "organization_export"}:
        return "People"
    if intent.intent in {"mail_query", "mail_search", "mail_draft_create", "message_send", "message_query", "chat_search"}:
        return "Communication"
    if intent.intent in {"general_query", "docs_read", "wiki_search", "drive_list"}:
        return "Knowledge"
    if intent.intent in {"task_query", "calendar_query"}:
        return "Workspace"
    if intent.intent == "external_information_query":
        return "External"
    return ""


def _is_named_person_lookup_query(*, question: str, text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "").lower())
    if not compact:
        return False
    if _has_any(compact, ("我是谁", "你是谁", "你知道我是谁", "我是什么", "这个公司", "这家公司", "公司老板", "老板是谁")):
        return False
    if _has_any(compact, ("任务", "待办", "日程", "会议", "审批")):
        return False
    if _has_any(compact, ("邮件", "收件箱")):
        return False
    if "邮箱" in compact and not re.search(r"[\u4e00-\u9fffA-Za-z·.\-]{2,32}的邮箱", compact):
        return False
    identity_signal = _has_any(compact, ("是谁", "谁是"))
    contact_signal = _has_any(compact, ("电话", "号码", "邮箱", "手机号", "职位", "岗位")) or bool(
        re.search(r"(哪个|什么|所属|所在)?部门", compact)
    )
    if not (identity_signal or contact_signal):
        return False
    keyword = _people_keyword(question)
    keyword_compact = re.sub(r"\s+", "", keyword)
    if not keyword_compact:
        return False
    if _has_any(keyword_compact, ("我", "你", "公司", "企业", "组织", "部门", "团队", "老板", "负责人", "这个", "那个")):
        return False
    return bool(re.fullmatch(r"[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z·.\-]{1,31}", keyword_compact))


def _embedded_people_lookup_intent(*, question: str, text: str) -> IntentResult | None:
    keyword, field = _embedded_people_lookup_parts(question)
    if not keyword or not field:
        return None
    return IntentResult(
        question_type="query",
        intent="people_lookup",
        data_scope="person",
        entities={"keyword": keyword, "people_query_field": field, "foundation_route": "people.person"},
        missing_params=(),
        confidence=0.88,
        canonical_question=f"{keyword}的{ {'mobile': '手机号', 'email': '邮箱', 'title': '岗位'}.get(field, '信息') }",
    )


def _is_foundation_mail_query(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "").lower())
    if not _has_any(compact, ("邮件", "邮箱", "收件箱", "email", "mail")):
        return False
    return _has_any(
        compact,
        (
            "多少",
            "几封",
            "几封邮件",
            "数量",
            "列表",
            "有哪些",
            "最近",
            "未读",
            "查看",
            "查询",
            "查一下",
            "搜索",
            "查找",
        ),
    )


def _is_foundation_chat_query(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "").lower())
    if not _has_any(compact, ("群", "群聊", "会话", "聊天")):
        return False
    if _has_any(compact, ("群消息", "聊天记录", "群聊记录")):
        return False
    return _has_any(
        compact,
        (
            "多少",
            "几个",
            "数量",
            "列表",
            "有哪些",
            "现在有",
            "当前有",
            "搜索",
            "查找",
            "找一下",
            "查一下",
        ),
    )


def _is_foundation_knowledge_query(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "").lower())
    if not compact or _is_company_intro_query(compact):
        return False
    if _has_any(compact, ("任务", "待办", "负荷", "延期", "到期")) and not _has_any(compact, ("流程", "制度", "规范", "手册", "模板", "资料", "文档")):
        return False
    knowledge_object = _has_any(compact, ("流程", "制度", "规范", "手册", "模板", "sop", "说明", "指南", "资料", "文档", "知识库", "wiki"))
    knowledge_action = _has_any(compact, ("怎么", "如何", "怎么办", "哪里", "在哪", "查", "看", "有哪些", "是什么", "说明"))
    return knowledge_object and knowledge_action


def _foundation_query_view(text: str) -> str:
    compact = re.sub(r"\s+", "", str(text or "").lower())
    if _has_any(compact, ("多少", "几个", "几封", "数量")):
        return "count"
    if _has_any(compact, ("搜索", "查找", "找一下")):
        return "search"
    return "list"


def _is_organization_export(text: str) -> bool:
    return (
        _has_any(text, ("组织", "组织架构", "组织结构", "通讯录"))
        and _has_any(text, ("表格", "多维表格", "base", "bitable", "表"))
        and _has_any(text, ("创建", "新建", "建表", "建一个", "建张", "做一个", "弄一个"))
        and _has_any(text, ("放入", "写入", "导入", "放进去", "放到", "同步"))
    )


def _is_smalltalk(text: str) -> bool:
    if classify_response_request(question=text, intent="smalltalk", result_type="smalltalk").response_class == "fact":
        return True
    return classify_interaction_intent(text).is_smalltalk


def _is_action_trace_query(text: str) -> bool:
    return _has_any(
        text,
        (
            "刚才执行",
            "上一步执行",
            "执行结果",
            "刚才按钮",
            "刚才操作",
            "上一步操作",
            "处理结果",
            "按钮没反应",
            "点了没反应",
            "没有下文",
            "没下文",
            "刚才怎么了",
            "执行到哪",
            "刚才为什么",
            "为什么没执行",
            "为什么没有执行",
            "为什么这么判断",
            "为什么这么回复",
            "怎么判断的",
            "走了什么策略",
            "用了什么能力",
            "用了哪个能力",
            "刚才走了什么",
        ),
    )


def _is_runtime_status_query(text: str) -> bool:
    return _has_any(
        text,
        (
            "系统诊断",
            "诊断详情",
            "运行状态",
        ),
    )


def _is_external_information_query(text: str) -> bool:
    if not text:
        return False
    if _is_external_capability_conversation(text):
        return False
    if _is_local_public_information_query(text):
        return True
    if _has_any(text, ("天气", "气温", "下雨", "降雨", "空气质量", "台风", "新闻", "热搜", "股价", "汇率", "油价", "航班")):
        return True
    if _has_any(text, ("官网", "官方网站", "网页", "网站", "公开资料", "网上", "搜索一下", "查一下网上", "外部资料")):
        return True
    if _has_any(text, ("今天", "明天", "现在", "最近", "最新")) and _has_any(text, ("政策", "法规", "公告", "市场", "行情", "价格", "新闻")):
        return True
    return False


def _is_local_public_information_query(text: str) -> bool:
    compact = text.replace(" ", "")
    if not _has_any(compact, ("附近", "周边", "附近有", "周围", "这附近")):
        return False
    return _has_any(
        compact,
        (
            "有吗",
            "哪里",
            "哪家",
            "推荐",
            "店",
            "餐厅",
            "吃",
            "喝",
            "外卖",
            "烧烤",
            "火锅",
            "咖啡",
            "酒店",
            "停车",
            "打印",
            "药店",
            "医院",
            "银行",
        ),
    )


def _is_external_capability_conversation(text: str) -> bool:
    return _has_any(text, ("联网", "上网", "外部实时", "实时联网")) and _has_any(
        text,
        (
            "你能",
            "你可以",
            "能不能",
            "可不可以",
            "要不要",
            "想不想",
            "变得",
            "更强",
            "聊天",
            "对话",
            "推理",
            "能力",
            "没能力",
            "没有推理",
        ),
    )


def _is_external_information_followup(text: str, context: RuntimeContext) -> bool:
    metadata = context.result_context.metadata if context.result_context and isinstance(context.result_context.metadata, dict) else {}
    if metadata.get("operation") != "external_information_query" and metadata.get("strategy") != "external_information_query":
        return False
    compact = text.replace(" ", "")
    if not compact:
        return False
    return (
        len(compact) <= 12
        or _has_any(compact, ("今天", "明天", "现在", "最近", "最新", "官网", "网站", "苏州", "北京", "上海", "广州", "深圳"))
    )


def _contextual_followup_intent(*, question: str, text: str, context: RuntimeContext) -> IntentResult | None:
    compact = text.replace(" ", "")
    if _rejects_recent_business_context(compact) or (_is_non_work_conversation(text) and not _embedded_people_lookup_parts(question)[0]):
        return IntentResult(
            question_type="query",
            intent="smalltalk",
            data_scope="self",
            entities={"fallback_answer": "这是生活需求，不会继承上一轮业务上下文。"},
            missing_params=(),
            confidence=0.82,
            canonical_question=question,
        )
    if _reserved_operational_surface(compact):
        return None
    people_followup = _contextual_people_followup_intent(question=question, text=text, context=context)
    if people_followup is not None:
        return people_followup
    if not context.chat_id or not _looks_like_contextual_followup(compact):
        return None
    conversation = _load_conversation_context(context.chat_id)
    if not conversation.turns:
        return None
    recent_text = "\n".join(
        f"{turn.user}\n{turn.assistant}\n{turn.route_path}\n{turn.route_label}"
        for turn in conversation.turns[-3:]
    ).lower()
    if _recent_external_context(recent_text):
        return IntentResult(
            question_type="query",
            intent="external_information_query",
            data_scope="external",
            entities={
                "external_query": _merge_recent_external_query(question=question, recent_text=recent_text),
                "provider_boundary": "external_realtime_not_connected",
            },
            missing_params=(),
            confidence=0.76,
            canonical_question=question,
        )
    if _recent_task_context(recent_text):
        return IntentResult(
            question_type="query",
            intent="task_query",
            data_scope=_contextual_scope(compact, fallback="self"),
            missing_params=(),
            confidence=0.72,
            canonical_question=question,
        )
    if _recent_calendar_context(recent_text):
        return IntentResult(
            question_type="query",
            intent="calendar_query",
            data_scope=_contextual_scope(compact, fallback="self"),
            missing_params=(),
            confidence=0.72,
            canonical_question=question,
        )
    return None


def _contextual_people_followup_intent(*, question: str, text: str, context: RuntimeContext) -> IntentResult | None:
    result_context = context.result_context
    if result_context is None or result_context.result_type not in {"people_search", "department_members", "organization_snapshot"}:
        return None
    compact = re.sub(r"\s+", "", str(text or "").lower())
    field_switch = _people_query_field(question)
    if field_switch and _is_people_field_switch_followup(compact):
        item = _single_people_context_item(result_context)
        keyword = str(item.get("name") or "").strip() if item else ""
        if keyword:
            field_text = {
                "mobile": "手机号",
                "email": "邮箱",
                "title": "岗位",
                "gender": "性别",
            }.get(field_switch, "信息")
            return IntentResult(
                question_type="query",
                intent="people_lookup",
                data_scope="person",
                entities={"keyword": keyword, "people_query_field": field_switch, "foundation_route": "people.person"},
                missing_params=(),
                confidence=0.88,
                canonical_question=f"{keyword}的{field_text}",
            )
        if _has_people_context_pronoun(compact):
            return IntentResult(
                question_type="query",
                intent="smalltalk",
                data_scope="self",
                entities={
                    "fallback_answer": "你说的是上一轮结果里的哪一位？告诉我名字后，我再查对应信息。",
                    "command_intent_trace": {
                        "source": "context_gate",
                        "reason": "people_pronoun_with_multiple_or_empty_results",
                        "result_type": result_context.result_type,
                        "result_count": result_context.count or len(result_context.items),
                        "requested_field": field_switch,
                    },
                },
                missing_params=("person",),
                confidence=0.55,
                canonical_question=question,
            )
    if not compact.startswith(("那", "那么", "还有")) and not compact.endswith(("呢", "的呢")):
        return None
    if _is_people_result_filter_followup(compact):
        return None
    keyword = _contextual_people_keyword(question)
    if not keyword:
        return None
    metadata = result_context.metadata if isinstance(result_context.metadata, dict) else {}
    query_field = str(metadata.get("people_query_field") or _people_query_field(str(getattr(result_context, "answer", "") or "")) or "")
    if not query_field:
        query_field = "profile"
    field_text = {
        "mobile": "手机号",
        "email": "邮箱",
        "title": "岗位",
        "gender": "性别",
        "profile": "信息",
    }.get(query_field, "信息")
    canonical = f"{keyword}的{field_text}"
    return IntentResult(
        question_type="query",
        intent="people_lookup",
        data_scope="person",
        entities={"keyword": keyword, "people_query_field": query_field, "foundation_route": "people.person"},
        missing_params=(),
        confidence=0.86,
        canonical_question=canonical,
    )


def _contextual_people_keyword(question: str) -> str:
    compact = re.sub(r"\s+", "", str(question or ""))
    compact = compact.removeprefix("那么").removeprefix("那").removeprefix("还有")
    compact = compact.removesuffix("的呢").removesuffix("呢")
    compact = compact.strip("，,。.!！?？")
    for token in ("你有吗", "有吗", "有么", "有没有", "你这有吗", "你这里有吗", "的"):
        compact = compact.replace(token, "")
    if not compact or compact in {"他", "她", "这个", "这个人", "上面这个"}:
        return ""
    if len(compact) > 8:
        return ""
    return compact


def _has_people_context_pronoun(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "").lower())
    return any(token in compact for token in ("他", "她", "那个人", "这个人", "刚才那个人", "那位", "这位"))


def _is_people_result_filter_followup(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "").lower()).strip("，,。.!！?？")
    compact = compact.removeprefix("那").removeprefix("那么").removesuffix("呢").removesuffix("的呢")
    if any(token in compact for token in ("男生", "男性", "男的", "男员工", "女生", "女性", "女的", "女员工")):
        return True
    if len(compact) <= 12 and any(token in compact for token in ("工程师", "经理", "主管", "总监", "销售", "财务", "测试", "运营", "人事", "研发", "部门", "事业部", "团队", "小组")):
        return True
    return False


def _is_people_field_switch_followup(compact: str) -> bool:
    if not compact:
        return False
    return _has_any(
        compact,
        (
            "我问的是",
            "问的是",
            "他的",
            "她的",
            "这个人的",
            "刚才那个人的",
            "上面那个人的",
            "那个人的",
        ),
    )


def _single_people_context_item(result_context: Any) -> dict[str, Any]:
    items = getattr(result_context, "items", ()) or ()
    if len(items) != 1:
        return {}
    item = items[0]
    return item if isinstance(item, dict) else {}


def _looks_like_contextual_followup(compact: str) -> bool:
    if not compact:
        return False
    return _has_any(
        compact,
        (
            "不是公司",
            "不是部门",
            "不是任务",
            "不是待办",
            "不是审批",
            "我的呢",
            "我问的是",
            "那公司",
            "那部门",
            "公司呢",
            "部门呢",
            "今天",
            "明天",
            "现在",
            "最近",
            "最新",
            "刚才那个",
            "上面那个",
            "继续",
            "展开",
            "详情",
            "第一个",
            "第二个",
            "第1",
            "第2",
            "全部显示",
        ),
    )


def _rejects_recent_business_context(compact: str) -> bool:
    return _has_any(compact, ("不是任务", "不是待办", "不是审批", "不是日程", "不是会议", "不是工作"))


def _is_non_work_conversation(text: str) -> bool:
    compact = text.replace(" ", "")
    if not compact:
        return False
    if _has_any(compact, ("外卖", "吃饭", "饿了", "点餐", "奶茶", "咖啡")):
        return True
    if _has_any(compact, ("你害怕", "你会害怕", "你累不累", "你饿不饿", "你需要吃饭", "你要吃饭")):
        return True
    if _has_any(compact, ("需要我帮忙", "要我帮忙", "我能帮你", "我可以帮你")):
        return True
    if _has_any(compact, ("帮我点", "帮我买", "帮我订")) and not _has_any(
        compact,
        ("任务", "待办", "日程", "会议", "审批", "邮件", "消息", "文档", "表格"),
    ):
        return True
    return False


def _non_work_conversation_answer(text: str) -> str:
    compact = text.replace(" ", "")
    if _has_any(compact, ("外卖", "吃饭", "饿了", "点餐", "奶茶", "咖啡")):
        return "明白，你现在说的是生活需求，不是任务。我可以陪你把想吃什么、预算和偏好理一下；真正查附近店铺或下单，需要后面接入外部实时服务。"
    if _has_any(compact, ("需要我帮忙", "要我帮忙", "我能帮你", "我可以帮你")):
        return "我在。你直接说想聊什么或要处理什么就行。"
    return "我在，听着呢。你可以继续说。"


def _ambiguous_operation_conversation_answer(text: str, context: RuntimeContext) -> str:
    compact = text.replace(" ", "")
    if not compact:
        return ""
    if _has_any(
        compact,
        (
            "任务",
            "待办",
            "日程",
            "会议",
            "审批",
            "邮件",
            "消息",
            "文档",
            "表格",
            "通讯录",
            "组织架构",
        ),
    ):
        return ""
    send_or_forward = _has_any(compact, ("转发", "转一下", "发一下", "发我一下", "传一下", "帮我转", "帮你转"))
    if send_or_forward:
        has_target = bool(_extract_send_target(text)) or _has_any(compact, ("发给我", "发给自己", "发到这里", "发到当前会话"))
        has_content = bool(_extract_message_text(text)) or bool(context.result_context)
        if has_target and has_content:
            return ""
        return "我还没对准你想转发或发送什么、发给谁。你可以直接说“发给谁”和“发什么”；如果只是随口聊，也可以继续说。"
    if _has_any(compact, ("处理一下", "弄一下", "搞一下", "帮忙一下", "帮我一下", "帮你一下")):
        return "我还没对准要处理的对象。你可以直接说要处理审批、任务、日程、邮件，或者先把情况讲给我听。"
    return ""


def _recent_external_context(text: str) -> bool:
    return _has_any(text, ("external_information", "天气", "联网", "外部实时", "网页资料", "公开信息"))


def _recent_task_context(text: str) -> bool:
    return _has_any(text, ("task_query", "任务查询", "任务", "workspace"))


def _recent_calendar_context(text: str) -> bool:
    return _has_any(text, ("calendar_query", "日程", "会议", "calendar"))


def _contextual_scope(compact: str, *, fallback: str) -> str:
    if _has_any(compact, ("我的", "我问的是", "不是公司", "不是部门", "我处理", "给我的")):
        return "self"
    if _has_any(compact, ("部门", "团队")):
        return "department"
    if _has_any(compact, ("公司", "全公司", "整个公司")):
        return "company"
    return fallback


def _merge_recent_external_query(*, question: str, recent_text: str) -> str:
    current = str(question or "").strip()
    if len(current.replace(" ", "")) > 12:
        return current
    for marker in ("天气", "气温", "官网", "新闻", "网页", "公开资料"):
        idx = recent_text.rfind(marker)
        if idx >= 0:
            start = max(0, idx - 30)
            return f"{recent_text[start:idx + len(marker)]} {current}".strip()
    return current


def _load_conversation_context(chat_id: str):
    from app.services.conversation_context import load_conversation_context

    return load_conversation_context(chat_id)


def _external_information_entities(question: str, context: RuntimeContext) -> dict:
    metadata = context.result_context.metadata if context.result_context and isinstance(context.result_context.metadata, dict) else {}
    previous_query = str(metadata.get("external_query") or "").strip()
    query = question.strip()
    if previous_query and len(query.replace(" ", "")) <= 12:
        query = f"{previous_query} {query}".strip()
    return {
        "query": query,
        "external_query": query,
        "external_category": _external_information_category(query),
        "requires_realtime": True,
        "provider_boundary": "external_realtime_not_connected",
    }


def _external_information_category(query: str) -> str:
    compact = str(query or "").replace(" ", "")
    if _is_local_public_information_query(compact):
        return "local_realtime"
    if _has_any(compact, ("天气", "气温", "下雨", "降雨", "空气质量", "台风")):
        return "weather_realtime"
    return "public_realtime"


def _is_governance_view_query(text: str) -> bool:
    return _has_any(
        text,
        (
            "能力清册",
            "能力目录",
            "治理视图",
            "治理状态",
            "能力接入情况",
            "还有哪些没接",
            "还缺什么能力",
            "能力状态",
        ),
    )


def _is_analysis_question(text: str) -> bool:
    return _has_any(text, ("为什么", "原因", "分析", "怎么看", "如何看待", "复盘", "问题出在哪里"))


def _is_decision_question(text: str) -> bool:
    return _has_any(text, ("怎么办", "怎么处理", "如何处理", "建议", "应该怎么做", "下一步怎么做", "要不要"))


def _is_approval_query(text: str) -> bool:
    if _has_any(
        text,
        (
            "待我审批",
            "需要我审批",
            "我审批",
            "我的审批",
            "待审批",
            "待审",
            "待我审核",
            "需要我审核",
            "审批工作台",
            "待审批工作台",
            "待审工作台",
            "审批列表",
            "待审批列表",
            "审批待办",
            "审批实时待办",
        ),
    ):
        return True
    if _has_any(text, ("审批", "批复", "批准", "审核", "审的单")) and _has_any(
        text,
        ("待", "未", "需要我", "要我", "我批", "我审", "我处理", "有没有", "哪些", "查看", "查一下"),
    ):
        return True
    return "单子" in text and _has_any(text, ("审批", "待我", "需要我", "我批", "我审"))


def _is_approval_initiated(text: str) -> bool:
    return _has_any(text, ("我发起的审批", "我提交的审批", "我申请的审批", "我发起的单", "我提交的单"))


def _is_approval_detail(text: str, context: RuntimeContext) -> bool:
    return _has_approval_ref_context(context) and _has_any(text, ("详情", "明细", "展开", "看看第", "看第", "审批详情"))


def _is_approval_transfer(text: str, context: RuntimeContext) -> bool:
    return _has_approval_ref_context(context) and _has_any(text, ("转交", "转给"))


def _is_approval_add_sign(text: str, context: RuntimeContext) -> bool:
    return _has_approval_ref_context(context) and _has_any(text, ("加签", "加签给"))


def _is_approval_rollback(text: str, context: RuntimeContext) -> bool:
    return _has_approval_ref_context(context) and _has_any(text, ("退回", "打回", "回退"))


def _is_approval_remind(text: str, context: RuntimeContext) -> bool:
    return _has_approval_ref_context(context) and _has_any(text, ("催办", "催一下", "提醒审批人"))


def _is_approval_cancel(text: str, context: RuntimeContext) -> bool:
    return _has_approval_ref_context(context) and _has_any(text, ("撤回", "取消审批", "撤销审批"))


def _is_approval_cc(text: str, context: RuntimeContext) -> bool:
    return _has_approval_ref_context(context) and _has_any(text, ("抄送", "知会"))


def _is_approval_approve(text: str, context: RuntimeContext) -> bool:
    normalized = text.strip().lower().strip("。.!！?？ ")
    compact = re.sub(r"\s+", "", normalized)
    return _has_approval_ref_context(context) and (
        compact in {"通过", "通过通过", "同意", "同意同意", "批准", "批准批准"}
        or _has_any(text, ("通过第", "同意第", "审批通过", "帮我通过", "帮我同意", "通过这个", "同意这个"))
    )


def _is_approval_reject(text: str, context: RuntimeContext) -> bool:
    normalized = text.strip().lower().strip("。.!！?？ ")
    compact = re.sub(r"\s+", "", normalized)
    return _has_approval_ref_context(context) and (
        compact in {"拒绝", "拒绝拒绝", "驳回", "驳回驳回", "不同意", "不同意不同意"}
        or _has_any(text, ("拒绝第", "驳回第", "审批拒绝", "审批驳回", "帮我拒绝", "帮我驳回", "拒绝这个", "驳回这个"))
    )


def _has_approval_ref_context(context: RuntimeContext) -> bool:
    return _has_approval_result_context(context) or bool(_current_approval_item(context))


def _has_approval_result_context(context: RuntimeContext) -> bool:
    return bool(
        context.result_context
        and context.result_context.items
        and (
            context.result_context.result_type in {"approval_list", "approval_detail", "approval_query"}
            or any(_looks_like_approval_item(item) for item in context.result_context.items[:3])
        )
    )


def _looks_like_approval_item(item: dict[str, object]) -> bool:
    if not isinstance(item, dict):
        return False
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else item
    return any(
        str(raw.get(key) or "").strip()
        for key in (
            "approval_name",
            "definition_name",
            "instance_code",
            "process_code",
            "task_id",
            "serial_number",
        )
    )


def _is_task_create(text: str) -> bool:
    return _has_any(
        text,
        (
            "创建任务",
            "新建任务",
            "创建一个任务",
            "新建一个任务",
            "创建待办",
            "新建待办",
            "创建一个待办",
            "新建一个待办",
            "加个待办",
            "加一个待办",
            "记个待办",
            "记一个待办",
            "安排个任务",
            "安排一个任务",
            "提醒我",
        ),
    ) and _has_any(text, ("任务", "待办", "提醒我"))


def _task_summary(question: str) -> str:
    summary = question.strip()
    summary = re.sub(r"^\s*(给|让|安排|指派)?\s*(这些人|他们|她们|这批人)\s*", "", summary)
    summary = re.sub(
        r"^\s*(帮我|请)?\s*(创建|新建|加|记|安排)\s*(一个|个)?\s*(任务|待办)[:：]?\s*",
        "",
        summary,
    )
    summary = re.sub(r"^\s*(做|处理|负责|执行)\s*(任务|待办)?[:：]?\s*", "", summary)
    summary = re.sub(r"^\s*(帮我|请)?\s*提醒我[:：]?\s*", "", summary)
    return summary.strip() or question.strip()


def _is_task_search(text: str) -> bool:
    return _has_any(text, ("搜索任务", "搜索待办", "查找任务", "查找待办")) or (
        _has_any(text, ("任务", "待办")) and _has_any(text, ("搜索", "查找", "关键词"))
    )


def _is_task_complete(text: str) -> bool:
    return _has_any(text, ("完成任务", "完成待办", "标记完成", "设为完成", "任务完成", "待办完成", "完成第"))


def _is_task_query(text: str) -> bool:
    if _is_company_intro_query(text) or _is_people_aggregate_query(text):
        return False
    return (
        _has_any(
            text,
            (
                "我的任务",
                "我的待办",
                "待办有哪些",
                "任务有哪些",
                "哪些任务",
                "哪些待办",
                "需要我处理",
                "要我处理",
                "我处理的任务",
                "我负责的任务",
                "分配给我的任务",
                "查一下待办",
                "查一下任务",
                "查看待办",
                "查看任务",
            ),
        )
        or (_has_any(text, ("任务", "待办")) and _has_any(text, ("全公司", "公司", "所有", "全部", "部门", "团队", "小组", "延期", "高风险", "本周到期")))
    )


def _is_calendar_create(text: str) -> bool:
    if _has_any(text, ("会议纪要", "会议记录", "总结会议")):
        return False
    if _has_any(
        text,
        (
            "创建日程",
            "新建日程",
            "创建一个日程",
            "新建一个日程",
            "安排会议",
            "安排一个会议",
            "约个会",
            "约一个会",
            "开会",
            "建个会议",
            "建一个会议",
            "创建会议",
            "创建一个会议",
            "新建会议",
            "新建一个会议",
        ),
    ):
        return True
    return (
        _has_any(text, ("创建", "新建", "安排", "约", "建"))
        and _has_any(text, ("会议", "日程", "会"))
        and _has_any(
            text,
            (
                "今天",
                "明天",
                "后天",
                "上午",
                "中午",
                "下午",
                "晚上",
                "今晚",
                "点",
                ":",
                "：",
            ),
        )
    )


def _is_calendar_query(text: str) -> bool:
    if _is_company_intro_query(text) or _is_people_aggregate_query(text):
        return False
    return _has_any(
        text,
        (
            "今天日程",
            "明天日程",
            "后天日程",
            "本周日程",
            "这周日程",
            "我的日程",
            "日程安排",
            "查看日程",
            "查一下日程",
            "部门日程",
            "团队日程",
            "小组日程",
            "全公司日程",
            "公司日程",
            "所有日程",
            "全部日程",
            "今天有什么会",
            "明天有什么会",
            "后天有什么会",
            "本周有什么会",
            "这周有什么会",
        ),
    )


def _query_data_scope(text: str) -> str:
    if _has_self_scope_signal(text):
        return "self"
    if _has_any(text, ("全公司", "公司", "所有", "全部")):
        return "company"
    if _has_any(text, ("部门", "团队", "小组")):
        return "department"
    if _has_any(text, ("张三", "李四", "王五", "某人", "指定人", "指定人员")):
        return "person"
    return "self"


def _has_self_scope_signal(text: str) -> bool:
    return _has_any(
        text,
        (
            "我的",
            "我自己",
            "需要我",
            "要我",
            "我处理",
            "我负责",
            "分配给我",
            "给我的",
            "待我",
            "我问的是",
        ),
    )


def _is_mail_draft_create(text: str) -> bool:
    return _has_any(text, ("邮件", "email", "mail")) and _has_any(text, ("起草", "草稿", "写一封", "拟一封", "写封", "新建邮件", "创建邮件", "发邮件"))


def _is_mail_search(text: str) -> bool:
    return _has_any(text, ("搜索邮件", "查找邮件")) or (
        _has_any(text, ("邮件", "邮箱", "收件箱")) and _has_any(text, ("搜索", "查找", "关键词", "包含"))
    )


def _is_mail_query(text: str) -> bool:
    return _has_any(text, ("我的邮件", "收件箱", "查看邮件", "查一下邮件", "最近邮件", "未读邮件", "有哪些邮件", "邮件列表"))


def _is_im_send(text: str) -> bool:
    return _has_any(text, ("发给我", "发给自己", "发到这里", "发到当前会话", "发到这个群", "发消息", "发送消息", "发条信息", "发条消息", "发个信息", "发个消息", "告诉")) or (
        _has_any(text, ("发给", "发到", "发送给", "转发给")) and not _has_any(text, ("邮件", "邮箱"))
    )


def _is_im_chat_search(text: str) -> bool:
    return _has_any(text, ("搜索群", "查找群", "找群", "查群", "群聊搜索")) or (
        _has_any(text, ("群", "群聊")) and _has_any(text, ("搜索", "查找", "找一下", "查一下"))
    )


def _is_im_message_query(text: str) -> bool:
    return _has_any(text, ("查看群消息", "查群消息", "最近群消息", "聊天记录", "群聊记录"))


def _is_department_members_query(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "").lower())
    if _has_any(compact, ("不要告诉我多少部门", "不用告诉我多少部门", "没必要告诉我多少部门", "不要部门数", "不用部门数")):
        return False
    has_org_unit = _has_any(compact, ("部门", "事业部", "团队", "中心", "小组", "组", "财务部", "研发部", "测试部", "运营部", "销售部")) or bool(
        re.search(r"[\u4e00-\u9fffA-Za-z0-9]{1,20}(?:部|组)", compact)
    )
    return has_org_unit and _has_any(
        compact,
        ("有哪些人", "都有谁", "成员", "人员", "同事", "名单", "多少人", "几个人", "几位", "多少位", "多少个"),
    )


def _is_people_aggregate_query(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "").lower()).replace("多少个", "多少")
    if not compact:
        return False
    if _has_any(compact, ("有谁的号码", "谁的号码", "有谁的电话", "谁的电话")):
        return True
    if _has_any(
        compact,
        (
            "任务",
            "待办",
            "日程",
            "会议",
            "审批",
            "邮件",
            "消息",
            "文档",
            "知识",
            "主营业务",
            "主要业务",
            "业务范围",
            "公司业务",
            "做什么",
            "干什么",
        ),
    ):
        return False
    subject_signal = _has_any(
        compact,
        (
            "公司",
            "全公司",
            "企业",
            "组织",
            "通讯录",
            "团队",
            "我们",
            "员工",
            "人员",
            "同事",
            "男生",
            "女生",
            "男性",
            "女性",
            "董事长",
            "负责人",
            "岗位",
            "职位",
            "工程师",
            "经理",
            "主管",
            "总监",
            "销售",
            "财务",
            "测试",
            "运营",
            "人事",
            "研发",
        ),
    )
    role_identity_signal = _has_any(compact, ("是谁", "谁是")) and _has_any(
        compact,
        ("董事长", "负责人", "岗位", "职位", "工程师", "经理", "主管", "总监", "销售", "财务", "测试", "运营", "人事", "研发"),
    )
    metric_signal = role_identity_signal or _has_any(
        compact,
        (
            "多少",
            "多少人",
            "多少个人",
            "几个人",
            "几个",
            "人数",
            "员工数",
            "人员数",
            "男生",
            "女生",
            "男性",
            "女性",
            "性别",
            "构成",
            "分布",
            "规模",
            "有哪些",
            "都有谁",
            "分别是谁",
            "名单",
            "发我",
            "发下",
            "发我下",
            "给我",
            "给我下",
            "发一下",
        ),
    )
    return subject_signal and metric_signal


def _people_query_mode(text: str) -> str:
    compact = re.sub(r"\s+", "", str(text or "").lower()).replace("多少个", "多少")
    wants_list = _has_any(compact, ("是谁", "谁是", "分别是谁", "都有谁", "名单", "列出", "全部显示", "有哪些"))
    if _has_any(compact, ("有谁的号码", "谁的号码", "有谁的电话", "谁的电话")):
        return "list"
    if "通讯录" in compact and _has_any(compact, ("发我", "发下", "发我下", "给我", "给我下", "发一下")):
        return "list"
    if "数量" in compact and _has_any(compact, ("只", "只需", "只要", "告诉我", "回答")):
        return "count_only"
    if _has_any(compact, ("只需要回答", "只回答", "不用告诉", "不要告诉", "不用给我详情", "不要给我详情", "不用详情", "不要详情", "不用明细", "不要明细", "不用列", "不要列", "没必要告诉", "直接回答")):
        return "count_only"
    if _has_any(compact, ("男生", "男性", "男的", "男员工", "女生", "女性", "女的", "女员工")):
        return "gender_list" if wants_list else "gender_count"
    if _has_any(compact, ("岗位", "职位", "董事长", "负责人", "工程师", "经理", "主管", "总监", "销售", "财务", "测试", "运营", "人事", "研发")):
        return "title_list" if wants_list else "title_count"
    if wants_list:
        return "list"
    return "count"


def _is_people_lookup(text: str) -> bool:
    if _has_any(text, ("电话", "邮箱", "手机号")):
        return True
    if _has_any(text, ("男还是女", "女还是男", "男性还是女性", "性别")):
        return True
    if _has_any(text, ("查通讯录", "通讯录查", "找人", "找一下人", "搜索人员", "人员搜索")):
        return True
    if _has_any(text, ("谁担任", "负责人是谁", "谁负责")):
        return True
    if _has_any(text, ("职位", "岗位")) and not _has_any(text, ("我", "我的", "你知道我", "我现在", "我在公司", "我在这个公司")):
        return True
    return False


def _extract_app_token(text: str) -> str | None:
    match = _APP_TOKEN_PATTERN.search(text)
    return match.group(1) if match else None


def _people_keyword(question: str) -> str:
    embedded_keyword, _ = _embedded_people_lookup_parts(question)
    if embedded_keyword:
        return embedded_keyword
    keyword = question
    for token in (
        "是谁",
        "谁是",
        "谁担任",
        "公司",
        "那",
        "那么",
        "还有",
        "呢",
        "吗",
        "的",
        "是",
        "是什么",
        "是多少",
        "多少",
        "号码",
        "是什么岗位",
        "是什么职位",
        "什么岗位",
        "什么职位",
        "男还是女",
        "女还是男",
        "男性还是女性",
        "性别",
        "电话",
        "邮箱",
        "手机号",
        "手机",
        "职位",
        "岗位",
        "职务",
        "部门",
        "和",
        "以及",
        "与",
        "什么",
        "告诉我",
        "你有吗",
        "有吗",
        "有么",
        "有没有",
        "查一下",
        "帮我查",
        "帮我",
        "请",
    ):
        keyword = keyword.replace(token, "")
    return keyword.strip() or question.strip()


def _embedded_people_lookup_parts(question: str) -> tuple[str, str]:
    compact = re.sub(r"\s+", "", str(question or ""))
    if _has_any(compact, ("我是谁", "你是谁", "你知道我", "我现在", "我在这个公司", "我在公司", "收件箱", "邮件")):
        return "", ""
    field_pattern = r"(?:电话号码|手机号|电话|号码|手机|邮箱|职位|岗位|职务|直属上级|上级|领导)"
    patterns = (
        rf"([\u4e00-\u9fffA-Za-z·.\-]{{2,16}})的({field_pattern})",
        rf"([\u4e00-\u9fffA-Za-z·.\-]{{2,8}})({field_pattern})",
        rf"把([\u4e00-\u9fffA-Za-z·.\-]{{2,16}})的?({field_pattern})(?:告诉我|发我|给我)",
    )
    for pattern in patterns:
        match = re.search(pattern, compact)
        if not match:
            continue
        keyword = _clean_people_keyword_candidate(match.group(1))
        field = _people_query_field(match.group(2))
        if field == "email" and compact[match.end(2): match.end(2) + 1] in {"里", "中"}:
            continue
        if keyword and field:
            return keyword, field
    return "", ""


def _clean_people_keyword_candidate(value: str) -> str:
    text = str(value or "").strip("，,。.!！?？")
    if any(token in text for token in ("谁", "我", "你")):
        return ""
    if "是什么" in text:
        text = text.split("是什么", 1)[0]
    if any(token in text for token in ("岗位", "职位", "职务", "领导", "直属上级", "上级")):
        text = re.split(r"(?:是|的|什么|岗位|职位|职务|领导|直属上级|上级)", text, maxsplit=1)[0]
    for token in ("那就把", "那你把", "请把", "帮我把", "把", "告诉我", "我让他", "让他", "给我", "发我", "查一下", "那", "那么", "还有"):
        text = text.replace(token, "")
    if any(token in text for token in ("公司", "通讯录", "你", "我")):
        parts = re.findall(r"[\u4e00-\u9fffA-Za-z·.\-]{2,8}", text)
        text = parts[-1] if parts else text
    text = text.removesuffix("的")
    return text.strip()


def _people_query_field(text: str) -> str:
    compact = re.sub(r"\s+", "", str(text or "").lower())
    if _has_any(compact, ("电话", "号码", "手机号", "手机")):
        return "mobile"
    if "邮箱" in compact:
        return "email"
    if _has_any(compact, ("职位", "岗位", "职务")):
        return "title"
    if _has_any(compact, ("直属上级", "上级", "领导")):
        return "leader"
    if _has_any(compact, ("男还是女", "女还是男", "男性还是女性", "性别")):
        return "gender"
    return ""


def _department_keyword(question: str) -> str:
    keyword = question
    for token in ("帮我", "请", "查一下", "查看", "有哪些人", "有多少人", "都有多少人", "多少人", "几个人", "几位", "多少位", "多少个", "都有谁", "分别是谁", "成员", "人员", "同事", "名单", "公司", "的", "都", "，", ",", "。", "？", "?"):
        keyword = keyword.replace(token, "")
    return keyword.strip() or question.strip()


def _task_search_keyword(question: str) -> str:
    keyword = question.strip()
    for token in ("帮我", "请", "搜索任务", "搜索待办", "查找任务", "查找待办", "搜索", "查找", "任务", "待办", "关键词", "：", ":"):
        keyword = keyword.replace(token, "")
    return keyword.strip()


def _task_ref(question: str, context: RuntimeContext) -> dict[str, str]:
    action_entities = _runtime_action_task_entities(context)
    if action_entities:
        return action_entities
    guid = _extract_task_guid(question)
    if guid:
        return {"task_guid": guid}
    index = _task_position_index(question)
    if index is None:
        return {}
    result_context = context.result_context
    if result_context is None or result_context.result_type != "task_list":
        return {}
    if not result_context.items:
        return {}
    item = result_context.items[index] if index >= 0 else result_context.items[-1]
    task_guid = str(item.get("guid") or item.get("task_guid") or item.get("id") or "").strip()
    title = str(item.get("title") or "").strip()
    return {"task_guid": task_guid, "title": title} if task_guid else {}


def _runtime_action_task_entities(context: RuntimeContext) -> dict[str, str]:
    for key in ("runtime_v5_confirmed_action_entities", "runtime_v5_pending_action"):
        payload = context.session_context.get(key)
        entities = payload.get("entities") if isinstance(payload, dict) and key == "runtime_v5_pending_action" else payload
        if not isinstance(entities, dict):
            continue
        task_guid = str(entities.get("task_guid") or "").strip()
        if task_guid:
            return {"task_guid": task_guid, "title": str(entities.get("title") or "").strip()}
    return {}


def _extract_task_guid(question: str) -> str | None:
    match = _TASK_GUID_PATTERN.search(question)
    return match.group(1) if match else None


def _task_position_index(question: str) -> int | None:
    if "最后" in question:
        return -1
    mapping = {
        "第一个": 0,
        "第一条": 0,
        "第一笔": 0,
        "第1个": 0,
        "第1条": 0,
        "第1笔": 0,
        "1号": 0,
        "第二个": 1,
        "第二条": 1,
        "第二笔": 1,
        "第2个": 1,
        "第2条": 1,
        "第2笔": 1,
        "2号": 1,
        "第三个": 2,
        "第三条": 2,
        "第三笔": 2,
        "第3个": 2,
        "第3条": 2,
        "第3笔": 2,
        "3号": 2,
    }
    for token, index in mapping.items():
        if token in question:
            return index
    match = re.search(r"第\s*(\d+)\s*(?:个|条|笔)?", question)
    if match:
        return max(int(match.group(1)) - 1, 0)
    return None


def _approval_ref(question: str, context: RuntimeContext) -> dict[str, object]:
    index = _task_position_index(question)
    result_context = context.result_context
    if result_context is not None and _has_approval_result_context(context):
        if index is None:
            if result_context.result_type == "approval_detail" or len(result_context.items) == 1:
                index = 0
            elif _has_any(question, ("这个", "这笔", "这条", "它")):
                index = 0
            else:
                current = _current_approval_item(context)
                if current:
                    return current
                return {}
        item = result_context.items[index] if index >= 0 else result_context.items[-1]
        return {"item": item, "index": index}

    current = _current_approval_item(context)
    if current and index is None:
        return current
    return {}


def _current_approval_item(context: RuntimeContext) -> dict[str, object]:
    current = context.session_context.get(_APPROVAL_CURRENT_KEY)
    if not isinstance(current, dict):
        return {}
    item = current.get("item")
    if not isinstance(item, dict) or not _looks_like_approval_item(item):
        return {}
    raw_index = current.get("index")
    try:
        index = int(raw_index)
    except (TypeError, ValueError):
        index = 0
    if index is None:
        index = 0
    return {"item": item, "index": index}


def _approval_comment(question: str, *, default: str) -> str:
    match = re.search(r"(?:理由|原因|备注|意见)\s*[：:]\s*(.+)$", question)
    if match:
        return match.group(1).strip()
    return default


def _approval_target_keyword(question: str) -> str:
    match = re.search(r"(?:给|转给|转交给|加签给|抄送给)\s*([^，。,；;：:\s]+)", question)
    if match:
        return match.group(1).strip()
    return ""


def _calendar_summary(question: str) -> str:
    title = _calendar_explicit_title(question)
    if title:
        return title
    summary = question.strip()
    calendar_create_tokens = (
        "帮我",
        "请",
        "创建日程",
        "新建日程",
        "创建一个日程",
        "新建一个日程",
        "安排会议",
        "约个会",
        "约一个会",
        "建个会议",
        "建一个会议",
        "创建会议",
        "创建一个会议",
        "新建会议",
        "新建一个会议",
        "安排一个会议",
        "安排个会议",
    )
    for token in sorted(calendar_create_tokens, key=len, reverse=True):
        summary = summary.replace(token, "")
    summary = summary.lstrip("：: ")
    summary = re.sub(r"(今天|明天|后天|本周|这周)?\s*(上午|中午|下午|晚上|今晚)?\s*(?:\d{1,2}|[零一二两三四五六七八九十]{1,3})(?:点|:|：)(?:半|[0-5]?\d分?)?", "", summary)
    summary = summary.replace("开会", "会议").replace("的会议", "会议").replace("一个会议", "会议")
    return summary.strip() or question.strip()


def _calendar_explicit_title(question: str) -> str:
    match = re.search(r"(?:主题|标题)\s*(?:是|为|:|：)\s*(.+)$", question)
    if not match:
        return ""
    title = match.group(1).strip()
    title = re.split(r"[。；;，,]\s*", title, maxsplit=1)[0].strip()
    return title


def _calendar_time_params(question: str) -> dict[str, str]:
    match = re.search(
        r"(\d{4}-\d{2}-\d{2}[ tT]\d{2}:\d{2}(?::\d{2})?)\s*(?:到|至|-|~)\s*(\d{4}-\d{2}-\d{2}[ tT]\d{2}:\d{2}(?::\d{2})?)",
        question,
    )
    if match:
        return {"start": _normalize_calendar_iso(match.group(1)), "end": _normalize_calendar_iso(match.group(2))}

    start = _relative_calendar_start(question)
    if start is None:
        return {}
    end = start + timedelta(hours=1)
    return {"start": _format_calendar_time(start), "end": _format_calendar_time(end)}


def _calendar_query_params(question: str) -> dict[str, str]:
    start = _relative_day_start(question)
    if start is None:
        return {}
    if "本周" in question or "这周" in question:
        end = start + timedelta(days=max(1, 7 - start.weekday()))
    else:
        end = start + timedelta(days=1)
    return {"start": _format_calendar_time(start), "end": _format_calendar_time(end)}


def _relative_calendar_start(question: str) -> datetime | None:
    day_start = _relative_day_start(question)
    if day_start is None:
        return None
    hour_minute = _calendar_hour_minute(question)
    if hour_minute is None:
        return None
    hour, minute = hour_minute
    return day_start.replace(hour=hour, minute=minute)


def _relative_day_start(question: str) -> datetime | None:
    today = datetime.now(_LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    if "后天" in question:
        return today + timedelta(days=2)
    if "明天" in question:
        return today + timedelta(days=1)
    if "今天" in question or "本周" in question or "这周" in question:
        return today
    return None


def _calendar_hour_minute(question: str) -> tuple[int, int] | None:
    match = re.search(r"(上午|中午|下午|晚上|今晚)?\s*(\d{1,2}|[零一二两三四五六七八九十]{1,3})(?:点|:|：)(半|[0-5]?\d分?)?", question)
    if not match:
        return None
    period = match.group(1) or ""
    hour = _cn_hour(match.group(2))
    minute_raw = match.group(3) or ""
    minute = 30 if minute_raw == "半" else int(minute_raw.replace("分", "") or 0)
    if period in {"下午", "晚上", "今晚"} and hour < 12:
        hour += 12
    if period == "中午" and hour < 11:
        hour += 12
    return hour, minute


def _cn_hour(value: str) -> int:
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if value.startswith("十"):
        return 10 + digits.get(value[-1], 0)
    if "十" in value:
        left, _, right = value.partition("十")
        return digits.get(left, 0) * 10 + digits.get(right, 0)
    return digits.get(value, 0)


def _normalize_calendar_iso(value: str) -> str:
    normalized = value.replace(" ", "T")
    return normalized if "+" in normalized or normalized.endswith("Z") else f"{normalized}+08:00"


def _format_calendar_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S+08:00")


def _mail_search_keyword(question: str) -> str:
    keyword = question.strip()
    for token in ("帮我", "请", "搜索邮件", "查找邮件", "搜索", "查找", "邮件", "邮箱", "收件箱", "关键词", "包含", "：", ":"):
        keyword = keyword.replace(token, "")
    return keyword.strip()


def _mail_draft_params(question: str, context: RuntimeContext | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {}
    people_targets = people_targets_from_result_context(context.result_context if context else None)
    if people_targets and references_people_context(question):
        emails = [str(item.get("email") or "").strip() for item in people_targets if str(item.get("email") or "").strip()]
        params["people_targets"] = [dict(item) for item in people_targets]
        params["people_target_count"] = str(len(people_targets))
        if emails:
            params["to"] = ", ".join(emails)
    email = _extract_email(question)
    if email:
        params["to"] = email
    subject = _extract_labeled_segment(question, ("主题", "标题"))
    if subject:
        params["subject"] = subject
    body = _extract_labeled_segment(question, ("正文", "内容", "说"))
    if body:
        params["body"] = body
    return params


def _people_context_attendee_params(question: str, people_targets: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    if not people_targets or not references_people_context(question):
        return {}
    attendee_ids = [str(item.get("open_id") or "").strip() for item in people_targets if str(item.get("open_id") or "").strip()]
    params: dict[str, Any] = {
        "people_targets": [dict(item) for item in people_targets],
        "people_target_count": str(len(people_targets)),
    }
    if attendee_ids:
        params["attendee_ids"] = attendee_ids
        params["user_id_type"] = "open_id"
    return params


def _people_context_task_member_params(question: str, people_targets: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    if not people_targets or not references_people_context(question):
        return {}
    members = [str(item.get("open_id") or "").strip() for item in people_targets if str(item.get("open_id") or "").strip()]
    params: dict[str, Any] = {
        "people_targets": [dict(item) for item in people_targets],
        "people_target_count": str(len(people_targets)),
    }
    if members:
        params["members"] = members
        params["user_id_type"] = "open_id"
    return params


def _extract_email(question: str) -> str | None:
    match = _EMAIL_PATTERN.search(question)
    return match.group(0) if match else None


def _extract_labeled_segment(question: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        match = re.search(rf"{label}\s*[：:]\s*(.+?)(?=\s*(?:主题|标题|正文|内容|说)\s*[：:]|$)", question)
        if match:
            return match.group(1).strip()
    return ""


def _im_send_params(question: str, context: RuntimeContext) -> dict[str, str]:
    params: dict[str, str | list[dict[str, str]]] = {}
    if _has_any(question, ("用机器人发", "机器人发", "以机器人", "用大飞哥发", "大飞哥通知", "系统通知", "自动推送", "自动通知")):
        params["execution_identity"] = "bot"
    elif _has_any(question, ("替我发", "用我", "以我的名义", "我发给")):
        params["execution_identity"] = "user"
    transfer_target = _extract_send_target(question)
    people_targets = people_targets_from_result_context(context.result_context)
    if not people_targets:
        people_targets = _people_targets_from_runtime_action(context)
    if transfer_target and not references_people_context(transfer_target[1]):
        target_type, target = transfer_target
        params["target_type"] = target_type
        params["target"] = target
    elif people_targets and references_people_context(question):
        params["target_type"] = "people_context"
        params["people_targets"] = [dict(item) for item in people_targets]
        params["people_target_count"] = str(len(people_targets))
        delivery_mode = _people_context_delivery_mode(question)
        if delivery_mode:
            params["delivery_mode"] = delivery_mode
    elif transfer_target:
        target_type, target = transfer_target
        params["target_type"] = target_type
        params["target"] = target
    elif _has_any(question, ("发给我", "发给自己", "发送给我")):
        params["target_type"] = "self"
        params["user_id"] = context.identity.open_id
    elif _has_any(question, ("发到这里", "发到当前会话", "发到这个群")):
        params["target_type"] = "current_chat"
    text = _extract_message_text(question)
    if text:
        params["text"] = text
    elif transfer_target:
        text = _extract_message_text_after_target(question, transfer_target[1])
        if text:
            params["text"] = text
    elif context.result_context:
        params["text"] = _result_context_message_text(context)
        params["use_previous_result"] = "true"
    return params  # type: ignore[return-value]


def _people_targets_from_runtime_action(context: RuntimeContext) -> tuple[dict[str, Any], ...]:
    for key in ("runtime_v5_pending_action", "runtime_v5_confirmed_action_entities"):
        payload = context.session_context.get(key)
        entities = payload.get("entities") if isinstance(payload, dict) and key == "runtime_v5_pending_action" else payload
        if not isinstance(entities, dict):
            continue
        if entities.get("target_type") != "people_context":
            continue
        targets = entities.get("people_targets")
        if isinstance(targets, list):
            return tuple(dict(item) for item in targets if isinstance(item, dict))
    return ()


def _people_context_delivery_mode(question: str) -> str:
    compact = re.sub(r"\s+", "", str(question or "").lower())
    if _has_any(compact, ("机器人通知", "用机器人", "机器人发", "系统通知", "自动通知", "大飞哥通知")):
        return "bot_multi_notify"
    if _has_any(compact, ("替我发", "用我", "以我的名义", "我发给", "分别发", "单独发", "单独发送", "私聊发")):
        return "user_multi_private"
    if _has_any(compact, ("拉群", "建群", "建个群", "创建群", "群里发", "发到群")):
        return "create_group_then_send"
    return ""


def _result_delivery_target_params(question: str, context: RuntimeContext) -> dict[str, str]:
    params: dict[str, str] = {}
    transfer_target = _extract_send_target(question)
    if transfer_target:
        target_type, target = transfer_target
        params["target_type"] = target_type
        params["target"] = target
    elif _has_any(question, ("发给我", "发给自己", "发送给我", "把文件发给我")):
        params["target_type"] = "self"
        params["user_id"] = context.identity.open_id
    elif _has_any(question, ("发到这里", "发到当前会话", "发到这个群")):
        params["target_type"] = "current_chat"
    return params


def _result_context_message_text(context: RuntimeContext) -> str:
    result_context = context.result_context
    if result_context is None:
        return ""
    if result_context.items:
        lines = []
        for index, item in enumerate(result_context.items[:20], start=1):
            title = str(item.get("title") or item.get("name") or item.get("subject") or item.get("summary") or "未命名")
            parts = [f"{index}. {title}"]
            for key, label in (
                ("applicant", "申请人"),
                ("amount", "金额"),
                ("status", "状态"),
                ("department", "部门"),
                ("title", "职位"),
                ("email", "邮箱"),
                ("mobile", "手机"),
            ):
                value = item.get(key)
                if value not in (None, "", [], {}):
                    parts.append(f"{label}：{value}")
            lines.append("｜".join(parts))
        return "\n".join(lines)
    return ""


def _extract_send_target(question: str) -> tuple[str, str] | None:
    for pattern in (
        r"发(?:条|个)?(?:信息|消息)给\s*(?!我|自己)(.+?)(?=\s*(?:说|内容是|消息是|通知|告诉)\s*[：:]?|[，,。；;]|$)",
        r"发(?:送)?给\s*(?!我|自己)(.+?)(?=\s*(?:说|内容是|消息是|通知|告诉)\s*[：:]?|[，,。；;]|$)",
        r"转发给\s*(?!我|自己)(.+?)(?=\s*(?:说|内容是|消息是|通知|告诉)\s*[：:]?|[，,。；;]|$)",
        r"给\s*(?!我|自己)(.+?)\s*发消息",
    ):
        match = re.search(pattern, question)
        if match:
            target = _normalize_send_target(match.group(1))
            if target:
                return _send_target_type_and_value(target)
    chat_match = re.search(r"发(?:送|条|个)?(?:信息|消息)?到\s*(.+?群(?:聊)?)(?=\s*(?:说|内容是|消息是|通知|告诉)\s*[：:]?|[：:，,。；;]|$)", question)
    if chat_match:
        target = _normalize_send_target(chat_match.group(1))
        return ("chat", target.replace("群聊", "").replace("群", "").strip() or target)
    return None


def _normalize_send_target(value: str) -> str:
    target = str(value or "").strip()
    target = re.sub(r"\s+", "", target)
    target = re.sub(r"(?:说|内容是|消息是|通知|告诉)\s*[：:]?.*$", "", target).strip()
    return target


def _send_target_type_and_value(target: str) -> tuple[str, str]:
    if any(token in target for token in ("群", "群聊")):
        return ("chat", target.replace("群聊", "").replace("群", "").strip() or target)
    return ("person", target)


def _extract_message_text(question: str) -> str:
    for pattern in (
        r"(?:说|内容是|消息是)\s*[：:]?\s*(.+)$",
        r"[：:]\s*(.+)$",
    ):
        match = re.search(pattern, question)
        if match:
            return match.group(1).strip()
    return ""


def _extract_message_text_after_target(question: str, target: str) -> str:
    if not target:
        return ""
    marker_index = question.find(target)
    if marker_index < 0:
        return ""
    tail = question[marker_index + len(target):].strip()
    tail = re.sub(r"^(?:说|内容是|消息是|通知|告诉)?\s*[：:\s，,]*", "", tail).strip()
    if _is_message_action_scaffold(tail):
        return ""
    return tail


def _is_message_action_scaffold(value: str) -> bool:
    compact = re.sub(r"\s+", "", str(value or ""))
    return compact in {"发消息", "发信息", "发送消息", "发送信息", "发条消息", "发条信息", "发个消息", "发个信息"}


def _extract_between(question: str, starts: tuple[str, ...], ends: tuple[str, ...]) -> str:
    for start in starts:
        if start not in question:
            continue
        tail = question.split(start, 1)[1].strip()
        for end in ends:
            if end and end in tail:
                value = tail.split(end, 1)[0].strip()
                if value:
                    return value
        if tail:
            return tail.strip()
    return ""


def _im_chat_query(question: str) -> str:
    query = question.strip()
    for token in ("帮我", "请", "搜索群", "查找群", "找群", "查群", "群聊搜索", "搜索", "查找", "找一下", "查一下", "群聊", "群", "：", ":"):
        query = query.replace(token, "")
    return query.strip() or question.strip()


def _im_message_query_params(question: str) -> dict[str, str]:
    # First V5 batch only supports explicit chat_id for message listing.
    match = re.search(r"\b(oc_[A-Za-z0-9_-]+)\b", question)
    return {"chat_id": match.group(1), "page_size": 20} if match else {}


def _is_docs_read(text: str) -> bool:
    return bool(_DOC_TOKEN_PATTERN.search(text)) or ("文档" in text and _has_any(text, ("读取", "读一下", "看一下", "查看", "打开", "总结")))


def _is_wiki_search(text: str) -> bool:
    return _has_any(text, ("知识库", "wiki", "知识空间")) and _has_any(text, ("查", "查询", "搜索", "列出", "看一下", "有哪些"))


def _extract_doc_token(question: str) -> str:
    match = _DOC_TOKEN_PATTERN.search(question)
    return match.group(1) if match else ""


def _extract_wiki_space_id(question: str) -> str:
    match = _WIKI_SPACE_PATTERN.search(question)
    return match.group(1) if match else ""


def _is_drive_list(text: str) -> bool:
    return _has_any(text, ("云盘", "云空间", "drive", "文件夹")) and _has_any(text, ("查", "查询", "列出", "看一下", "有哪些", "文件"))


def _drive_list_params(question: str) -> dict[str, str]:
    match = re.search(r"\b(fld[-A-Za-z0-9_]+)\b", question)
    return {"folder_token": match.group(1)} if match else {}


def _is_slides_read(text: str) -> bool:
    return bool(_SLIDES_URL_TOKEN_PATTERN.search(text) or _SLIDES_TOKEN_PATTERN.search(text)) and _has_any(text, ("读取", "读一下", "查看", "看一下", "总结", "分析", "幻灯片", "ppt", "slides"))


def _extract_slides_token(question: str) -> str:
    url_match = _SLIDES_URL_TOKEN_PATTERN.search(question)
    if url_match:
        return url_match.group(1)
    match = _SLIDES_TOKEN_PATTERN.search(question)
    return match.group(1) if match else ""


def _is_whiteboard_read(text: str) -> bool:
    return bool(_WHITEBOARD_TOKEN_PATTERN.search(text)) and _has_any(text, ("读取", "读一下", "查看", "看一下", "总结", "分析", "画板", "whiteboard"))


def _extract_whiteboard_token(question: str) -> str:
    match = _WHITEBOARD_TOKEN_PATTERN.search(question)
    return match.group(1) if match else ""


def _is_vc_meeting_search(text: str) -> bool:
    return _has_any(text, ("历史会议", "会议记录", "视频会议", "会议列表", "最近会议")) and _has_any(
        text,
        ("查", "查询", "搜索", "看一下", "查看", "最近", "有哪些"),
    )


def _is_attendance_query(text: str) -> bool:
    return _has_any(text, ("考勤", "打卡", "出勤")) and _has_any(text, ("查", "查询", "看一下", "查看", "最近", "记录", "今天", "本周"))


def _is_okr_query(text: str) -> bool:
    return _has_any(text, ("okr", "目标", "关键结果")) and _has_any(text, ("查", "查询", "看一下", "查看", "我的", "当前", "进展"))


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)
