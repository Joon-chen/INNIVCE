from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.routes.v5_agent_request_models import AgentSettingsUpdate
from app.db.session import get_db
from app.services.v5_agent_admin import save_agent_settings as save_agent_settings_service

router = APIRouter()


@router.put("/agent/settings")
def save_agent_settings(
    data: AgentSettingsUpdate,
    company_id: UUID,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    updates = data.model_dump(exclude_none=True)
    return save_agent_settings_service(db, company_id=company_id, updates=updates)
