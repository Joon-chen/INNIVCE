from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.v5_resource_status import list_resource_sync_status

router = APIRouter()


@router.get("/resources/sync-status")
def resource_sync_status(company_id: UUID | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    return list_resource_sync_status(db, company_id=company_id)
