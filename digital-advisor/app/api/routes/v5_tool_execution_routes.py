from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.routes.v5_tool_request_models import ToolExecutionRequest
from app.db.session import get_db
from app.services.v5_tool_admin import execute_admin_tool

router = APIRouter()


@router.post("/tools/{tool_name}/execute")
def execute_tool(
    tool_name: str,
    request: ToolExecutionRequest,
    company_id: UUID,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        return execute_admin_tool(
            db,
            company_id=company_id,
            tool_name=tool_name,
            question=request.question,
            normalized_command=request.normalized_command,
            params=request.params,
            chat_id=request.chat_id,
            actor_role=request.actor_role,
            actor_access_scope=request.actor_access_scope,
            actor_domains=request.actor_domains,
            actor_display_name=request.actor_display_name,
            actor_open_id=request.actor_open_id,
            actor_email=request.actor_email,
        )
    except ValueError as exc:
        detail = str(exc)
        raise HTTPException(status_code=404 if "Unknown agent tool" in detail else 400, detail=detail) from exc
