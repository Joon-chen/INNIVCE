from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin_api_token
from app.api.routes.feishu_admin_read_tool_request_models import FeishuBitableTablesRequest
from app.db.session import get_db
from app.services.feishu import get_feishu_app_or_404
from app.services.feishu_admin_read_tools import cli_offset_from_page_token, execute_admin_feishu_read_tool

router = APIRouter()


@router.post("/apps/{app_config_id}/bitable/tables", dependencies=[Depends(require_admin_api_token)])
async def list_bitable_tables(
    app_config_id: UUID,
    data: FeishuBitableTablesRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    app_config = get_feishu_app_or_404(db, app_config_id)
    return execute_admin_feishu_read_tool(
        db,
        app_config,
        tool_name="bitable_qa",
        question="管理后台读取飞书多维表格数据表",
        domains=("bitable",),
        params={
            "app_token": data.app_token,
            "page_size": data.page_size,
            "offset": cli_offset_from_page_token(data.page_token, endpoint="bitable/tables"),
        },
    )
