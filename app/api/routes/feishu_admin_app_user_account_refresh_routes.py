from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import require_admin_api_token

router = APIRouter()


@router.post("/apps/{app_config_id}/user-accounts/{account_id}/refresh", dependencies=[Depends(require_admin_api_token)])
async def refresh_feishu_user_token(
    app_config_id: UUID,
    account_id: UUID,
) -> dict[str, Any]:
    del app_config_id, account_id
    raise HTTPException(
        status_code=410,
        detail=(
            "Admin refresh for personal Feishu accounts is retired. "
            "Personal credentials are refreshed only inside User Identity authorized execution or sync paths."
        ),
    )
