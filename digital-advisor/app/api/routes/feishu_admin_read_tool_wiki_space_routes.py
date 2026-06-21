from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin_api_token
from app.api.routes.feishu_admin_read_tool_request_models import FeishuWikiSpacesRequest
from app.db.session import get_db
from app.services.feishu import get_feishu_app_or_404
from app.services.feishu_admin_read_tools import execute_admin_feishu_read_tool

router = APIRouter()


@router.post("/apps/{app_config_id}/wiki/spaces", dependencies=[Depends(require_admin_api_token)])
async def list_wiki_spaces(
    app_config_id: UUID,
    data: FeishuWikiSpacesRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    app_config = get_feishu_app_or_404(db, app_config_id)
    return execute_admin_feishu_read_tool(
        db,
        app_config,
        tool_name="feishu_wiki_space_list",
        question="管理后台读取飞书知识空间",
        domains=("knowledge",),
        params={"page_size": data.page_size, "page_token": data.page_token},
    )
