from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import SyncRun


def start_sync_run(
    db: Session,
    *,
    company_id,
    provider: str,
    sync_type: str,
    cursor: dict[str, Any] | None = None,
) -> SyncRun:
    sync_run = SyncRun(
        company_id=company_id,
        provider=provider,
        sync_type=sync_type,
        status="running",
        started_at=datetime.now(UTC),
        saved_count=0,
        error_count=0,
        cursor=cursor or {},
        summary={},
    )
    db.add(sync_run)
    db.flush()
    return sync_run


def finish_sync_run(
    sync_run: SyncRun,
    *,
    status: str,
    saved_count: int = 0,
    error_count: int = 0,
    cursor: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
) -> SyncRun:
    sync_run.status = status
    sync_run.finished_at = datetime.now(UTC)
    sync_run.saved_count = saved_count
    sync_run.error_count = error_count
    sync_run.cursor = cursor or sync_run.cursor or {}
    sync_run.summary = summary or sync_run.summary or {}
    return sync_run
