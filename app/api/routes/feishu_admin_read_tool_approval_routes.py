from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin_api_token
from app.api.routes.feishu_admin_read_tool_request_models import FeishuApprovalPendingRequest
from app.db.session import get_db
from app.services.feishu import get_feishu_app_or_404
from app.services.feishu_admin_read_tools import pending_approval_tasks_payload

router = APIRouter()


@router.post("/apps/{app_config_id}/approvals/pending", dependencies=[Depends(require_admin_api_token)])
async def list_pending_approval_tasks(
    app_config_id: UUID,
    data: FeishuApprovalPendingRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    app_config = get_feishu_app_or_404(db, app_config_id)
    return pending_approval_tasks_payload(db, app_config, open_id=data.open_id, limit=data.limit)
