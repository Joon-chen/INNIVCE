import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.models.entities import ExtractedItem, WorkEvent
from app.services.cockpit.schemas import CockpitItem
from app.services.cockpit.scope import CockpitScope


OPEN_STATUSES = {"open", "pending", "todo", "doing", "in_progress", "running"}
CLOSED_STATUSES = {"closed", "done", "resolved", "completed", "cancelled", "canceled"}


def since_today() -> datetime:
    now = datetime.now(UTC)
    return datetime(now.year, now.month, now.day, tzinfo=UTC)


def since_days(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def scope_filter(query: Select[Any], model: Any, scope: CockpitScope) -> Select[Any]:
    if scope.all_companies:
        return query
    company_ids = scope.effective_company_ids
    if len(company_ids) == 1:
        return query.where(model.company_id == company_ids[0])
    if company_ids:
        return query.where(model.company_id.in_(company_ids))
    return query


def text_match_conditions(model: Any, keywords: list[str]) -> list[Any]:
    conditions: list[Any] = []
    for keyword in keywords:
        pattern = f"%{keyword}%"
        conditions.extend(
            [
                model.event_type.ilike(pattern),
                model.title.ilike(pattern),
                model.content_text.ilike(pattern),
            ]
        )
    return conditions


def list_events_by_keywords(
    db: Session,
    *,
    scope: CockpitScope,
    keywords: list[str],
    limit: int,
    days: int | None = None,
) -> list[WorkEvent]:
    query = select(WorkEvent).order_by(WorkEvent.occurred_at.desc()).limit(limit)
    query = scope_filter(query, WorkEvent, scope)
    if days:
        query = query.where(WorkEvent.occurred_at >= since_days(days))
    conditions = text_match_conditions(WorkEvent, keywords)
    if conditions:
        query = query.where(or_(*conditions))
    return list(db.scalars(query).all())


def list_extracted_items(
    db: Session,
    *,
    scope: CockpitScope,
    item_type: str,
    limit: int,
    open_only: bool = False,
) -> list[ExtractedItem]:
    query = (
        select(ExtractedItem)
        .where(ExtractedItem.item_type == item_type)
        .order_by(ExtractedItem.created_at.desc())
        .limit(limit)
    )
    query = scope_filter(query, ExtractedItem, scope)
    if open_only:
        query = query.where(ExtractedItem.status.notin_(CLOSED_STATUSES))
    return list(db.scalars(query).all())


def item_to_cockpit_item(item: ExtractedItem) -> CockpitItem:
    return CockpitItem(
        id=str(item.id),
        title=display_extracted_title(item),
        description=item.description,
        status=item.status,
        priority=item.priority,
        owner=item.owner,
        occurred_at=item.created_at.isoformat() if item.created_at else None,
        payload=item.payload or {},
    )


def event_to_cockpit_item(event: WorkEvent) -> CockpitItem:
    return CockpitItem(
        id=str(event.id),
        title=event.title or event.event_type,
        description=short_text(event.content_text, 180),
        status=_event_status(event),
        occurred_at=event.occurred_at.isoformat() if event.occurred_at else None,
        source=event.source,
        event_type=event.event_type,
        payload={"labels": event.labels or []},
    )


def short_text(text: str | None, max_length: int = 120) -> str | None:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if not value:
        return None
    return value if len(value) <= max_length else f"{value[:max_length]}..."


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


def summarize_open_items(label: str, items: list[ExtractedItem]) -> str:
    open_items = [item for item in items if str(item.status or "open").lower() not in CLOSED_STATUSES]
    if not items:
        return f"当前没有已抽取的{label}。"
    return f"当前共 {len(items)} 条{label}，其中开放 {len(open_items)} 条。"


def _event_status(event: WorkEvent) -> str | None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    for key in ("status", "approval_status", "task_status"):
        value = payload.get(key)
        if value:
            return str(value)
    return None
