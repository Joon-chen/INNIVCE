from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.operations_advisor import AdvisorChatRequest, answer_operations_advisor_chat_from_request

router = APIRouter()


@router.post("/advisor/chat")
def advisor_chat(data: AdvisorChatRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    return answer_operations_advisor_chat_from_request(db, data)
