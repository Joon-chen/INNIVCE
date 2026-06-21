from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin_api_token
from app.db.session import get_db
from app.services.feishu import get_feishu_app_or_404
from app.services.feishu_admin_api_reads import accessible_mailboxes_payload

router = APIRouter()


@router.get("/apps/{app_config_id}/mail/accessible-mailboxes", dependencies=[Depends(require_admin_api_token)])
async def list_accessible_mailboxes(app_config_id: UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    get_feishu_app_or_404(db, app_config_id)
    return accessible_mailboxes_payload()
