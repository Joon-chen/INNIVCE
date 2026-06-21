from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin_api_token
from app.db.session import get_db
from app.services.feishu import get_feishu_app_or_404
from app.services.feishu_admin_apps import feishu_client_routing_payload

router = APIRouter()


@router.get("/apps/{app_config_id}/client-routing", dependencies=[Depends(require_admin_api_token)])
def feishu_client_routing(
    app_config_id: UUID,
    path_or_key: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    app_config = get_feishu_app_or_404(db, app_config_id)
    return feishu_client_routing_payload(app_config, path_or_key)
