from typing import Any

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.api.routes.portal_request_models import PortalApprovalActionRequest
from app.db.session import get_db
from app.services.portal_runtime import portal_approval_action_payload


router = APIRouter()


@router.post("/api/portal/approvals/action")
def portal_approval_action(
    data: PortalApprovalActionRequest,
    x_admin_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return portal_approval_action_payload(db, data, x_admin_token)
