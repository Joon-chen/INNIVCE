from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import require_admin_api_token

router = APIRouter()


@router.get("/apps/{app_config_id}/oauth-url", dependencies=[Depends(require_admin_api_token)])
def feishu_user_oauth_url(
    app_config_id: UUID,
    state: str | None = None,
) -> dict[str, str]:
    del app_config_id, state
    raise HTTPException(
        status_code=410,
        detail=(
            "Admin OAuth URL generation for personal Feishu accounts is retired. "
            "Use /api/user-identity/oauth/feishu/start with the resource owner's open_id."
        ),
    )
