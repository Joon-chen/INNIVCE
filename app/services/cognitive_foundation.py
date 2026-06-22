from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import redact_payload
from app.models.entities import MemoryCandidate, Snapshot, WorkEvent


SNAPSHOT_STATUS_COMPLETED = "completed"
MEMORY_CANDIDATE_STATUS = "candidate"
WORKSPACE_COGNITIVE_PROJECTION_VERSION = "workspace_cognitive_projection_v0"
WORKSPACE_COGNITIVE_FIELD_ALLOWLIST: dict[str, tuple[str, ...]] = {
    "task": (
        "task_id",
        "task_guid",
        "title",
        "status",
        "priority",
        "due_at",
        "completed_at",
        "updated_at",
        "owner_user_id",
        "owner_open_id",
        "owner_department_id",
        "assignee_count",
        "follower_count",
        "is_overdue",
    ),
    "calendar": (
        "event_id",
        "calendar_id",
        "title",
        "status",
        "start_at",
        "end_at",
        "updated_at",
        "owner_user_id",
        "owner_open_id",
        "owner_department_id",
        "attendee_count",
        "is_conflict",
        "is_busy",
    ),
}


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
    visibility_scope: str = "company",
    data_classification: str = "company",
    allowed_user_ids: list[str] | None = None,
    allowed_departments: list[str] | None = None,
    allowed_roles: list[str] | None = None,
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
        visibility_scope=visibility_scope,
        data_classification=data_classification,
        allowed_user_ids=list(allowed_user_ids or []),
        allowed_departments=list(allowed_departments or []),
        allowed_roles=list(allowed_roles or []),
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


def append_workspace_cognitive_event(
    db: Session,
    *,
    company_id: UUID,
    object_type: str,
    object_id: str,
    source: str,
    actor: str,
    raw_payload: dict,
    owner_user_id: str = "",
    owner_open_id: str = "",
    owner_department_id: str = "",
    visibility_scope: str = "self",
) -> WorkEvent:
    projection = build_workspace_cognitive_projection(
        object_type=object_type,
        raw_payload=raw_payload,
        owner_user_id=owner_user_id,
        owner_open_id=owner_open_id,
        owner_department_id=owner_department_id,
        visibility_scope=visibility_scope,
    )
    return append_cognitive_work_event(
        db,
        company_id=company_id,
        event_type=f"workspace_{object_type}_observed",
        object_type=object_type,
        object_id=object_id,
        source=source,
        actor=actor,
        payload=projection,
        visibility_scope=visibility_scope,
        data_classification="workspace_cognitive",
        allowed_user_ids=[owner_user_id] if owner_user_id else [],
        allowed_departments=[owner_department_id] if owner_department_id else [],
    )


def build_workspace_cognitive_projection(
    *,
    object_type: str,
    raw_payload: dict,
    owner_user_id: str = "",
    owner_open_id: str = "",
    owner_department_id: str = "",
    visibility_scope: str = "self",
) -> dict:
    normalized_type = _normalize_workspace_object_type(object_type)
    allowlist = WORKSPACE_COGNITIVE_FIELD_ALLOWLIST[normalized_type]
    cognitive_fields = {key: raw_payload[key] for key in allowlist if key in raw_payload and raw_payload[key] not in (None, "")}
    if owner_user_id:
        cognitive_fields.setdefault("owner_user_id", owner_user_id)
    if owner_open_id:
        cognitive_fields.setdefault("owner_open_id", owner_open_id)
    if owner_department_id:
        cognitive_fields.setdefault("owner_department_id", owner_department_id)

    return {
        "projection_version": WORKSPACE_COGNITIVE_PROJECTION_VERSION,
        "object_type": normalized_type,
        "visibility_scope": visibility_scope,
        "operational_detail_stored": False,
        "raw_detail_allowed": False,
        "cognitive_fields": redact_payload(cognitive_fields),
        "aggregate_surfaces": {
            "self": ["count", "status", "due", "conflict", "workload"],
            "department": ["count", "overdue_count", "conflict_count", "workload_bucket"],
            "company": ["count", "overdue_count", "conflict_count", "risk_trend"],
        },
        "redacted_fields": _workspace_redacted_fields(raw_payload, allowlist),
    }


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


def _normalize_workspace_object_type(object_type: str) -> str:
    normalized = str(object_type or "").strip().lower()
    if normalized in {"task", "calendar"}:
        return normalized
    raise ValueError("workspace object_type must be task or calendar")


def _workspace_redacted_fields(raw_payload: dict, allowlist: tuple[str, ...]) -> list[str]:
    allowset = set(allowlist)
    return sorted(str(key) for key in raw_payload.keys() if str(key) not in allowset)
