from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.v5_administration import list_administration_company_settings

router = APIRouter()


@router.get("/administration/company-settings")
def list_company_settings(
    company_id: UUID | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return list_administration_company_settings(db, company_id=company_id)
