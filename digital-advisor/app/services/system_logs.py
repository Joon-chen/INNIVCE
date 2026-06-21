from typing import Any

from app.models.entities import AuditLog
from app.services.tools.write_audit import write_target_summary


def system_log_overview_from_audit_logs(items: list[AuditLog]) -> dict[str, Any]:
    payloads = [system_log_item_payload(item) for item in items]
    return system_log_overview_from_payloads(payloads)


def filtered_system_log_overview_from_audit_logs(
    items: list[AuditLog],
    *,
    category: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    reason: str | None = None,
    confirmed: bool | None = None,
    confirmation_token_checked: bool | None = None,
    used_agent_runtime: bool | None = None,
    final_answer_owner: str | None = None,
    route_path: str | None = None,
    agent_id: str | None = None,
    agent_owner_open_id: str | None = None,
) -> dict[str, Any]:
    payloads = [system_log_item_payload(item) for item in items]
    if category:
        payloads = [item for item in payloads if item["category"] == category]
    if severity:
        payloads = [item for item in payloads if item["severity"] == severity]
    if status:
        payloads = [item for item in payloads if str(item.get("status") or "").lower() == status.lower()]
    if reason:
        payloads = [item for item in payloads if str(item.get("reason") or "").lower() == reason.lower()]
    if confirmed is not None:
        payloads = [item for item in payloads if item.get("confirmed") is confirmed]
    if confirmation_token_checked is not None:
        payloads = [
            item for item in payloads if item.get("confirmation_token_checked") is confirmation_token_checked
        ]
    if used_agent_runtime is not None:
        payloads = [item for item in payloads if item.get("used_agent_runtime") is used_agent_runtime]
    if final_answer_owner:
        payloads = [item for item in payloads if str(item.get("final_answer_owner") or "").lower() == final_answer_owner.lower()]
    if route_path:
        payloads = [item for item in payloads if str(item.get("route_path") or "").lower() == route_path.lower()]
    if agent_id:
        payloads = [item for item in payloads if str(item.get("agent_id") or "").lower() == agent_id.lower()]
    if agent_owner_open_id:
        payloads = [
            item
            for item in payloads
            if str(item.get("agent_owner_open_id") or "").lower() == agent_owner_open_id.lower()
        ]
    return system_log_overview_from_payloads(payloads)


def system_log_overview_from_payloads(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "items": payloads,
        "counts": {
            "total": len(payloads),
            "errors": sum(1 for item in payloads if item["severity"] == "error"),
            "warnings": sum(1 for item in payloads if item["severity"] == "warning"),
            "tools": sum(1 for item in payloads if item["category"] == "tool"),
            "agent": sum(1 for item in payloads if item["category"] == "agent"),
            "feishu": sum(1 for item in payloads if item["category"] == "feishu"),
            "sync": sum(1 for item in payloads if item["category"] == "sync"),
            "approval": sum(1 for item in payloads if item["category"] == "approval"),
            "gateway": sum(1 for item in payloads if item["category"] == "gateway"),
            "agent_runtime_gateway": sum(
                1 for item in payloads if item["category"] == "gateway" and item.get("used_agent_runtime") is True
            ),
            "report": sum(1 for item in payloads if item["category"] == "report"),
            "workspace": sum(1 for item in payloads if item["category"] == "workspace"),
            "administration": sum(1 for item in payloads if item["category"] == "administration"),
            "system": sum(1 for item in payloads if item["category"] == "system"),
        },
        "category_counts": _count_by(payloads, "category"),
        "severity_counts": _count_by(payloads, "severity"),
        "latest_errors": [item for item in payloads if item["severity"] == "error"][:10],
    }


