from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.feishu_admin_apps import feishu_user_oauth_url_payload
from app.services.user_identity_authorizations import build_user_identity_oauth_state, default_feishu_app_for_user_identity

router = APIRouter()


@router.get("/feishu/start")
def start_feishu_user_identity_oauth(
    company_id: UUID,
    open_id: str,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    app_config = default_feishu_app_for_user_identity(db, company_id=company_id)
    oauth_state = build_user_identity_oauth_state(
        company_id=company_id,
        open_id=open_id,
        resource_type="personal_feishu",
        provider="feishu",
        app_config_id=app_config.id,
    )
    payload = feishu_user_oauth_url_payload(app_config, oauth_state)
    return RedirectResponse(payload["oauth_url"])
