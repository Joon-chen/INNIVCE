from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.routes.user_identity_oauth_feishu_cli_request_models import FeishuCliUserAuthCompleteRequest
from app.db.session import get_db
from app.services.feishu_cli_user_auth import complete_lark_cli_user_authorization
from app.services.user_identity_authorizations import (
    default_feishu_app_for_user_identity,
    mark_user_identity_authorization,
)

router = APIRouter()


@router.post("/feishu/cli/complete")
def complete_feishu_cli_user_identity_auth(
    data: FeishuCliUserAuthCompleteRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    app_config = default_feishu_app_for_user_identity(db, company_id=data.company_id)
    payload = complete_lark_cli_user_authorization(
        app_config,
        device_code=data.device_code,
        expected_owner_open_id=data.open_id,
    )
    mark_user_identity_authorization(
        db,
        company_id=data.company_id,
        open_id=data.open_id,
        resource_type="personal_feishu",
        status="authorized",
        provider="feishu_cli",
        owner_open_id=data.open_id,
        metadata={
            "cli_profile": payload.get("cli_profile"),
            "identity": payload.get("identity"),
            "identity_open_id": payload.get("identity_open_id"),
            "identity_match": payload.get("identity_match"),
            "split_flow": True,
        },
    )
    return payload
