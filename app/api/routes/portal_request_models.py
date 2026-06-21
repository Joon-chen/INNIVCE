from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.api.routes.feishu_admin_write_request_models import FeishuApprovalActionRequest


class PortalPendingApprovalsRequest(BaseModel):
    app_config_id: UUID
    open_id: str
    chat_id: str | None = None
    limit: int = Field(default=20, ge=1, le=50)


class PortalApprovalActionRequest(FeishuApprovalActionRequest):
    app_config_id: UUID
    chat_id: str | None = None


PortalPayload = dict[str, Any]
