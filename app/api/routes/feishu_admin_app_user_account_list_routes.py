from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin_api_token
from app.db.session import get_db
from app.services.feishu import get_feishu_app_or_404
from app.services.feishu_admin_apps import list_feishu_user_account_payloads

router = APIRouter()


@router.get("/apps/{app_config_id}/user-accounts", dependencies=[Depends(require_admin_api_token)])
def list_feishu_user_accounts(app_config_id: UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    app_config = get_feishu_app_or_404(db, app_config_id)
    return list_feishu_user_account_payloads(db, app_config)
