from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import redact_payload
from app.models.entities import MemoryCandidate, Snapshot, WorkEvent


SNAPSHOT_STATUS_COMPLETED = "completed"
MEMORY_CANDIDATE_STATUS = "candidate"
WORKSPACE_COGNITIVE_PROJECTION_VERSION = "workspace_cognitive_projection_v0"
WORKSPACE_AGGREGATION_VERSION = "workspace_aggregation_v0"
WORKSPACE_V0_AGGREGATE_METRICS = (
    "task_total",
    "overdue_task_count",
    "due_soon_task_count",
    "calendar_conflict_count",
    "meeting_occupied_minutes",
    "workload_buckets",
)
WORKSPACE_SYNC_SOURCE = "feishu_user_observation"
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


def build_workspace_aggregation_summary(
    events: list[WorkEvent] | tuple[WorkEvent, ...],
    *,
    scope: str,
    now: datetime | None = None,
    due_soon_days: int = 7,
) -> dict:
    effective_now = now or datetime.now(UTC)
    due_soon_until = effective_now + _timedelta_days(due_soon_days)
    owner_stats: dict[str, dict[str, int]] = {}
    task_total = 0
    overdue_task_count = 0
    due_soon_task_count = 0
    calendar_conflict_count = 0
    meeting_occupied_minutes = 0

    for event in _latest_workspace_projection_events(events):
        projection = event.payload if isinstance(event.payload, dict) else {}
        if projection.get("projection_version") != WORKSPACE_COGNITIVE_PROJECTION_VERSION:
            continue
        object_type = str(projection.get("object_type") or event.object_type or "").strip().lower()
        fields = projection.get("cognitive_fields") if isinstance(projection.get("cognitive_fields"), dict) else {}
        owner_key = str(fields.get("owner_user_id") or fields.get("owner_open_id") or "unknown").strip() or "unknown"
        owner_bucket = owner_stats.setdefault(owner_key, {"task_count": 0, "overdue_count": 0, "meeting_minutes": 0, "conflict_count": 0})

        if object_type == "task":
            if _is_completed_task(fields):
                continue
            task_total += 1
            owner_bucket["task_count"] += 1
            due_at = _parse_datetime(fields.get("due_at"))
            is_overdue = bool(fields.get("is_overdue")) or bool(due_at and due_at < effective_now)
            if is_overdue:
                overdue_task_count += 1
                owner_bucket["overdue_count"] += 1
            if due_at and effective_now <= due_at <= due_soon_until:
                due_soon_task_count += 1
        elif object_type == "calendar":
            start_at = _parse_datetime(fields.get("start_at"))
            end_at = _parse_datetime(fields.get("end_at"))
            occupied = _meeting_minutes(start_at, end_at) if bool(fields.get("is_busy", True)) else 0
            meeting_occupied_minutes += occupied
            owner_bucket["meeting_minutes"] += occupied
            if bool(fields.get("is_conflict")):
                calendar_conflict_count += 1
                owner_bucket["conflict_count"] += 1

    return {
        "aggregation_version": WORKSPACE_AGGREGATION_VERSION,
        "scope": scope,
        "detail_available": False,
        "source_event_count": len(events),
        "metrics": {
            "task_total": task_total,
            "overdue_task_count": overdue_task_count,
            "due_soon_task_count": due_soon_task_count,
            "calendar_conflict_count": calendar_conflict_count,
            "meeting_occupied_minutes": meeting_occupied_minutes,
            "workload_buckets": _workspace_workload_buckets(owner_stats),
        },
        "metric_order": list(WORKSPACE_V0_AGGREGATE_METRICS),
        "policy_notes": [
            "aggregation_only",
            "operational_detail_not_included",
            "source_visibility_must_be_filtered_before_display",
        ],
    }


def append_workspace_cognitive_observations(
    db: Session,
    *,
    company_id: UUID,
    object_type: str,
    raw_items: list[dict] | tuple[dict, ...],
    actor: str,
    owner_user_id: str = "",
    owner_open_id: str = "",
    owner_department_id: str = "",
    visibility_scope: str = "self",
    source: str = WORKSPACE_SYNC_SOURCE,
) -> tuple[WorkEvent, ...]:
    """Store authorized Workspace observations as cognitive projections only."""
    normalized_type = _normalize_workspace_object_type(object_type)
    events: list[WorkEvent] = []
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            continue
        object_id = _workspace_observation_object_id(normalized_type, raw_item, index)
        events.append(
            append_workspace_cognitive_event(
                db,
                company_id=company_id,
                object_type=normalized_type,
                object_id=object_id,
                source=source,
                actor=actor,
                raw_payload=raw_item,
                owner_user_id=owner_user_id,
                owner_open_id=owner_open_id,
                owner_department_id=owner_department_id,
                visibility_scope=visibility_scope,
            )
        )
    return tuple(events)


