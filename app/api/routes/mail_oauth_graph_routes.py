from fastapi import APIRouter

from app.services.mail_admin import graph_oauth_url_payload

router = APIRouter()


@router.get("/graph/oauth-url")
def graph_oauth_url(state: str = "local") -> dict[str, str]:
    return graph_oauth_url_payload(state=state)
