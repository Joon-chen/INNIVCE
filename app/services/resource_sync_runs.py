from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.entities import ResourceSyncRun


def start_resource_sync_run(
    db: Session,
    *,
    company_id: UUID,
    resource_id: UUID,
    sync_action: str,
    summary: dict[str, Any] | None = None,
) -> ResourceSyncRun:
    run = ResourceSyncRun(
        company_id=company_id,
        resource_id=resource_id,
        sync_action=sync_action,
        status="running",
        started_at=datetime.now(UTC),
        items_seen=0,
        items_indexed=0,
        items_skipped=0,
        summary=summary or {},
    )
    db.add(run)
    db.flush()
    return run


def finish_resource_sync_run(
    run: ResourceSyncRun,
    *,
    status: str,
    items_seen: int = 0,
    items_indexed: int = 0,
    items_skipped: int = 0,
    error_message: str | None = None,
    summary: dict[str, Any] | None = None,
) -> ResourceSyncRun:
    run.status = status
    run.finished_at = datetime.now(UTC)
    run.items_seen = max(items_seen, 0)
    run.items_indexed = max(items_indexed, 0)
    run.items_skipped = max(items_skipped, 0)
    run.error_message = error_message
    if summary is not None:
        run.summary = summary
    return run
