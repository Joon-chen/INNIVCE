from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.operations_memory import list_memory_facts as list_memory_facts_service

router = APIRouter()


@router.get("/memory-facts")
def list_memory_facts(
    company_id: UUID,
    fact_type: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return list_memory_facts_service(db, company_id=company_id, fact_type=fact_type, limit=limit)
