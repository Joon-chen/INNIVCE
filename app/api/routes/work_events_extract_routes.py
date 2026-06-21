from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.work_events_admin import extract_event_items_payload

router = APIRouter()


@router.post("/work-events/{event_id}/extract")
def extract_event_items(event_id: UUID, db: Session = Depends(get_db)) -> dict:
    return extract_event_items_payload(db, event_id)
