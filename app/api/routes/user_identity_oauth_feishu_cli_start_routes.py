from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.routes.user_identity_oauth_feishu_cli_request_models import FeishuCliUserAuthStartRequest
from app.db.session import get_db
from app.services.feishu_cli_user_auth import start_lark_cli_user_authorization
from app.services.user_identity_authorizations import (
    default_feishu_app_for_user_identity,
    mark_user_identity_authorization,
)

router = APIRouter()


@router.post("/feishu/cli/start")
def start_feishu_cli_user_identity_auth(
    data: FeishuCliUserAuthStartRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    app_config = default_feishu_app_for_user_identity(db, company_id=data.company_id)
    payload = start_lark_cli_user_authorization(app_config, domains=data.domains)
    mark_user_identity_authorization(
        db,
        company_id=data.company_id,
        open_id=data.open_id,
        resource_type="personal_feishu",
        status="authorization_started",
        provider="feishu_cli",
        owner_open_id=data.open_id,
        metadata={
            "cli_profile": payload.get("cli_profile"),
            "domains": payload.get("domains"),
            "split_flow": True,
        },
    )
    return payload
