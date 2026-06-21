from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.routes.v5_resource_request_models import ResourceSyncPolicyUpdate
from app.db.session import get_db
from app.services.v5_sync_policy import update_v5_resource_sync_policy_payload

router = APIRouter()


@router.put("/resources/sync-policy")
def save_resource_sync_policy(data: ResourceSyncPolicyUpdate, db: Session = Depends(get_db)) -> dict[str, Any]:
    return update_v5_resource_sync_policy_payload(
        db,
        company_id=data.company_id,
        data=data.model_dump(exclude={"company_id"}),
    )
