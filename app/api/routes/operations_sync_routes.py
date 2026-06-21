from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.operations_read_models import list_sync_run_payloads

router = APIRouter()


@router.get("/sync-runs")
def list_sync_runs(
    company_id: UUID | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return list_sync_run_payloads(db, company_id=company_id, limit=limit)
