from sqlalchemy.orm import Session

from app.models.entities import ExtractedItem, WorkEvent
from app.services.ai.business_extraction import enrich_extracted_item, extract_business_items
from app.services.ai.openai_service import AIService
from app.services.audit import write_audit_log


def extract_items_for_event(db: Session, event: WorkEvent) -> list[ExtractedItem]:
    service = AIService()
    event_text = event.content_text or ""
    raw_items = _merge_raw_items(service.extract_items(event_text), extract_business_items(event_text))
    items: list[ExtractedItem] = []
    for raw in raw_items:
        raw = enrich_extracted_item(raw, event_text)
        item = ExtractedItem(
            company_id=event.company_id,
            work_event_id=event.id,
            item_type=raw.get("item_type", "task"),
            title=(raw.get("title") or "未命名事项")[:500],
            description=raw.get("description"),
            owner=raw.get("owner"),
            due_at=None,
            priority=raw.get("priority"),
            payload=raw,
        )
        db.add(item)
        items.append(item)
    write_audit_log(
        db,
        action="ai.extract_items",
        company_id=event.company_id,
        target_type="work_event",
        target_id=str(event.id),
        payload={"count": len(items)},
    )
    return items


def _merge_raw_items(primary: list[dict], business_items: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for raw in [*business_items, *primary]:
        key = (str(raw.get("item_type") or "task"), str(raw.get("title") or "").strip())
        if key in seen:
            continue
        seen = seen | {key}
        merged.append(raw)
    return merged
