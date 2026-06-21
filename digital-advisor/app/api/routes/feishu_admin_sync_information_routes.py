from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin_api_token
from app.api.routes.feishu_admin_sync_request_models import FeishuInformationSyncRequest
from app.db.session import get_db
from app.services.feishu import get_feishu_app_or_404
from app.services.feishu_admin_sync import sync_app_information_payload

router = APIRouter()


@router.post("/apps/{app_config_id}/information/sync", dependencies=[Depends(require_admin_api_token)])
async def sync_app_information(
    app_config_id: UUID,
    data: FeishuInformationSyncRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    app_config = get_feishu_app_or_404(db, app_config_id)
    return await sync_app_information_payload(db, app_config, data)
