from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin_api_token
from app.api.routes.feishu_admin_read_tool_request_models import FeishuContactSnapshotRequest
from app.db.session import get_db
from app.services.feishu import get_feishu_app_or_404
from app.services.feishu_admin_read_tools import contact_snapshot_payload

router = APIRouter()


@router.post("/apps/{app_config_id}/contacts/snapshot", dependencies=[Depends(require_admin_api_token)])
async def snapshot_contacts(
    app_config_id: UUID,
    data: FeishuContactSnapshotRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    app_config = get_feishu_app_or_404(db, app_config_id)
    return contact_snapshot_payload(db, app_config, data)
