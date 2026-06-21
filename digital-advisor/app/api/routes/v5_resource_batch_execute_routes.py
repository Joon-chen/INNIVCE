from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.routes.v5_resource_request_models import ResourceBatchSyncRequest
from app.db.session import get_db
from app.services.v5_auto_sync import sync_company_resource_batch

router = APIRouter()


@router.post("/resources/sync")
async def sync_resources(data: ResourceBatchSyncRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    return await sync_company_resource_batch(
        db,
        company_id=data.company_id,
        resource_type=data.resource_type,
        statuses=data.statuses,
        limit_resources=data.limit_resources,
        limit=data.limit,
        max_pages=data.max_pages,
        extract_items=data.extract_items,
        start_time=data.start_time,
        end_time=data.end_time,
        large_document_mode=data.large_document_mode,
        bitable_mode=data.bitable_mode,
        memory_mode=data.memory_mode,
        vector_mode=data.vector_mode,
    )
