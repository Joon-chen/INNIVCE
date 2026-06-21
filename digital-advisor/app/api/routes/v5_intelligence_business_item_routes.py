from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.v5_intelligence_admin import enrich_open_business_extracted_items as enrich_open_business_extracted_items_service

router = APIRouter()


@router.post("/intelligence/business-items/enrich-open")
def enrich_open_business_extracted_items(
    company_id: UUID | None = None,
    limit: int = 500,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return enrich_open_business_extracted_items_service(db, company_id=company_id, limit=limit)
