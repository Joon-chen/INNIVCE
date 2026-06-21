from sqlalchemy.orm import Session

from app.services.cockpit.schemas import CockpitModuleResult
from app.services.cockpit.scope import CockpitScope
from app.services.cockpit.utils import event_to_cockpit_item, list_events_by_keywords


def build_keyword_event_module(
    db: Session,
    *,
    key: str,
    name: str,
    description: str,
    keywords: list[str],
    next_actions: list[str],
    scope: CockpitScope,
    limit: int,
) -> CockpitModuleResult:
    events = list_events_by_keywords(
        db,
        scope=scope,
        keywords=keywords,
        limit=limit,
        days=90,
    )
    return CockpitModuleResult(
        key=key,
        name=name,
        description=description,
        count=len(events),
        summary=_summary(name, events),
        items=[event_to_cockpit_item(event) for event in events[:limit]],
        next_actions=next_actions,
        metrics={"matched_events": len(events)},
    )


def _summary(name: str, events: list[object]) -> str:
    if not events:
        return f"最近 90 天没有命中的{name}。"
    return f"最近 90 天命中 {len(events)} 条{name}，已按时间倒序展示。"
