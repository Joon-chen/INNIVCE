from __future__ import annotations

from app.services.runtime_v5.company_profile_query import COMPANY_PROFILE_FIELD_MARKERS, looks_like_company_profile_query
from app.services.runtime_v5.interaction_intent import classify_interaction_intent


_NON_BUSINESS_INTENTS = {
    "smalltalk",
    "runtime_status",
    "action_trace",
    "governance_view",
    "external_information_query",
}
_BUSINESS_QUERY_INTENTS = {
    "approval_query",
    "approval_detail",
    "approval_initiated",
    "task_query",
    "task_search",
    "calendar_query",
    "mail_query",
    "mail_search",
    "message_query",
    "people_lookup",
    "department_members",
    "organization_snapshot",
    "docs_read",
    "wiki_search",
    "drive_list",
}
_EXTERNAL_MARKERS = (
    "天气",
    "气温",
    "新闻",
    "官网",
    "官方网站",
    "网页",
    "网站",
    "公开资料",
    "网上",
    "搜索一下",
    "查一下网上",
    "股价",
    "汇率",
    "油价",
    "航班",
    "附近",
    "周边",
    "餐厅",
    "外卖",
    "门店",
    "酒店",
    "停车",
)
_SELF_SCOPE_MARKERS = (
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
)
_WORKSPACE_INTENTS = {"task_query", "task_search", "calendar_query"}
_WORKSPACE_MARKERS = (
    "任务",
    "待办",
    "日程",
    "会议",
    "工作负荷",
    "负荷",
    "逾期",
    "延期",
    "到期",
    "冲突",
    "安排",
    "排期",
    "项目",
)
_PEOPLE_AGGREGATE_MARKERS = (
    "多少人",
    "多少个人",
    "几个人",
    "人数",
    "员工数",
    "人员数",
    "男生",
    "女生",
    "男性",
    "女性",
    "性别",
    "人员构成",
    "组织规模",
)
_COMPANY_PROFILE_MARKERS = COMPANY_PROFILE_FIELD_MARKERS


def observe_command_route(
    *,
    question: str,
    intent: str,
    question_type: str,
    data_scope: str,
    confidence: float,
    route_source: str,
) -> dict:
    compact = _compact(question)
    interaction = classify_interaction_intent(question)
    risk_reasons: list[str] = []
    route_family = _route_family(intent=intent, question_type=question_type)

    if interaction.is_smalltalk and intent not in _NON_BUSINESS_INTENTS:
        risk_reasons.append("conversation_routed_to_business")
    if _has_external_signal(compact) and intent != "external_information_query":
        risk_reasons.append("external_info_routed_to_internal_business")
    if _has_self_scope_signal(compact) and data_scope != "self" and intent in _BUSINESS_QUERY_INTENTS:
        risk_reasons.append("self_scope_routed_to_broader_scope")
    if intent in _WORKSPACE_INTENTS and _has_company_or_people_signal_without_workspace_signal(compact):
        risk_reasons.append("organization_or_profile_routed_to_workspace")
    if question_type == "action" and confidence < 0.75:
        risk_reasons.append("low_confidence_action")

    return {
        "route_source": route_source,
        "route_family": route_family,
        "interaction_kind": interaction.kind,
        "interaction_confidence": interaction.confidence,
        "intent": intent,
        "question_type": question_type,
        "data_scope": data_scope,
        "confidence": confidence,
        "misroute_risk": bool(risk_reasons),
        "risk_reasons": risk_reasons,
        "denoise_action": _denoise_action(risk_reasons),
    }


def should_reject_candidate_route(
    *,
    question: str,
    candidate_intent: str,
    candidate_question_type: str,
    candidate_scope: str,
    rule_intent: str,
) -> bool:
    observation = observe_command_route(
        question=question,
        intent=candidate_intent,
        question_type=candidate_question_type,
        data_scope=candidate_scope,
        confidence=1.0,
        route_source="llm_candidate",
    )
    reasons = set(observation["risk_reasons"])
    if "conversation_routed_to_business" in reasons and rule_intent == "smalltalk":
        return True
    if "external_info_routed_to_internal_business" in reasons:
        return True
    if "organization_or_profile_routed_to_workspace" in reasons:
        return True
    return False


def should_force_self_scope(*, question: str, intent: str, data_scope: str) -> bool:
    return (
        data_scope != "self"
        and intent in _BUSINESS_QUERY_INTENTS
        and _has_self_scope_signal(_compact(question))
    )


def _route_family(*, intent: str, question_type: str) -> str:
    if intent in _NON_BUSINESS_INTENTS:
        return "conversation" if intent == "smalltalk" else intent
    if question_type == "action":
        return "action"
    if question_type in {"analysis", "insight", "decision"}:
        return "cognitive"
    return "business_query"


def _denoise_action(risk_reasons: list[str]) -> str:
    if "external_info_routed_to_internal_business" in risk_reasons:
        return "route_external_boundary"
    if "organization_or_profile_routed_to_workspace" in risk_reasons:
        return "route_people_or_knowledge"
    if "self_scope_routed_to_broader_scope" in risk_reasons:
        return "prefer_self_scope"
    if "conversation_routed_to_business" in risk_reasons:
        return "prefer_conversation"
    if "low_confidence_action" in risk_reasons:
        return "ask_confirmation_or_clarify"
    return "none"


def _has_external_signal(compact: str) -> bool:
    return any(marker in compact for marker in _EXTERNAL_MARKERS)


def _has_self_scope_signal(compact: str) -> bool:
    return any(marker in compact for marker in _SELF_SCOPE_MARKERS)


def _has_company_or_people_signal_without_workspace_signal(compact: str) -> bool:
    compact = compact.replace("多少个", "多少")
    has_people_signal = any(marker in compact for marker in _PEOPLE_AGGREGATE_MARKERS)
    has_company_profile_signal = looks_like_company_profile_query(compact)
    if not has_people_signal and not has_company_profile_signal:
        return False
    return not any(marker in compact for marker in _WORKSPACE_MARKERS)


def _compact(text: str) -> str:
    return str(text or "").replace(" ", "").strip().lower()
