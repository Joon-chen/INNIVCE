from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.routes.work_events_request_models import DailyReportRequest
from app.db.session import get_db
from app.services.work_events_admin import daily_report_payload

router = APIRouter()


@router.post("/reports/daily")
def daily_report(data: DailyReportRequest, db: Session = Depends(get_db)) -> dict:
    return daily_report_payload(db, data)
