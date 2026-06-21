from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin_api_token
from app.api.routes.feishu_admin_read_tool_request_models import FeishuChatSearchRequest
from app.db.session import get_db
from app.services.feishu import get_feishu_app_or_404
from app.services.feishu_admin_read_tools import execute_admin_feishu_read_tool

router = APIRouter()


@router.post("/apps/{app_config_id}/im/chats/search", dependencies=[Depends(require_admin_api_token)])
async def search_im_chats(
    app_config_id: UUID,
    data: FeishuChatSearchRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    app_config = get_feishu_app_or_404(db, app_config_id)
    return execute_admin_feishu_read_tool(
        db,
        app_config,
        tool_name="feishu_im_chat_search",
        question="管理后台搜索飞书群聊",
        domains=("chat",),
        params={
            "query": data.query,
            "page_size": data.limit,
            "max_pages": data.max_pages,
        },
    )
