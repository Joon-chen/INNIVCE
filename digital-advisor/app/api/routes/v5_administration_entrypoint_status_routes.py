from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.v5_administration import v5_entrypoint_status

router = APIRouter()


@router.get("/entrypoints/status")
def entrypoints_status(
    company_id: UUID | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return v5_entrypoint_status(db, company_id=company_id)
