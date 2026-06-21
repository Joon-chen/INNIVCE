from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import WorkEvent
from app.schemas.common import WorkEventCreate, WorkEventOut
from app.services.work_events_admin import create_work_event_payload

router = APIRouter()


@router.post("/work-events", response_model=WorkEventOut)
def create_work_event(data: WorkEventCreate, db: Session = Depends(get_db)) -> WorkEvent:
    return create_work_event_payload(db, data)
