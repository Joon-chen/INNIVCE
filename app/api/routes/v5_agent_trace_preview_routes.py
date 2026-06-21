from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.routes.v5_agent_request_models import AgentTracePreviewRequest
from app.db.session import get_db
from app.services.v5_agent_admin import preview_agent_trace

router = APIRouter()


@router.post("/agent/trace-preview")
def agent_trace_preview(
    data: AgentTracePreviewRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return preview_agent_trace(
        db,
        company_id=data.company_id,
        question=data.question,
        normalized_command=data.normalized_command or data.question,
        chat_id=data.chat_id,
        actor_role=data.actor_role,
        actor_access_scope=data.actor_access_scope,
        actor_domains=data.actor_domains,
        actor_display_name=data.actor_display_name,
        actor_open_id=data.actor_open_id,
        actor_email=data.actor_email,
    )
