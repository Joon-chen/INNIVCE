from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.v5_system_logs import list_system_log_items

router = APIRouter()


@router.get("/system/logs")
def list_system_logs(
    company_id: UUID | None = None,
    category: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    reason: str | None = None,
    confirmed: bool | None = None,
    confirmation_token_checked: bool | None = None,
    used_agent_runtime: bool | None = None,
    final_answer_owner: str | None = None,
    route_path: str | None = None,
    agent_id: str | None = None,
    agent_owner_open_id: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return list_system_log_items(
        db,
        company_id=company_id,
        category=category.strip() if category else None,
        severity=severity.strip() if severity else None,
        status=status.strip() if status else None,
        reason=reason.strip() if reason else None,
        confirmed=confirmed,
        confirmation_token_checked=confirmation_token_checked,
        used_agent_runtime=used_agent_runtime,
        final_answer_owner=final_answer_owner.strip() if final_answer_owner else None,
        route_path=route_path.strip() if route_path else None,
        agent_id=agent_id.strip() if agent_id else None,
        agent_owner_open_id=agent_owner_open_id.strip() if agent_owner_open_id else None,
        action=action.strip() if action else None,
        target_type=target_type.strip() if target_type else None,
        limit=limit,
    )
