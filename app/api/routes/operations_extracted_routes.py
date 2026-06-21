from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.operations_read_models import list_extracted_item_payloads

router = APIRouter()


@router.get("/extracted-items")
def list_extracted_items(
    company_id: UUID | None = None,
    item_type: str | None = None,
    status: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return list_extracted_item_payloads(db, company_id=company_id, item_type=item_type, status=status, limit=limit)
