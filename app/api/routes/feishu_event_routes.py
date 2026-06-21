from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.feishu import get_feishu_app_or_404
from app.services.feishu_event_entrypoint import receive_feishu_event_payload

router = APIRouter()


@router.post("/events/{app_config_id}")
async def receive_event(
    app_config_id: UUID,
    payload: dict[str, Any],
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    app_config = get_feishu_app_or_404(db, app_config_id)
    return await receive_feishu_event_payload(db, app_config, payload)
