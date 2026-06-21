from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from app.core.config import settings
from app.services.integrations.mail import GraphMailService
from app.services.user_identity_authorizations import build_user_identity_oauth_state

router = APIRouter()


@router.get("/mail/graph/start")
def start_graph_user_identity_oauth(company_id: UUID, open_id: str) -> RedirectResponse:
    oauth_state = build_user_identity_oauth_state(
        company_id=company_id,
        open_id=open_id,
        resource_type="external_mail",
        provider="graph",
    )
    redirect_uri = f"{settings.api_base_url.rstrip('/')}/api/user-identity/oauth/mail/graph/callback"
    return RedirectResponse(GraphMailService().build_authorization_url(oauth_state, redirect_uri=redirect_uri))
