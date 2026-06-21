from typing import Any

from fastapi import APIRouter, Header

from app.api.routes.portal_request_models import PortalPendingApprovalsRequest
from app.services.portal_runtime import portal_cached_approvals_payload


router = APIRouter()


@router.post("/api/portal/approvals/cached")
def portal_cached_approvals(
    data: PortalPendingApprovalsRequest,
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    return portal_cached_approvals_payload(data, x_admin_token)
