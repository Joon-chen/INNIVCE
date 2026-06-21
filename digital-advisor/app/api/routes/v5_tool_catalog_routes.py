from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.v5_tool_admin import list_tools_for_company

router = APIRouter()


@router.get("/tools")
def list_tools(company_id: UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    return list_tools_for_company(db, company_id=company_id)
