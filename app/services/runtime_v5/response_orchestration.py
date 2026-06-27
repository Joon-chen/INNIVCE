from __future__ import annotations

from typing import Literal

from app.services.runtime_v5.response_classification import classify_response_request


ResponseMode = Literal["instant", "llm_enhanced", "async_followup"]

_INSTANT_RESULT_TYPES = {
    "approval_list",
    "approval_detail",
    "task_list",
    "calendar_event_list",
    "workspace_aggregation_summary",
    "people_search",
    "department_members",
    "organization_snapshot",
    "mail_list",
    "docs_read",
    "drive_file_list",
    "vc_meeting_list",
    "attendance_record_list",
    "okr_objective_list",
    "slides_read",
    "whiteboard_read",
    "chat_list",
    "im_message_list",
    "memory_fact_list",
    "web_search_list",
    "runtime_pending_confirmation",
    "runtime_waiting_input",
    "waiting_authorization",
    "runtime_action",
    "approval_approve",
    "approval_reject",
    "task_complete",
    "task_create",
    "calendar_create",
}
_INSTANT_INTENTS = {"runtime_status", "governance_view", "action_trace"}
_BOUNDARY_TERMS = ("未接入", "没有接入", "没有权限", "不能生成", "不会改用", "权限拦截", "需要授权")


def build_response_policy(
    *,
    intent: str,
    result_type: str,
    data_scope: str,
    answer: str,
    question_type: str = "",
    requires_confirmation: bool = False,
) -> dict[str, object]:
    """Return V0 response orchestration metadata for Interaction consumers.

    This policy is intentionally deterministic. LLM output quality can improve
    wording, but it must not decide whether a business result should block.
    """

    mode = _response_mode(
        intent=intent,
        result_type=result_type,
        data_scope=data_scope,
        answer=answer,
        question_type=question_type,
        requires_confirmation=requires_confirmation,
    )
    llm_allowed = mode == "llm_enhanced"
    return {
        "response_mode": mode,
        "llm_allowed": llm_allowed,
        "async_followup_allowed": mode == "async_followup",
        "latency_budget_ms": _latency_budget_ms(mode),
        "reason": _response_policy_reason(
            mode=mode,
            intent=intent,
            result_type=result_type,
            data_scope=data_scope,
            answer=answer,
            requires_confirmation=requires_confirmation,
        ),
    }


def _response_mode(
    *,
    intent: str,
    result_type: str,
    data_scope: str,
    answer: str,
    question_type: str,
    requires_confirmation: bool,
) -> ResponseMode:
    if requires_confirmation:
        return "instant"
    classification = classify_response_request(answer=answer, intent=intent, result_type=result_type)
    if intent == "smalltalk":
        if classification.fact_kind in {"time", "date", "permission"}:
            return "instant"
        return "llm_enhanced"
    if classification.response_class == "fact":
        return "instant"
    if intent in _INSTANT_INTENTS or result_type in _INSTANT_RESULT_TYPES:
        return "instant"
    if result_type.endswith("_list") or result_type.endswith("_detail") or result_type.endswith("_empty"):
        return "instant"
    if any(term in answer for term in _BOUNDARY_TERMS):
        return "instant"
    if question_type in {"analysis", "insight", "decision"}:
        return "async_followup"
    return "instant"


def _latency_budget_ms(mode: ResponseMode) -> int:
    if mode == "instant":
        return 800
    if mode == "llm_enhanced":
        return 6000
    return 800


def _response_policy_reason(
    *,
    mode: ResponseMode,
    intent: str,
    result_type: str,
    data_scope: str,
    answer: str,
    requires_confirmation: bool,
) -> str:
    if requires_confirmation:
        return "confirmation_must_not_wait_for_llm"
    if intent == "smalltalk":
        return "smalltalk_can_use_bounded_conversation_llm"
    if result_type in _INSTANT_RESULT_TYPES or result_type.endswith(("_list", "_detail", "_empty")):
        return "business_result_must_reply_fast"
    if any(term in answer for term in _BOUNDARY_TERMS):
        return "scope_or_permission_boundary_must_reply_fast"
    if mode == "async_followup":
        return "insight_or_analysis_should_not_block_first_reply"
    return "operational_result_must_reply_fast"
