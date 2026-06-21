from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import AuditLog
from app.services.agent.policies import BotActor
from app.services.agent.runtime import agent_runtime_result_payload, answer_agent_message_with_trace
from app.services.agent.settings import get_company_agent_settings, update_company_agent_settings
from app.services.audit import write_audit_log


def preview_agent_trace(
    db: Session,
    *,
    company_id: UUID,
    question: str,
    normalized_command: str | None,
    chat_id: str | None,
    actor_role: str,
    actor_access_scope: str,
    actor_domains: list[str],
    actor_display_name: str | None,
    actor_open_id: str | None,
    actor_email: str | None,
) -> dict[str, Any]:
    agent_settings_payload = get_company_agent_settings(db, company_id)
    agent_settings = agent_settings_payload["settings"]
    actor = BotActor(
        role=actor_role,
        access_scope=actor_access_scope,
        domains=tuple(actor_domains),
        display_name=actor_display_name,
        open_id=actor_open_id,
        email=actor_email,
    )
    result = answer_agent_message_with_trace(
        db,
        company_id=company_id,
        question=question,
        normalized_command=normalized_command or question,
        chat_id=chat_id,
        actor=actor,
        planner_enabled=bool(agent_settings.get("planner_enabled")),
        max_planner_steps=int(agent_settings.get("max_planner_steps") or 3),
        allow_write_tools=bool(agent_settings.get("allow_write_tools")),
        require_write_confirmation=bool(agent_settings.get("require_write_confirmation")),
    )
    payload = agent_runtime_result_payload(result)
    trace_payload = payload.get("trace") or {}
    actor_context = trace_payload.get("actor_context") if isinstance(trace_payload.get("actor_context"), dict) else {}
    agent_identity = trace_payload.get("agent_identity") if isinstance(trace_payload.get("agent_identity"), dict) else {}
    write_policy_summary = agent_trace_write_policy_summary(payload.get("trace") or {})
    write_audit_log(
        db,
        action="agent.trace.preview",
        company_id=company_id,
        actor=actor_open_id or actor_display_name or actor_role,
        target_type="agent_trace",
        target_id=result.trace.route_path,
        payload={
            "status": "success",
            "semantic_intent": result.trace.semantic_intent,
            "route_path": result.trace.route_path,
            "route_scope": result.trace.route_scope,
            "route_reason": result.trace.route_reason,
            "agent_identity": agent_identity,
            "agent_id": agent_identity.get("agent_id"),
            "agent_type": agent_identity.get("agent_type"),
            "agent_owner_open_id": agent_identity.get("agent_owner_open_id"),
            "agent_owner_display_name": agent_identity.get("agent_owner_display_name"),
            "shared_business_tool_count": agent_identity.get("shared_business_tool_count"),
            "actor_context": actor_context,
            "data_access_scope": actor_context.get("data_access_scope"),
            "personal_owner_open_id": actor_context.get("personal_owner_open_id"),
            "company_data_allowed": actor_context.get("company_data_allowed"),
            "cross_user_data_allowed": actor_context.get("cross_user_data_allowed"),
            "reply_mode": trace_payload.get("reply_mode"),
            "step_count": len(result.trace.steps),
            "planner_enabled": bool(agent_settings.get("planner_enabled")),
            "max_planner_steps": int(agent_settings.get("max_planner_steps") or 3),
            "allow_write_tools": bool(agent_settings.get("allow_write_tools")),
            "require_write_confirmation": bool(agent_settings.get("require_write_confirmation")),
            "chat_id": chat_id,
            **write_policy_summary,
        },
    )
    db.commit()
    return {"company_id": str(company_id), **payload}


