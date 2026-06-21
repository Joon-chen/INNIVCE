from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.security import redact_payload
from app.models.entities import AuditLog


def write_audit_log(
    db: Session,
    *,
    action: str,
    company_id: UUID | None = None,
    actor: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> AuditLog:
    audit = AuditLog(
        company_id=company_id,
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        payload=redact_payload(payload or {}),
    )
    db.add(audit)
    return audit
