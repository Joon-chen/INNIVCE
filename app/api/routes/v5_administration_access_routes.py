from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.routes.v5_administration_request_models import AccessPreviewRequest
from app.db.session import get_db
from app.services.v5_administration import preview_administration_resource_access

router = APIRouter()


@router.post("/administration/access-preview")
def preview_resource_access(data: AccessPreviewRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    return preview_administration_resource_access(
        db,
        company_id=data.company_id,
        resource_id=data.resource_id,
        user_id=data.user_id,
        open_id=data.open_id,
        role=data.role,
        domains=data.domains,
        current_chat_id=data.current_chat_id,
    )
