from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.v5_tool_admin import list_tool_execution_logs

router = APIRouter()


@router.get("/tools/executions")
def list_tool_executions(
    company_id: UUID,
    tool_name: str | None = None,
    status: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return list_tool_execution_logs(db, company_id=company_id, tool_name=tool_name, status=status, limit=limit)
