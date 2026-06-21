from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin_api_token
from app.api.routes.feishu_admin_write_request_models import FeishuSendMessageRequest
from app.db.session import get_db
from app.services.feishu import get_feishu_app_or_404
from app.services.feishu_admin_write_tools import send_message_payload

router = APIRouter()


@router.post("/apps/{app_config_id}/bot/send", dependencies=[Depends(require_admin_api_token)])
async def send_bot_message(
    app_config_id: UUID,
    data: FeishuSendMessageRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    app_config = get_feishu_app_or_404(db, app_config_id)
    return send_message_payload(db, app_config, data)
