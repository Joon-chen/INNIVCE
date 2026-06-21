from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.operations_read_models import list_audit_log_payloads

router = APIRouter()


@router.get("/audit-logs")
def list_audit_logs(
    company_id: UUID | None = None,
    action: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return list_audit_log_payloads(db, company_id=company_id, action=action, limit=limit)
