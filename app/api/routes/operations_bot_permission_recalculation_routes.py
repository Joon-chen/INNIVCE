from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.operations_bot_users import (
    RecalculateBotPermissionsRequest,
    recalculate_bot_user_permissions_from_request,
)

router = APIRouter()


@router.post("/bot-users/recalculate-permissions")
def recalculate_bot_permissions(data: RecalculateBotPermissionsRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    return recalculate_bot_user_permissions_from_request(db, data)
