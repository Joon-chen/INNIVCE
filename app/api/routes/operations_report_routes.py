from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.operations_read_models import list_report_payloads

router = APIRouter()


@router.get("/reports")
def list_reports(
    company_id: UUID | None = None,
    report_type: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return list_report_payloads(db, company_id=company_id, report_type=report_type, limit=limit)
