from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import AuditLog
from app.services.system_logs import (
    filtered_system_log_overview_from_audit_logs,
    system_log_overview_from_audit_logs,
)


def system_logs_overview_for_company(
    db: Session,
    *,
    company_id: UUID | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    bounded_limit = _bounded_log_limit(limit)
    query = select(AuditLog).order_by(AuditLog.created_at.desc())
    if company_id:
        query = query.where(AuditLog.company_id == company_id)
    query = query.limit(bounded_limit)
    return system_log_overview_from_audit_logs(list(db.scalars(query).all()))


def list_system_log_items(
    db: Session,
    *,
    company_id: UUID | None = None,
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
    action: str | None = None,
    target_type: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    bounded_limit = _bounded_log_limit(limit)
    query = select(AuditLog).order_by(AuditLog.created_at.desc())
    if company_id:
        query = query.where(AuditLog.company_id == company_id)
    if action:
        query = query.where(AuditLog.action.contains(action.strip()))
    if target_type:
        query = query.where(AuditLog.target_type == target_type.strip())

    needs_payload_filter = (
        category
        or severity
        or status
        or reason
        or confirmed is not None
        or confirmation_token_checked is not None
        or used_agent_runtime is not None
        or final_answer_owner
        or route_path
        or agent_id
        or agent_owner_open_id
    )
    query = query.limit(min(bounded_limit * 5, 1000) if needs_payload_filter else bounded_limit)
    result = filtered_system_log_overview_from_audit_logs(
        list(db.scalars(query).all()),
        category=category.strip() if category else None,
        severity=severity.strip() if severity else None,
        status=status.strip() if status else None,
        reason=reason.strip() if reason else None,
        confirmed=confirmed,
        confirmation_token_checked=confirmation_token_checked,
        used_agent_runtime=used_agent_runtime,
        final_answer_owner=final_answer_owner.strip() if final_answer_owner else None,
        route_path=route_path.strip() if route_path else None,
        agent_id=agent_id.strip() if agent_id else None,
        agent_owner_open_id=agent_owner_open_id.strip() if agent_owner_open_id else None,
    )
    result["items"] = result["items"][:bounded_limit]
    return result


def _bounded_log_limit(limit: int) -> int:
    return min(max(limit, 1), 300)
