from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.v5_agent_admin import read_agent_settings

router = APIRouter()


@router.get("/agent/settings")
def agent_settings(company_id: UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    return read_agent_settings(db, company_id=company_id)
