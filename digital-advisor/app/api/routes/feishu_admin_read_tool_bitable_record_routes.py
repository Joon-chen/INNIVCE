from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin_api_token
from app.api.routes.feishu_admin_read_tool_request_models import FeishuBitableRecordsRequest
from app.db.session import get_db
from app.services.feishu import get_feishu_app_or_404
from app.services.feishu_admin_read_tools import cli_offset_from_page_token, execute_admin_feishu_read_tool

router = APIRouter()


@router.post("/apps/{app_config_id}/bitable/records", dependencies=[Depends(require_admin_api_token)])
async def list_bitable_records(
    app_config_id: UUID,
    data: FeishuBitableRecordsRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    app_config = get_feishu_app_or_404(db, app_config_id)
    return execute_admin_feishu_read_tool(
        db,
        app_config,
        tool_name="bitable_qa",
        question="管理后台读取飞书多维表格记录",
        domains=("bitable",),
        params={
            "app_token": data.app_token,
            "table_id": data.table_id,
            "page_size": data.page_size,
            "offset": cli_offset_from_page_token(data.page_token, endpoint="bitable/records"),
            "view_id": data.view_id,
            "field_names": data.field_names,
        },
    )
