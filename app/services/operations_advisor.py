from typing import Any
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.models.entities import Company
from app.services.ai.advisor import answer_advisor_question


class AdvisorChatRequest(BaseModel):
    company_id: UUID
    question: str = Field(min_length=1, max_length=4000)


def answer_operations_advisor_chat_from_request(
    db: Session,
    data: AdvisorChatRequest,
) -> dict[str, Any]:
    return answer_operations_advisor_chat(db, company_id=data.company_id, question=data.question)


def answer_operations_advisor_chat(db: Session, *, company_id: UUID, question: str) -> dict[str, Any]:
    company = db.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=400, detail="Invalid company_id")
    answer = answer_advisor_question(
        db,
        company_id=company_id,
        question=question,
        scope="company",
        actor_role="owner",
        actor_access_scope="company",
        actor_domains=["finance", "sales", "rd", "delivery", "hr", "admin"],
    )
    return {"company_id": str(company_id), "answer": answer}
