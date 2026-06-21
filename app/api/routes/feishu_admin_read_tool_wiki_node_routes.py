from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin_api_token
from app.api.routes.feishu_admin_read_tool_request_models import FeishuWikiNodesRequest
from app.db.session import get_db
from app.services.feishu import get_feishu_app_or_404
from app.services.feishu_admin_read_tools import execute_admin_feishu_read_tool

router = APIRouter()


@router.post("/apps/{app_config_id}/wiki/nodes", dependencies=[Depends(require_admin_api_token)])
async def list_wiki_nodes(
    app_config_id: UUID,
    data: FeishuWikiNodesRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    app_config = get_feishu_app_or_404(db, app_config_id)
    return execute_admin_feishu_read_tool(
        db,
        app_config,
        tool_name="feishu_wiki_node_list",
        question="管理后台读取飞书知识库节点",
        domains=("knowledge",),
        params={
            "space_id": data.space_id,
            "parent_node_token": data.parent_node_token,
            "page_size": data.page_size,
            "page_token": data.page_token,
        },
    )
