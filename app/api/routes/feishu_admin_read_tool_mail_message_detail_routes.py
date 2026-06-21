from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin_api_token
from app.api.routes.feishu_admin_read_tool_request_models import FeishuMailMessageDetailRequest
from app.db.session import get_db
from app.services.feishu import get_feishu_app_or_404
from app.services.feishu_admin_read_tools import execute_admin_feishu_read_tool

router = APIRouter()


@router.post("/apps/{app_config_id}/mail/message-detail", dependencies=[Depends(require_admin_api_token)])
async def get_mail_message_detail(
    app_config_id: UUID,
    data: FeishuMailMessageDetailRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    app_config = get_feishu_app_or_404(db, app_config_id)
    return execute_admin_feishu_read_tool(
        db,
        app_config,
        tool_name="feishu_mail_message_get",
        question="管理后台读取飞书邮箱邮件详情",
        domains=("mail",),
        params={
            "user_mailbox_id": data.user_mailbox_id,
            "message_id": data.message_id,
            "html": data.format not in {"plain_text", "plain_text_full"},
        },
    )
