from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.v5_system_logs import system_logs_overview_for_company

router = APIRouter()


@router.get("/system/logs/overview")
def system_logs_overview(
    company_id: UUID | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return system_logs_overview_for_company(db, company_id=company_id, limit=limit)
