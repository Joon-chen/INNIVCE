from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.capability_registry_builder import CapabilityRegistryBuilder
from app.services.tools.config import list_tool_configurations

router = APIRouter()


@router.get("/capability-registry")
def capability_registry(company_id: UUID, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "private, max-age=30"
    tool_configs = tuple(list_tool_configurations(db, company_id=company_id))
    return CapabilityRegistryBuilder(company_id=str(company_id), tool_configs=tool_configs).build()
