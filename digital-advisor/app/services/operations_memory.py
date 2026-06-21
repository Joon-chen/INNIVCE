from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.serialization import json_safe
from app.models.entities import MemoryFact


class MemoryFactCreate(BaseModel):
    company_id: UUID
    fact_type: str
    subject: str
    content: str
    confidence: str = "medium"
    source_work_event_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class GenerateMemoryRequest(BaseModel):
    company_id: UUID | None = None
    limit: int = Field(default=100, ge=1, le=1000)


def create_memory_fact_from_request(db: Session, data: MemoryFactCreate) -> dict[str, Any]:
    return create_memory_fact(
        db,
        company_id=data.company_id,
        fact_type=data.fact_type,
        subject=data.subject,
        content=data.content,
        confidence=data.confidence,
        source_work_event_id=data.source_work_event_id,
        payload=data.payload,
    )


def enqueue_recent_memory_generation_from_request(data: GenerateMemoryRequest) -> dict[str, Any]:
    return enqueue_recent_memory_generation(company_id=data.company_id, limit=data.limit)


def create_memory_fact(
    db: Session,
    *,
    company_id: UUID,
    fact_type: str,
    subject: str,
    content: str,
    confidence: str = "medium",
    source_work_event_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fact = MemoryFact(
        company_id=company_id,
        fact_type=fact_type,
        subject=subject,
        content=content,
        confidence=confidence,
        source_work_event_id=source_work_event_id,
        payload=json_safe(payload or {}),
    )
    db.add(fact)
    db.commit()
    db.refresh(fact)
    return {"id": str(fact.id), "fact_type": fact.fact_type, "subject": fact.subject}


def list_memory_facts(
    db: Session,
    *,
    company_id: UUID,
    fact_type: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    query = select(MemoryFact).where(MemoryFact.company_id == company_id).order_by(MemoryFact.created_at.desc()).limit(limit)
    if fact_type:
        query = query.where(MemoryFact.fact_type == fact_type)
    return {"items": [memory_fact_payload(item) for item in db.scalars(query).all()]}


def memory_fact_payload(item: MemoryFact) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "fact_type": item.fact_type,
        "subject": item.subject,
        "content": item.content,
        "confidence": item.confidence,
        "source_work_event_id": str(item.source_work_event_id) if item.source_work_event_id else None,
    }


def enqueue_recent_memory_generation(*, company_id: UUID | None = None, limit: int = 100) -> dict[str, Any]:
    from app.tasks.celery_app import generate_recent_memory_task

    task = generate_recent_memory_task.delay(str(company_id) if company_id else None, limit)
    return {"task_id": task.id, "status": "queued"}