def build_workspace_aggregation_from_visible_events(
    events: list[WorkEvent] | tuple[WorkEvent, ...],
    *,
    scope: str,
    now: datetime | None = None,
) -> dict:
    summary = build_workspace_aggregation_summary(events, scope=scope, now=now)
    summary["workevent_as_realtime_source"] = False
    summary["operational_detail_available"] = False
    summary["source"] = "workspace_cognitive_projection"
    return summary


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
        "cognitive_fields": _redact_workspace_cognitive_fields(cognitive_fields),
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


def _latest_workspace_projection_events(events: list[WorkEvent] | tuple[WorkEvent, ...]) -> tuple[WorkEvent, ...]:
    latest: dict[tuple[str, str], WorkEvent] = {}
    for event in events:
        projection = event.payload if isinstance(event.payload, dict) else {}
        if projection.get("projection_version") != WORKSPACE_COGNITIVE_PROJECTION_VERSION:
            continue
        object_type = str(projection.get("object_type") or event.object_type or "").strip().lower()
        key = (object_type, str(event.object_id or "").strip())
        if not key[0] or not key[1]:
            continue
        existing = latest.get(key)
        if existing is None or _workspace_projection_sort_time(event) >= _workspace_projection_sort_time(existing):
            latest[key] = event
    return tuple(latest.values())


def _workspace_projection_sort_time(event: WorkEvent) -> datetime:
    projection = event.payload if isinstance(event.payload, dict) else {}
    fields = projection.get("cognitive_fields") if isinstance(projection.get("cognitive_fields"), dict) else {}
    for key in ("updated_at", "completed_at", "due_at", "start_at", "end_at"):
        parsed = _parse_datetime(fields.get(key))
        if parsed is not None:
            return parsed
    return event.occurred_at


def _workspace_observation_object_id(object_type: str, raw_item: dict, index: int) -> str:
    if object_type == "task":
        for key in ("task_id", "task_guid", "guid", "id"):
            if raw_item.get(key):
                return str(raw_item[key])
    if object_type == "calendar":
        for key in ("event_id", "calendar_id", "id"):
            if raw_item.get(key):
                return str(raw_item[key])
    return f"{object_type}:observed:{index}"


def _redact_workspace_cognitive_fields(fields: dict) -> dict:
    time_values = {key: fields[key] for key in ("due_at", "completed_at", "updated_at", "start_at", "end_at") if key in fields}
    redacted = redact_payload(dict(fields))
    for key in ("due_at", "completed_at", "updated_at", "start_at", "end_at"):
        if key in time_values:
            redacted[key] = time_values[key]
    return redacted


def _parse_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _timedelta_days(days: int):
    from datetime import timedelta

    return timedelta(days=max(0, int(days)))


def _is_completed_task(fields: dict) -> bool:
    status = str(fields.get("status") or "").strip().lower()
    if status in {"done", "completed", "complete", "finished"}:
        return True
    completed_at = _parse_datetime(fields.get("completed_at"))
    return bool(completed_at and completed_at.year > 1970)


def _meeting_minutes(start_at: datetime | None, end_at: datetime | None) -> int:
    if start_at is None or end_at is None or end_at <= start_at:
        return 0
    return int((end_at - start_at).total_seconds() // 60)


def _workspace_workload_buckets(owner_stats: dict[str, dict[str, int]]) -> dict[str, int]:
    buckets = {"normal": 0, "busy": 0, "overloaded": 0, "at_risk": 0}
    for stats in owner_stats.values():
        task_count = int(stats.get("task_count") or 0)
        meeting_minutes = int(stats.get("meeting_minutes") or 0)
        overdue_count = int(stats.get("overdue_count") or 0)
        conflict_count = int(stats.get("conflict_count") or 0)
        if overdue_count > 0 or conflict_count > 0:
            buckets["at_risk"] += 1
        elif task_count >= 8 or meeting_minutes >= 360:
            buckets["overloaded"] += 1
        elif task_count >= 4 or meeting_minutes >= 180:
            buckets["busy"] += 1
        else:
            buckets["normal"] += 1
    return buckets
