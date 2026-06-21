from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.work_events_admin import work_event_detail_payload

router = APIRouter()


@router.get("/work-events/{event_id}")
def get_work_event_detail(event_id: UUID, db: Session = Depends(get_db)) -> dict:
    return work_event_detail_payload(db, event_id)