def system_log_item_payload(item: AuditLog) -> dict[str, Any]:
    payload = item.payload or {}
    reply_mode = _reply_mode_from_payload(payload)
    agent_identity = _agent_identity_from_payload(payload)
    category = _log_category(item.action, item.target_type)
    severity = _log_severity(item.action, payload)
    return {
        "id": str(item.id),
        "company_id": str(item.company_id) if item.company_id else None,
        "category": category,
        "severity": severity,
        "actor": item.actor,
        "action": item.action,
        "target_type": item.target_type,
        "target_id": item.target_id,
        "status": payload.get("status"),
        "reason": payload.get("reason"),
        "provider": payload.get("provider"),
        "preferred_execution_engine": payload.get("preferred_execution_engine"),
        "realtime_policy": payload.get("realtime_policy"),
        "realtime_bridge": payload.get("realtime_bridge"),
        "api_role": payload.get("api_role"),
        "execution_chain": payload.get("execution_chain"),
        "gateway_chain": payload.get("gateway_chain"),
        "agent_runtime_direct_access": payload.get("agent_runtime_direct_access"),
        "used_agent_runtime": payload.get("used_agent_runtime"),
        "final_answer_owner": payload.get("final_answer_owner"),
        "route_path": payload.get("route_path"),
        "route_label": payload.get("route_label"),
        "agent_identity": agent_identity or None,
        "agent_id": agent_identity.get("agent_id") if agent_identity else None,
        "agent_type": agent_identity.get("agent_type") if agent_identity else None,
        "agent_owner_open_id": agent_identity.get("agent_owner_open_id") if agent_identity else None,
        "agent_owner_display_name": agent_identity.get("agent_owner_display_name") if agent_identity else None,
        "shared_business_tool_count": agent_identity.get("shared_business_tool_count") if agent_identity else None,
        "data_permission_model": agent_identity.get("data_permission_model") if agent_identity else None,
        "reply_mode": reply_mode or None,
        "reply_mode_id": reply_mode.get("mode_id") if reply_mode else None,
        "reply_mode_label": reply_mode.get("label") if reply_mode else None,
        "reply_mode_data_requirement": reply_mode.get("data_requirement") if reply_mode else None,
        "reply_mode_enterprise_data_required": reply_mode.get("enterprise_data_required") if reply_mode else None,
        "reply_mode_pre_reply_required": reply_mode.get("pre_reply_required") if reply_mode else None,
        "reply_mode_tool_strategy": reply_mode.get("tool_strategy") if reply_mode else None,
        "agent_runtime_trace": payload.get("agent_runtime_trace"),
        "agent_runtime_step_count": payload.get("agent_runtime_step_count"),
        "agent_runtime_tool_steps": payload.get("agent_runtime_tool_steps"),
        "agent_runtime_workflow_steps": payload.get("agent_runtime_workflow_steps"),
        "sync_engine_direct_api_allowed": payload.get("sync_engine_direct_api_allowed"),
        "sync_engine_mcp_access_allowed": payload.get("sync_engine_mcp_access_allowed"),
        "confirmed": payload.get("confirmed"),
        "confirmation_token_checked": payload.get("confirmation_token_checked"),
        "expected_action": payload.get("expected_action"),
        "write_target": payload.get("write_target"),
        "write_target_summary": write_target_summary(payload.get("write_target")),
        "error": payload.get("error") or payload.get("error_message"),
        "summary": _log_summary(item.action, item.target_type, payload),
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _agent_identity_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    direct = payload.get("agent_identity")
    if isinstance(direct, dict):
        return direct
    trace = payload.get("agent_runtime_trace")
    if isinstance(trace, dict) and isinstance(trace.get("agent_identity"), dict):
        return trace["agent_identity"]
    return {}


def _reply_mode_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    direct = payload.get("reply_mode")
    if isinstance(direct, dict):
        return direct
    trace = payload.get("agent_runtime_trace")
    if isinstance(trace, dict) and isinstance(trace.get("reply_mode"), dict):
        return trace["reply_mode"]
    return {}


def _log_category(action: str, target_type: str | None) -> str:
    if action.startswith("gateway."):
        return "gateway"
    if action.startswith("agent."):
        return "agent"
    if action.startswith("tool."):
        return "tool"
    if action.startswith("feishu."):
        return "feishu"
    if action.startswith("approval."):
        return "approval"
    if action.startswith("report."):
        return "report"
    if action.startswith("work_event."):
        return "workspace"
    if target_type in {"resource", "resource_sync_run"} or "sync" in action:
        return "sync"
    if target_type in {"company", "account", "feishu_app"}:
        return "administration"
    return "system"


def _log_severity(action: str, payload: dict[str, Any]) -> str:
    status = str(payload.get("status") or "").lower()
    reason = str(payload.get("reason") or "").lower()
    if action.startswith("gateway.") and reason == "unhandled_card_action":
        return "warning"
    if status in {"error", "failed", "denied"} or payload.get("error") or payload.get("error_message"):
        return "error" if status != "denied" else "warning"
    if "failed" in action or "error" in action:
        return "error"
    if status in {"partial", "skipped", "warning"}:
        return "warning"
    return "info"


def _log_summary(action: str, target_type: str | None, payload: dict[str, Any]) -> str:
    status = payload.get("status")
    provider = payload.get("provider")
    error = payload.get("error") or payload.get("error_message")
    parts = [action]
    if target_type:
        parts.append(f"target={target_type}")
    if provider:
        parts.append(f"provider={provider}")
    if status:
        parts.append(f"status={status}")
    if payload.get("used_agent_runtime") is True:
        parts.append("agent_runtime=true")
    if payload.get("final_answer_owner"):
        parts.append(f"final_answer_owner={payload.get('final_answer_owner')}")
    if payload.get("route_path"):
        parts.append(f"route={payload.get('route_path')}")
    if payload.get("agent_runtime_step_count") is not None:
        parts.append(f"agent_steps={payload.get('agent_runtime_step_count')}")
    if payload.get("confirmation_token_checked") is True:
        parts.append("confirmation_token_checked=true")
    if payload.get("confirmed") is True:
        parts.append("confirmed=true")
    if payload.get("expected_action"):
        parts.append(f"expected_action={payload.get('expected_action')}")
    target_summary = write_target_summary(payload.get("write_target"))
    if target_summary:
        parts.append(f"write_target={target_summary}")
    if error:
        parts.append(f"error={error}")
    return " / ".join(str(part) for part in parts if part)


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts
