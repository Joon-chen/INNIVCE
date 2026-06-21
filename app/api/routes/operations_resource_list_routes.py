from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.operations_resources import list_v5_feishu_resources

router = APIRouter()


@router.get("/feishu/resources")
def list_feishu_resources(company_id: UUID | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    return list_v5_feishu_resources(db, company_id=company_id)
