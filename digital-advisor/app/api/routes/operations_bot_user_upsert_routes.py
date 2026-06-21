from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.operations_bot_users import BotUserAccessCreate, upsert_bot_user_access_from_request

router = APIRouter()


@router.post("/bot-users")
def upsert_bot_user(data: BotUserAccessCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    return upsert_bot_user_access_from_request(db, data)
