from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.v5_administration import bootstrap_v5_administration

router = APIRouter()


@router.post("/bootstrap/foundation")
def bootstrap_foundation(
    company_id: UUID | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    result = bootstrap_v5_administration(db, company_id=company_id)
    return {
        "status": "ok",
        "version": "v5",
        "message": "V5 Administration foundation initialized.",
        "created": result.as_dict(),
    }
