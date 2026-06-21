from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.v5_resource_status import list_workspace_event_payloads

router = APIRouter()


@router.get("/workspace/events")
def list_workspace_events(
    company_id: UUID | None = None,
    resource_id: UUID | None = None,
    limit: int = 80,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return list_workspace_event_payloads(db, company_id=company_id, resource_id=resource_id, limit=limit)
