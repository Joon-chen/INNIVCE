from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.routes.v5_tool_request_models import ToolBatchUpdateRequest
from app.db.session import get_db
from app.services.v5_tool_admin import batch_update_tool_configurations

router = APIRouter()


@router.post("/tools/batch")
def batch_update_tool_configs(
    request: ToolBatchUpdateRequest,
    company_id: UUID,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        return batch_update_tool_configurations(
            db,
            company_id=company_id,
            all_tools=request.all_tools,
            tool_names=request.tool_names,
            include_prefixes=request.include_prefixes,
            include_providers=request.include_providers,
            supports_write=request.supports_write,
            enabled=request.enabled,
            provider=request.provider,
            config_json=request.config_json,
            dry_run=request.dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
