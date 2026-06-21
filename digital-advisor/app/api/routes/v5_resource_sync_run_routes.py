from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.v5_resource_status import list_resource_sync_runs

router = APIRouter()


@router.get("/resources/sync-runs")
def resource_sync_runs(
    company_id: UUID | None = None,
    resource_id: UUID | None = None,
    limit: int = 20,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return list_resource_sync_runs(db, company_id=company_id, resource_id=resource_id, limit=limit)
