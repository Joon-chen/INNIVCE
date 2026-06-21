from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.v5_administration import list_administration_resource_permissions

router = APIRouter()


@router.get("/administration/resource-permissions")
def list_resource_permissions(
    company_id: UUID | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return list_administration_resource_permissions(db, company_id=company_id)
