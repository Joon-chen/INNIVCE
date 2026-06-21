from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.routes.v5_resource_request_models import ResourceAccessDecisionRequest
from app.db.session import get_db
from app.services.v5_workspace import update_v5_resource_access_decision

router = APIRouter()


@router.post("/resources/{resource_id}/access-decision")
def update_resource_access_decision(
    resource_id: UUID,
    data: ResourceAccessDecisionRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return update_v5_resource_access_decision(db, resource_id=resource_id, decision=data.decision, note=data.note)
