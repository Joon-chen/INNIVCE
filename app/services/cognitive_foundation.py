from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import redact_payload
from app.models.entities import MemoryCandidate, Snapshot, WorkEvent


SNAPSHOT_STATUS_COMPLETED = "completed"
MEMORY_CANDIDATE_STATUS = "candidate"


def append_cognitive_work_event(
    db: Session,
    *,
    company_id: UUID,
    event_type: str,
    object_type: str,
    object_id: str,
    source: str,
    actor: str,
    payload: dict | None = None,
) -> WorkEvent:
    """Append an ECF V1 fact event without deduping or overwriting older facts."""
    _require_text("event_type", event_type)
    _require_text("object_type", object_type)
    _require_text("object_id", object_id)
    _require_text("source", source)
    _require_text("actor", actor)
    event = WorkEvent(
        id=uuid4(),
        company_id=company_id,
        source=source,
        source_type="unknown",
        visibility_scope="company",
        data_classification="company",
        business_domain=object_type,
        event_type=event_type,
        object_type=object_type,
        object_id=object_id,
        actor=actor,
        title=f"{object_type}:{object_id}:{event_type}",
        occurred_at=datetime.now(UTC),
        actors=[{"actor": actor}],
        labels=["ecf_v1", object_type],
        payload=redact_payload(payload or {}),
        raw_json=redact_payload(payload or {}),
        vector_status="skipped",
    )
    db.add(event)
    db.flush()
    return event


def get_snapshot(
    db: Session,
    *,
    company_id: UUID,
    object_type: str,
    object_id: str,
    snapshot_type: str,
) -> Snapshot | None:
    return db.scalar(
        select(Snapshot)
        .where(Snapshot.company_id == company_id)
        .where(Snapshot.object_type == object_type)
        .where(Snapshot.object_id == object_id)
        .where(Snapshot.snapshot_type == snapshot_type)
    )


def get_completed_snapshot(
    db: Session,
    *,
    company_id: UUID,
    object_type: str,
    object_id: str,
    snapshot_type: str,
) -> Snapshot | None:
    snapshot = get_snapshot(
        db,
        company_id=company_id,
        object_type=object_type,
        object_id=object_id,
        snapshot_type=snapshot_type,
    )
    if snapshot is None or snapshot.status != SNAPSHOT_STATUS_COMPLETED:
        return None
    return snapshot


def upsert_snapshot(
    db: Session,
    *,
    company_id: UUID,
    object_type: str,
    object_id: str,
    snapshot_type: str,
    status: str,
    summary: str = "",
    recommendation: str = "",
    risk_level: str = "unknown",
    reasons: list[str] | None = None,
    source_event_ids: list[str] | None = None,
    payload: dict | None = None,
) -> Snapshot:
    _require_text("object_type", object_type)
    _require_text("object_id", object_id)
    _require_text("snapshot_type", snapshot_type)
    _require_text("status", status)
    snapshot = get_snapshot(
        db,
        company_id=company_id,
        object_type=object_type,
        object_id=object_id,
        snapshot_type=snapshot_type,
    )
    if snapshot is None:
        snapshot = Snapshot(
            id=uuid4(),
            company_id=company_id,
            object_type=object_type,
            object_id=object_id,
            snapshot_type=snapshot_type,
        )
        db.add(snapshot)
    snapshot.status = status
    snapshot.summary = summary
    snapshot.recommendation = recommendation
    snapshot.risk_level = risk_level
    snapshot.reasons = list(reasons or [])
    snapshot.source_event_ids = list(source_event_ids or [])
    snapshot.payload = redact_payload(payload or {})
    snapshot.updated_at = datetime.now(UTC)
    db.flush()
    return snapshot


def write_memory_candidate(
    db: Session,
    *,
    company_id: UUID,
    memory_type: str,
    object_type: str,
    object_id: str,
    evidence_event_ids: list[str],
    candidate_text: str,
    confidence: float,
    payload: dict | None = None,
) -> MemoryCandidate:
    _require_text("memory_type", memory_type)
    _require_text("object_type", object_type)
    _require_text("object_id", object_id)
    _require_text("candidate_text", candidate_text)
    if confidence < 0 or confidence > 1:
        raise ValueError("confidence must be between 0 and 1")
    candidate = MemoryCandidate(
        id=uuid4(),
        company_id=company_id,
        memory_type=memory_type,
        object_type=object_type,
        object_id=object_id,
        evidence_event_ids=list(evidence_event_ids),
        candidate_text=candidate_text,
        confidence=confidence,
        status=MEMORY_CANDIDATE_STATUS,
        payload=redact_payload(payload or {}),
    )
    db.add(candidate)
    db.flush()
    return candidate


def _require_text(name: str, value: str) -> None:
    if not str(value or "").strip():
        raise ValueError(f"{name} is required")
