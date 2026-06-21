from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.v5_intelligence_admin import close_finance_document_risk_noise_items

router = APIRouter()


@router.post("/intelligence/risk-noise/close-finance-documents")
def close_finance_document_risk_noise(
    company_id: UUID | None = None,
    limit: int = 500,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return close_finance_document_risk_noise_items(db, company_id=company_id, limit=limit)
