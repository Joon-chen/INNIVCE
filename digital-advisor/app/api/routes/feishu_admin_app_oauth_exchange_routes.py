from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import require_admin_api_token
from app.api.routes.feishu_admin_app_request_models import FeishuOAuthExchangeRequest

router = APIRouter()


@router.post("/apps/{app_config_id}/oauth/exchange", dependencies=[Depends(require_admin_api_token)])
async def exchange_feishu_user_token(
    app_config_id: UUID,
    data: FeishuOAuthExchangeRequest,
) -> dict[str, Any]:
    del app_config_id, data
    raise HTTPException(
        status_code=410,
        detail=(
            "Admin OAuth exchange for personal Feishu accounts is retired. "
            "Use /api/user-identity/oauth/feishu/start with the resource owner's open_id."
        ),
    )
