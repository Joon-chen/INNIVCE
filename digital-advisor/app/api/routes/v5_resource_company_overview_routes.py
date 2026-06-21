from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.v5_resource_status import resource_company_overview as resource_company_overview_service

router = APIRouter()


@router.get("/resources/company-overview")
def resource_company_overview(company_id: UUID | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    return resource_company_overview_service(db, company_id=company_id)
