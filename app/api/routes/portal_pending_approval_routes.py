from typing import Any

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.api.routes.portal_request_models import PortalPendingApprovalsRequest
from app.db.session import get_db
from app.services.portal_runtime import portal_pending_approvals_payload


router = APIRouter()


@router.post("/api/portal/approvals/pending")
def portal_pending_approvals(
    data: PortalPendingApprovalsRequest,
    x_admin_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return portal_pending_approvals_payload(db, data, x_admin_token)
