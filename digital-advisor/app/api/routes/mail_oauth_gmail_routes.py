from fastapi import APIRouter

from app.services.mail_admin import gmail_oauth_url_payload

router = APIRouter()


@router.get("/gmail/oauth-url")
def gmail_oauth_url(state: str = "local") -> dict[str, str]:
    return gmail_oauth_url_payload(state=state)
