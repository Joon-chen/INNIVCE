from typing import Any

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.api.routes.portal_request_models import PortalApprovalDetailRequest
from app.db.session import get_db
from app.services.portal_runtime import portal_approval_detail_payload


router = APIRouter()


@router.post("/api/portal/approvals/detail")
def portal_approval_detail(
    data: PortalApprovalDetailRequest,
    x_admin_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return portal_approval_detail_payload(db, data, x_admin_token)