def list_agent_trace_logs(
    db: Session,
    *,
    company_id: UUID,
    route_path: str | None = None,
    agent_id: str | None = None,
    agent_owner_open_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    bounded_limit = min(max(limit, 1), 200)
    query = (
        select(AuditLog)
        .where(AuditLog.company_id == company_id)
        .where(AuditLog.target_type == "agent_trace")
        .order_by(AuditLog.created_at.desc())
    )
    if route_path:
        query = query.where(AuditLog.target_id == route_path)
    needs_payload_filter = bool(agent_id or agent_owner_open_id)
    query = query.limit(min(bounded_limit * 5, 1000) if needs_payload_filter else bounded_limit)
    items = [agent_trace_payload(item) for item in db.scalars(query).all()]
    if agent_id:
        items = [item for item in items if str(item.get("agent_id") or "").lower() == agent_id.lower()]
    if agent_owner_open_id:
        items = [
            item
            for item in items
            if str(item.get("agent_owner_open_id") or "").lower() == agent_owner_open_id.lower()
        ]
    items = items[:bounded_limit]
    return {"items": items, "counts": _count_by(items, "route_path")}


def read_agent_settings(db: Session, *, company_id: UUID) -> dict[str, Any]:
    return get_company_agent_settings(db, company_id)


def save_agent_settings(
    db: Session,
    *,
    company_id: UUID,
    updates: dict[str, Any],
) -> dict[str, Any]:
    result = update_company_agent_settings(db, company_id, updates)
    write_audit_log(
        db,
        action="agent.settings.update",
        company_id=company_id,
        actor="admin_console",
        target_type="agent_settings",
        target_id=str(company_id),
        payload={"status": "success", "updated_fields": sorted(updates)},
    )
    db.commit()
    return result


def agent_trace_payload(item: AuditLog) -> dict[str, Any]:
    payload = item.payload or {}
    reply_mode = payload.get("reply_mode") if isinstance(payload.get("reply_mode"), dict) else {}
    return {
        "id": str(item.id),
        "company_id": str(item.company_id) if item.company_id else None,
        "actor": item.actor,
        "route_path": payload.get("route_path") or item.target_id,
        "route_scope": payload.get("route_scope"),
        "route_reason": payload.get("route_reason"),
        "agent_identity": payload.get("agent_identity") if isinstance(payload.get("agent_identity"), dict) else None,
        "agent_id": payload.get("agent_id"),
        "agent_type": payload.get("agent_type"),
        "agent_owner_open_id": payload.get("agent_owner_open_id"),
        "agent_owner_display_name": payload.get("agent_owner_display_name"),
        "shared_business_tool_count": payload.get("shared_business_tool_count"),
        "actor_context": payload.get("actor_context") if isinstance(payload.get("actor_context"), dict) else None,
        "data_access_scope": payload.get("data_access_scope"),
        "personal_owner_open_id": payload.get("personal_owner_open_id"),
        "company_data_allowed": payload.get("company_data_allowed"),
        "cross_user_data_allowed": payload.get("cross_user_data_allowed"),
        "semantic_intent": payload.get("semantic_intent"),
        "reply_mode": reply_mode or None,
        "reply_mode_id": reply_mode.get("mode_id"),
        "reply_mode_label": reply_mode.get("label"),
        "reply_mode_data_requirement": reply_mode.get("data_requirement"),
        "reply_mode_enterprise_data_required": reply_mode.get("enterprise_data_required"),
        "reply_mode_pre_reply_required": reply_mode.get("pre_reply_required"),
        "reply_mode_tool_strategy": reply_mode.get("tool_strategy"),
        "step_count": payload.get("step_count"),
        "supports_write": bool(payload.get("supports_write")),
        "requires_dry_run": bool(payload.get("requires_dry_run")),
        "write_policy": payload.get("write_policy"),
        "confirmed_execution_requires": payload.get("confirmed_execution_requires") or [],
        "chat_id": payload.get("chat_id"),
        "status": payload.get("status"),
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def agent_trace_write_policy_summary(trace: dict[str, Any]) -> dict[str, Any]:
    steps = trace.get("steps") if isinstance(trace.get("steps"), list) else []
    for step in steps:
        if not isinstance(step, dict) or step.get("kind") != "tool":
            continue
        metadata = step.get("metadata") if isinstance(step.get("metadata"), dict) else {}
        if not metadata.get("supports_write"):
            continue
        return {
            "supports_write": True,
            "requires_dry_run": bool(metadata.get("requires_dry_run")),
            "write_policy": metadata.get("write_policy") or metadata.get("policy_reason"),
            "confirmed_execution_requires": metadata.get("confirmed_execution_requires") or [],
        }
    return {
        "supports_write": False,
        "requires_dry_run": False,
        "write_policy": None,
        "confirmed_execution_requires": [],
    }


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts
