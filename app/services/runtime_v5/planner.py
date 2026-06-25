from __future__ import annotations

from typing import Any

from app.services.runtime_v5.capabilities import capabilities_for_strategy
from app.services.runtime_v5.models import IntentResult, PlannerResult
from app.services.runtime_v5.provider_snapshot import provider_snapshot_operation


_STRATEGIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "smalltalk": ("smalltalk", ()),
    "action_trace": ("action_trace", ()),
    "runtime_status": ("runtime_status", ()),
    "governance_view": ("governance_view", ()),
    "approval_query": ("approval_query", ("approval",)),
    "approval_detail": ("approval_detail", ("approval",)),
    "approval_approve": ("approval_approve", ("approval",)),
    "approval_reject": ("approval_reject", ("approval",)),
    "approval_transfer": ("approval_transfer", ("approval",)),
    "approval_add_sign": ("approval_add_sign", ("approval",)),
    "approval_rollback": ("approval_rollback", ("approval",)),
    "approval_remind": ("approval_remind", ("approval",)),
    "approval_cancel": ("approval_cancel", ("approval",)),
    "approval_cc": ("approval_cc", ("approval",)),
    "approval_initiated": ("approval_initiated", ("approval",)),
    "people_lookup": ("people_lookup", ("people",)),
    "department_members": ("department_members", ("people",)),
    "organization_snapshot": ("organization_snapshot", ("people",)),
    "organization_export": ("organization_export", ("people", "base")),
    "task_query": ("task_query", ("task",)),
    "task_search": ("task_search", ("task",)),
    "task_create": ("task_create", ("task",)),
    "task_complete": ("task_complete", ("task",)),
    "task_update": ("task_update", ("task",)),
    "task_reopen": ("task_reopen", ("task",)),
    "task_delete": ("task_delete", ("task",)),
    "task_subtask_create": ("task_subtask_create", ("task",)),
    "task_comment": ("task_comment", ("task",)),
    "task_assign_members": ("task_assign_members", ("task",)),
    "task_update_followers": ("task_update_followers", ("task",)),
    "task_update_reminders": ("task_update_reminders", ("task",)),
    "task_upload_attachment": ("task_upload_attachment", ("task",)),
    "task_add_to_tasklist": ("task_add_to_tasklist", ("task",)),
    "task_set_ancestor": ("task_set_ancestor", ("task",)),
    "task_clear_ancestor": ("task_clear_ancestor", ("task",)),
    "tasklist_create": ("tasklist_create", ("task",)),
    "tasklist_update": ("tasklist_update", ("task",)),
    "tasklist_delete": ("tasklist_delete", ("task",)),
    "tasklist_update_members": ("tasklist_update_members", ("task",)),
    "tasklist_set_members": ("tasklist_set_members", ("task",)),
    "task_section_create": ("task_section_create", ("task",)),
    "task_section_update": ("task_section_update", ("task",)),
    "task_section_delete": ("task_section_delete", ("task",)),
    "calendar_query": ("calendar_query", ("calendar",)),
    "calendar_create": ("calendar_create", ("calendar",)),
    "mail_query": ("mail_query", ("mail",)),
    "mail_search": ("mail_search", ("mail",)),
    "mail_get_message": ("mail_get_message", ("mail",)),
    "mail_draft_create": ("mail_draft_create", ("mail",)),
    "message_send": ("message_send", ("im",)),
    "chat_search": ("chat_search", ("im",)),
    "message_query": ("message_query", ("im",)),
    "chat_create": ("chat_create", ("im",)),
    "chat_auto_join_public": ("chat_auto_join_public", ("im",)),
    "docs_read": ("docs_read", ("docs",)),
    "docs_edit": ("docs_edit", ("docs",)),
    "wiki_search": ("wiki_search", ("wiki",)),
    "drive_list": ("drive_list", ("drive",)),
    "drive_upload": ("drive_upload", ("drive",)),
    "sheets_write": ("sheets_write", ("sheets",)),
    "vc_meeting_search": ("vc_meeting_search", ("vc",)),
    "minutes_read": ("minutes_read", ("minutes",)),
    "attendance_query": ("attendance_query", ("attendance",)),
    "okr_query": ("okr_query", ("okr",)),
    "okr_update": ("okr_update", ("okr",)),
    "slides_read": ("slides_read", ("slides",)),
    "slides_write": ("slides_write", ("slides",)),
    "whiteboard_read": ("whiteboard_read", ("whiteboard",)),
    "whiteboard_write": ("whiteboard_write", ("whiteboard",)),
    "vc_agent_read": ("vc_agent_read", ("vc_agent",)),
    "vc_agent_join": ("vc_agent_join", ("vc_agent",)),
    "company_intro": ("company_intro", ("company_profile",)),
    "external_information_query": ("external_information_query", ("web",)),
    "risk_analysis": ("risk_analysis", ("workevent", "memory", "knowledge")),
    "general_analysis": ("general_analysis", ("workevent", "memory", "knowledge")),
    "decision_advice": ("decision_advice", ("workevent", "memory", "knowledge")),
    "general_query": ("general_query", ("knowledge",)),
}


def plan_task(
    intent: IntentResult,
    *,
    runtime_provider_snapshot: dict[str, Any] | None = None,
) -> PlannerResult:
    strategy, sources = _STRATEGIES.get(intent.intent, _STRATEGIES["general_query"])
    return PlannerResult(
        strategy=strategy,
        sources=sources,
        metadata={
            "question_type": intent.question_type,
            "data_scope": intent.data_scope,
            "runtime_provider_snapshot": _runtime_provider_snapshot_metadata(
                strategy=strategy,
                sources=sources,
                snapshot=runtime_provider_snapshot,
            ),
        },
    )


def _runtime_provider_snapshot_metadata(
    *,
    strategy: str,
    sources: tuple[str, ...],
    snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {
            "consumed": False,
            "status": "missing",
            "issue_count": 0,
            "planned_sources": list(sources),
            "source_status": [],
        }
    providers = snapshot.get("providers") if isinstance(snapshot.get("providers"), dict) else {}
    coverage = snapshot.get("coverage") if isinstance(snapshot.get("coverage"), list) else []
    capabilities = capabilities_for_strategy(strategy, sources)
    operation_status: list[dict[str, Any]] = []
    for capability in capabilities:
        operation_state = provider_snapshot_operation(
            snapshot,
            source=capability.source,
            operation=capability.operation,
        )
        operation_status.append(
            {
                "source": capability.source,
                "operation": capability.operation,
                "label": capability.label,
                "ready": bool(operation_state.get("ready")),
                "status": str(operation_state.get("status") or ""),
            }
        )
    source_status: list[dict[str, Any]] = []
    for source in sources:
        provider = providers.get(source) if isinstance(providers.get(source), dict) else {}
        source_coverage = [
            item
            for item in coverage
            if isinstance(item, dict) and str(item.get("source") or "") == source
        ]
        source_status.append(
            {
                "source": source,
                "exists": bool(provider.get("exists")),
                "enabled": bool(provider.get("enabled")),
                "healthy": bool(provider.get("healthy")),
                "covered_operations": [
                    str(item.get("operation") or "")
                    for item in source_coverage
                    if item.get("ready") and item.get("operation")
                ],
                "issue_count": sum(1 for item in source_coverage if not item.get("ready")),
            }
        )
    return {
        "consumed": True,
        "status": snapshot.get("status") or "",
        "label": snapshot.get("label") or "",
        "issue_count": int(snapshot.get("issue_count") or 0),
        "planned_sources": list(sources),
        "planned_operation_count": len(operation_status),
        "ready_operation_count": sum(1 for item in operation_status if item.get("ready")),
        "blocked_operation_count": sum(1 for item in operation_status if not item.get("ready")),
        "operation_status": operation_status,
        "top_blocked_operation": next((item for item in operation_status if not item.get("ready")), {}),
        "source_status": source_status,
    }


def strategy_registry() -> dict[str, tuple[str, ...]]:
    return {strategy: sources for strategy, sources in _STRATEGIES.values()}
