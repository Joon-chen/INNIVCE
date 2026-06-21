from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import WorkEvent
from app.schemas.common import WorkEventOut
from app.services.work_events_admin import list_work_event_payloads

router = APIRouter()


@router.get("/work-events", response_model=list[WorkEventOut])
def list_work_events(
    company_id: UUID | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> list[WorkEvent]:
    return list_work_event_payloads(db, company_id=company_id, limit=limit)
