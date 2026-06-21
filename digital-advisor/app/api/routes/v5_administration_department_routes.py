from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.v5_administration import list_administration_departments

router = APIRouter()


@router.get("/administration/departments")
def list_departments(
    company_id: UUID | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return list_administration_departments(db, company_id=company_id)
