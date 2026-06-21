from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.operations_dashboard import dashboard_overview_payload

router = APIRouter()


@router.get("/dashboard/overview")
def dashboard_overview(db: Session = Depends(get_db)) -> dict[str, Any]:
    return dashboard_overview_payload(db)
