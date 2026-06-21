from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.v5_sync_policy import get_v5_resource_sync_policy_payload

router = APIRouter()


@router.get("/resources/sync-policy")
def resource_sync_policy(company_id: UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    return get_v5_resource_sync_policy_payload(db, company_id=company_id)
