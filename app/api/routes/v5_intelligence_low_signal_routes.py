from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.v5_intelligence_admin import close_low_signal_notice_noise_items

router = APIRouter()


@router.post("/intelligence/noise/close-low-signal-notices")
def close_low_signal_notice_noise(
    company_id: UUID | None = None,
    limit: int = 500,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return close_low_signal_notice_noise_items(db, company_id=company_id, limit=limit)
