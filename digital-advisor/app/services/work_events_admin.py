from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Attachment, ExtractedItem, WorkEvent
from app.schemas.common import WorkEventCreate
from app.services.ai.extraction import extract_items_for_event
from app.services.ai.reports import generate_daily_report
from app.services.ai.vector_search import WorkEventVectorIndex, mark_event_vector_indexing_result
from app.services.work_events import upsert_work_event


def create_work_event_payload(db: Session, data: WorkEventCreate) -> WorkEvent:
    event = upsert_work_event(db, data)
    db.commit()
    db.refresh(event)
    return event


def list_work_event_payloads(db: Session, *, company_id: UUID | None = None, limit: int = 50) -> list[WorkEvent]:
    query = select(WorkEvent).order_by(WorkEvent.occurred_at.desc()).limit(limit)
    if company_id:
        query = query.where(WorkEvent.company_id == company_id)
    return list(db.scalars(query).all())


def work_event_detail_payload(db: Session, event_id: UUID) -> dict:
    event = db.get(WorkEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Work event not found")

    extracted_items = db.scalars(
        select(ExtractedItem).where(ExtractedItem.work_event_id == event.id).order_by(ExtractedItem.created_at.desc())
    ).all()
    attachments = db.scalars(
        select(Attachment).where(Attachment.work_event_id == event.id).order_by(Attachment.created_at.desc())
    ).all()
    return {
        "id": str(event.id),
        "company_id": str(event.company_id),
        "account_id": str(event.account_id) if event.account_id else None,
        "resource_id": str(getattr(event, "resource_id", None)) if getattr(event, "resource_id", None) else None,
        "source": event.source,
        "event_type": event.event_type,
        "external_id": event.external_id,
        "thread_id": event.thread_id,
        "title": event.title,
        "content_text": event.content_text,
        "occurred_at": event.occurred_at.isoformat(),
        "actors": event.actors,
        "labels": event.labels,
        "payload": event.payload,
        "raw_json": getattr(event, "raw_json", event.payload),
        "importance_score": getattr(event, "importance_score", 0.0),
        "sensitivity": event.sensitivity,
        "vector_status": event.vector_status,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "updated_at": event.updated_at.isoformat() if event.updated_at else None,
        "extracted_items": [
            {
                "id": str(item.id),
                "item_type": item.item_type,
                "title": item.title,
                "description": item.description,
                "owner": item.owner,
                "due_at": item.due_at.isoformat() if item.due_at else None,
                "priority": item.priority,
                "status": item.status,
                "payload": item.payload,
            }
            for item in extracted_items
        ],
        "attachments": [
            {
                "id": str(item.id),
                "filename": item.filename,
                "content_type": item.content_type,
                "size_bytes": item.size_bytes,
                "storage_bucket": item.storage_bucket,
                "storage_key": item.storage_key,
                "checksum": item.checksum,
            }
            for item in attachments
        ],
    }


def extract_event_items_payload(db: Session, event_id: UUID) -> dict:
    event = db.get(WorkEvent, event_id)
    if not event:
        return {"count": 0, "items": []}
    items = extract_items_for_event(db, event)
    db.commit()
    return {"count": len(items), "items": [{"id": str(item.id), "type": item.item_type} for item in items]}


def vectorize_event_payload(db: Session, event_id: UUID) -> dict:
    event = db.get(WorkEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Work event not found")
    ok = WorkEventVectorIndex().upsert_event(event)
    mark_event_vector_indexing_result(event, indexed=ok)
    db.commit()
    return {"ok": ok, "event_id": str(event.id), "vector_status": event.vector_status}


def daily_report_payload(db: Session, data) -> dict:
    report = generate_daily_report(db, report_date=data.report_date, company_id=data.company_id)
    db.commit()
    db.refresh(report)
    return {
        "id": str(report.id),
        "title": report.title,
        "content_markdown": report.content_markdown,
        "payload": report.payload,
    }


def vector_search_payload(data) -> dict:
    return {
        "results": WorkEventVectorIndex().search(
            query=data.query,
            company_id=data.company_id,
            limit=data.limit,
            chat_id=data.chat_id,
        )
    }


def vectorize_pending_payload(data) -> dict:
    from app.tasks.celery_app import vectorize_pending_work_events_task

    task = vectorize_pending_work_events_task.delay(data.limit)
    return {"task_id": task.id, "status": "queued"}
