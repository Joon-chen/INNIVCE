from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin_api_token
from app.api.routes.feishu_admin_read_tool_request_models import FeishuDocumentContentRequest
from app.db.session import get_db
from app.services.feishu import get_feishu_app_or_404
from app.services.feishu_admin_read_tools import execute_admin_feishu_read_tool

router = APIRouter()


@router.post("/apps/{app_config_id}/documents/content", dependencies=[Depends(require_admin_api_token)])
async def get_document_content(
    app_config_id: UUID,
    data: FeishuDocumentContentRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    app_config = get_feishu_app_or_404(db, app_config_id)
    return execute_admin_feishu_read_tool(
        db,
        app_config,
        tool_name="feishu_doc_fetch",
        question="管理后台读取飞书文档",
        domains=("knowledge", "doc"),
        params={
            "document_id": data.document_id,
            "document_type": data.document_type,
        },
    )
