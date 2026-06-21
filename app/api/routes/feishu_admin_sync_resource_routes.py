from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin_api_token
from app.api.routes.feishu_admin_sync_request_models import FeishuResourceDiscoverRequest
from app.db.session import get_db
from app.services.feishu import get_feishu_app_or_404
from app.services.feishu_admin_sync import discover_app_resources_payload

router = APIRouter()


@router.post("/apps/{app_config_id}/resources/discover", dependencies=[Depends(require_admin_api_token)])
async def discover_app_resources(
    app_config_id: UUID,
    data: FeishuResourceDiscoverRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    app_config = get_feishu_app_or_404(db, app_config_id)
    return await discover_app_resources_payload(db, app_config, data)
