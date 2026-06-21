from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import redact_payload, redact_text
from app.models.entities import Resource, WorkEvent
from app.schemas.common import WorkEventCreate
from app.services.audit import write_audit_log
from app.services.data.resource_sources import normalize_source_type


def upsert_work_event(db: Session, data: WorkEventCreate) -> WorkEvent:
    occurred_at = data.occurred_at or datetime.now(UTC)
    content_text = redact_text(data.content_text) if data.content_text else None
    payload = redact_payload(data.payload)
    raw_json = redact_payload(data.raw_json or data.payload)
    source_type = normalize_source_type(data.source_type)
    inferred_resource_id = data.resource_id or _infer_resource_id_for_work_event(db, data)

    event: WorkEvent | None = None
    if data.external_id:
        event = db.scalar(
            select(WorkEvent).where(
                WorkEvent.source == data.source,
                WorkEvent.external_id == data.external_id,
            )
        )

    if event is None:
        event = WorkEvent(
            company_id=data.company_id,
            account_id=data.account_id,
            resource_id=inferred_resource_id,
            source=data.source,
            source_type=source_type,
            source_account_id=data.source_account_id,
            visibility_scope=data.visibility_scope,
            allowed_user_ids=data.allowed_user_ids,
            allowed_roles=data.allowed_roles,
            allowed_departments=data.allowed_departments,
            data_classification=data.data_classification,
            business_domain=data.business_domain,
            event_type=data.event_type,
            external_id=data.external_id,
            thread_id=data.thread_id,
            title=data.title,
            content_text=content_text,
            occurred_at=occurred_at,
            actors=data.actors,
            labels=data.labels,
            payload=payload,
            raw_json=raw_json,
            importance_score=data.importance_score,
            sensitivity=data.sensitivity,
        )
        db.add(event)
        action = "work_event.created"
    else:
        event.thread_id = data.thread_id or event.thread_id
        event.resource_id = inferred_resource_id or event.resource_id
        event.source_type = source_type or event.source_type
        event.source_account_id = data.source_account_id or event.source_account_id
        event.visibility_scope = data.visibility_scope or event.visibility_scope
        event.allowed_user_ids = data.allowed_user_ids or event.allowed_user_ids
        event.allowed_roles = data.allowed_roles or event.allowed_roles
        event.allowed_departments = data.allowed_departments or event.allowed_departments
        event.data_classification = data.data_classification or event.data_classification
        event.business_domain = data.business_domain or event.business_domain
        event.title = data.title or event.title
        event.content_text = content_text or event.content_text
        event.occurred_at = occurred_at
        event.actors = data.actors or event.actors
        event.labels = data.labels or event.labels
        event.payload = payload or event.payload
        event.raw_json = raw_json or event.raw_json
        event.importance_score = data.importance_score
        event.sensitivity = data.sensitivity or event.sensitivity
        action = "work_event.updated"

    should_enqueue_vectorize = _should_enqueue_vectorize_event(event)
    event.vector_status = "pending" if should_enqueue_vectorize else "skipped"
    db.flush()
    write_audit_log(
        db,
        action=action,
        company_id=event.company_id,
        target_type="work_event",
        target_id=str(event.id),
        payload={"source": event.source, "external_id": event.external_id, "title": event.title},
    )
    if should_enqueue_vectorize:
        _enqueue_vectorize_event(str(event.id))
    return event


def _infer_resource_id_for_work_event(db: Session, data: WorkEventCreate) -> UUID | None:
    if data.resource_id or data.source != "feishu" or not data.thread_id:
        return data.resource_id
    resource = db.scalar(
        select(Resource.id)
        .where(Resource.company_id == data.company_id)
        .where(Resource.platform == "feishu")
        .where(Resource.resource_type == "chat")
        .where(Resource.resource_id == data.thread_id)
    )
    return resource


def _should_enqueue_vectorize_event(event: WorkEvent) -> bool:
    try:
        from app.services.ai.vector_search import should_vectorize_work_event

        return should_vectorize_work_event(event)
    except Exception:
        return False


def _enqueue_vectorize_event(event_id: str) -> None:
    try:
        from app.tasks.celery_app import vectorize_work_event_task

        vectorize_work_event_task.apply_async(args=[event_id], countdown=2)
    except Exception as exc:
        # The event itself is already persisted; record the background indexing failure separately.
        # We intentionally do not raise here because ingestion must remain the source of truth.
        # The caller's transaction will include this audit log.
        # Import stays local to avoid a circular import at app startup.
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            write_audit_log(
                db,
                action="work_event.vectorize.enqueue_failed",
                target_type="work_event",
                target_id=event_id,
                payload={"error": str(exc)[:300]},
            )
            db.commit()
        finally:
            db.close()
        return
