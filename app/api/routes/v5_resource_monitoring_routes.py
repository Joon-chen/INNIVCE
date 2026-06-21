from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.v5_resource_status import resource_monitoring_summary

router = APIRouter()


@router.get("/resources/monitoring")
def resource_monitoring(company_id: UUID | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    return resource_monitoring_summary(db, company_id=company_id)
