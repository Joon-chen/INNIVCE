import json
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import AuditLog, ExtractedItem, Report, SyncRun


def list_sync_run_payloads(db: Session, *, company_id: UUID | None = None, limit: int = 50) -> dict[str, Any]:
    query = select(SyncRun).order_by(SyncRun.started_at.desc()).limit(limit)
    if company_id:
        query = query.where(SyncRun.company_id == company_id)
    return {"items": [sync_run_payload(run) for run in db.scalars(query).all()]}


def sync_run_payload(run: SyncRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "company_id": str(run.company_id) if run.company_id else None,
        "provider": run.provider,
        "sync_type": run.sync_type,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "saved_count": run.saved_count,
        "error_count": run.error_count,
        "cursor": run.cursor,
        "summary": run.summary,
    }


def list_extracted_item_payloads(
    db: Session,
    *,
    company_id: UUID | None = None,
    item_type: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    query = select(ExtractedItem).order_by(ExtractedItem.created_at.desc()).limit(limit)
    if company_id:
        query = query.where(ExtractedItem.company_id == company_id)
    if item_type:
        query = query.where(ExtractedItem.item_type == item_type)
    if status:
        query = query.where(ExtractedItem.status == status)
    return {"items": [extracted_item_payload(item) for item in db.scalars(query).all()]}


def extracted_item_payload(item: ExtractedItem) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "company_id": str(item.company_id),
        "work_event_id": str(item.work_event_id) if item.work_event_id else None,
        "item_type": item.item_type,
        "title": display_extracted_title(item),
        "description": item.description,
        "owner": item.owner,
        "due_at": item.due_at.isoformat() if item.due_at else None,
        "priority": item.priority,
        "status": item.status,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def list_report_payloads(
    db: Session,
    *,
    company_id: UUID | None = None,
    report_type: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    query = select(Report).order_by(Report.created_at.desc()).limit(limit)
    if company_id:
        query = query.where(Report.company_id == company_id)
    if report_type:
        query = query.where(Report.report_type == report_type)
    return {"items": [report_payload(report) for report in db.scalars(query).all()]}


def report_payload(report: Report) -> dict[str, Any]:
    return {
        "id": str(report.id),
        "company_id": str(report.company_id) if report.company_id else None,
        "report_type": report.report_type,
        "title": report.title,
        "period_start": report.period_start.isoformat() if report.period_start else None,
        "period_end": report.period_end.isoformat() if report.period_end else None,
        "content_markdown": report.content_markdown,
        "created_at": report.created_at.isoformat() if report.created_at else None,
    }


def list_audit_log_payloads(
    db: Session,
    *,
    company_id: UUID | None = None,
    action: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    query = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if company_id:
        query = query.where(AuditLog.company_id == company_id)
    if action:
        query = query.where(AuditLog.action == action)
    return {"items": [audit_log_payload(item) for item in db.scalars(query).all()]}


def audit_log_payload(item: AuditLog) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "company_id": str(item.company_id) if item.company_id else None,
        "actor": item.actor,
        "action": item.action,
        "target_type": item.target_type,
        "target_id": item.target_id,
        "payload": item.payload,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def display_extracted_title(item: ExtractedItem) -> str:
    raw_title = item.title or ""
    subject_match = re.search(r'"subject"\s*:\s*"([^"]+)"', raw_title)
    if subject_match:
        return subject_match.group(1)
    try:
        parsed = json.loads(raw_title)
    except (TypeError, ValueError):
        return raw_title
    if isinstance(parsed, dict):
        for key in ("subject", "title", "summary", "name"):
            value = parsed.get(key)
            if value:
                return str(value)
    return raw_title
