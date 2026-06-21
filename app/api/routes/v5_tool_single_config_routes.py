from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.routes.v5_tool_request_models import ToolConfigUpdate
from app.db.session import get_db
from app.services.tools.config import IncompatibleToolProviderError, UnknownToolConfigError
from app.services.v5_tool_admin import update_tool_configuration

router = APIRouter()


@router.put("/tools/{tool_name}")
def update_tool_config(
    tool_name: str,
    request: ToolConfigUpdate,
    company_id: UUID,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        return update_tool_configuration(
            db,
            company_id=company_id,
            tool_name=tool_name,
            enabled=request.enabled,
            provider=request.provider,
            config_json=request.config_json,
        )
    except UnknownToolConfigError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IncompatibleToolProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
