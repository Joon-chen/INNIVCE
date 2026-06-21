from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.routes.v5_resource_request_models import ResourceBatchSyncRequest
from app.db.session import get_db
from app.services.v5_auto_sync import preview_company_resource_batch_sync

router = APIRouter()


@router.post("/resources/sync-preview")
def preview_sync_resources(data: ResourceBatchSyncRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    return preview_company_resource_batch_sync(
        db,
        company_id=data.company_id,
        resource_type=data.resource_type,
        statuses=data.statuses,
        limit_resources=data.limit_resources,
        large_document_mode=data.large_document_mode,
        bitable_mode=data.bitable_mode,
        memory_mode=data.memory_mode,
        vector_mode=data.vector_mode,
    )
