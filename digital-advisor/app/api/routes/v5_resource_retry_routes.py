from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.routes.v5_resource_request_models import ResourceSyncRequest
from app.db.session import get_db
from app.services.v5_workspace import retry_v5_resource_access_block

router = APIRouter()


@router.post("/resources/{resource_id}/retry-access-block")
async def retry_resource_access_block(
    resource_id: UUID,
    data: ResourceSyncRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return await retry_v5_resource_access_block(
        db,
        resource_id=resource_id,
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
