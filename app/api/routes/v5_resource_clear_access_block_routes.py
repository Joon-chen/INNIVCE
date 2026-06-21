from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.v5_workspace import clear_v5_resource_access_block

router = APIRouter()


@router.post("/resources/{resource_id}/clear-access-block")
def clear_resource_access_block(
    resource_id: UUID,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return clear_v5_resource_access_block(db, resource_id=resource_id)
