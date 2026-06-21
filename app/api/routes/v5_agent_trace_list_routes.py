from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.v5_agent_admin import list_agent_trace_logs

router = APIRouter()


@router.get("/agent/traces")
def list_agent_traces(
    company_id: UUID,
    route_path: str | None = None,
    agent_id: str | None = None,
    agent_owner_open_id: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return list_agent_trace_logs(
        db,
        company_id=company_id,
        route_path=route_path.strip() if route_path else None,
        agent_id=agent_id.strip() if agent_id else None,
        agent_owner_open_id=agent_owner_open_id.strip() if agent_owner_open_id else None,
        limit=limit,
    )
