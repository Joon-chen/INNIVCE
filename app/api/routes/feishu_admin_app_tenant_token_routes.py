from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin_api_token
from app.db.session import get_db
from app.services.feishu import get_feishu_app_or_404
from app.services.feishu_admin_apps import refresh_tenant_access_token

router = APIRouter()


@router.post("/apps/{app_config_id}/tenant-access-token", dependencies=[Depends(require_admin_api_token)])
async def tenant_access_token(app_config_id: UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    app_config = get_feishu_app_or_404(db, app_config_id)
    return await refresh_tenant_access_token(db, app_config)
