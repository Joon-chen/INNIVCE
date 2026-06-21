from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.v5_resource_status import list_v5_resources

router = APIRouter()


@router.get("/resources")
def list_resources(company_id: UUID | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    return list_v5_resources(db, company_id=company_id)
