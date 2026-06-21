from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.operations_bot_users import list_bot_user_access

router = APIRouter()


@router.get("/bot-users")
def list_bot_users(company_id: UUID | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    return list_bot_user_access(db, company_id=company_id)
