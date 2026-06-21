from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.entities import WorkEvent
from app.services.cockpit.schemas import CockpitItem, CockpitModuleResult
from app.services.cockpit.scope import CockpitScope
from app.services.cockpit.utils import scope_filter, short_text, since_days


MEETING_TERMS = ("meeting", "calendar", "会议", "日程", "纪要", "会后", "评审")
DECISION_TERMS = ("决策", "拍板", "确认", "同意", "拒绝", "预算", "付款", "合同", "方案评审", "立项")
FOLLOW_UP_TERMS = ("待办", "行动项", "跟进", "负责人", "截止", "会后", "安排", "落实", "推进")
LOW_SIGNAL_TERMS = (
    "同步完成",
    "同步记录",
    "写入或更新",
    "欢迎使用",
    "使用指南",
    "帮助中心",
    "数字参谋系统启动",
    "日历助手",
    "邀请你加入日程",
)


def build_meetings_module(db: Session, *, scope: CockpitScope, limit: int) -> CockpitModuleResult:
    events = _meeting_events(db, scope=scope, limit=max(limit * 4, 50))
    items = _dedupe_meeting_items([_meeting_item(event) for event in events if not _is_low_signal_event(event)])
    decisions = [item for item in items if item.attention_label == "需要决策"]
    follow_ups = [item for item in items if item.attention_label == "会后跟进"]
    records = [item for item in items if item.attention_label == "背景日程"]
    ordered = [*decisions, *follow_ups, *records][:limit]
    return CockpitModuleResult(
        key="meetings",
        name="会议日程",
        description="会议、日程、纪要和会后待跟进事项。",
        count=len(decisions) + len(follow_ups),
        summary=(
            f"最近 90 天会议日程 {len(items)} 条；"
            f"需要决策 {len(decisions)} 条、会后跟进 {len(follow_ups)} 条、背景日程 {len(records)} 条。"
        ),
        items=ordered,
        next_actions=_meeting_next_actions(
            decisions=len(decisions),
            follow_ups=len(follow_ups),
            records=len(records),
        ),
        metrics={
            "meeting_decision": len(decisions),
            "meeting_follow_up": len(follow_ups),
            "meeting_record": len(records),
            "meeting_events": len(items),
        },
    )


def _meeting_events(db: Session, *, scope: CockpitScope, limit: int) -> list[WorkEvent]:
    query = (
        select(WorkEvent)
        .where(WorkEvent.occurred_at >= since_days(90))
        .order_by(WorkEvent.occurred_at.desc())
        .limit(limit)
    )
    query = scope_filter(query, WorkEvent, scope)
    conditions = []
    for term in MEETING_TERMS:
        pattern = f"%{term}%"
        conditions.extend(
            [
                WorkEvent.event_type.ilike(pattern),
                WorkEvent.title.ilike(pattern),
                WorkEvent.content_text.ilike(pattern),
            ]
        )
    return list(db.scalars(query.where(or_(*conditions))).all())


def _dedupe_meeting_items(items: list[CockpitItem]) -> list[CockpitItem]:
    seen: set[str] = set()
    result: list[CockpitItem] = []
    for item in items:
        day = str(item.occurred_at or "")[:10]
        key = f"{item.attention_label}:{item.title}:{item.source or ''}:{day}"
        if key in seen:
            continue
        seen = seen | {key}
        result.append(item)
    return result


def _meeting_item(event: WorkEvent) -> CockpitItem:
    level = _meeting_attention_level(event)
    return CockpitItem(
        id=str(event.id),
        title=event.title or event.event_type or "会议日程",
        attention_label=_meeting_attention_label(level),
        subtitle=_meeting_subtitle(level),
        description=_meeting_next_step(level),
        status=_meeting_status(event),
        priority="high" if level == "meeting_decision" else None,
        occurred_at=event.occurred_at.isoformat() if event.occurred_at else None,
        source=event.source,
        event_type=event.event_type,
        payload={
            "meeting_attention_level": level,
            "meeting_attention_label": _meeting_attention_label(level),
            "summary": short_text(event.content_text, 180),
            **_meeting_payload(event),
        },
    )


def _meeting_attention_level(event: WorkEvent) -> str:
    text = _meeting_text(event)
    if any(term in text for term in DECISION_TERMS):
        return "meeting_decision"
    if any(term in text for term in FOLLOW_UP_TERMS):
        return "meeting_follow_up"
    return "meeting_record"


def _meeting_attention_label(level: str) -> str:
    labels = {
        "meeting_decision": "需要决策",
        "meeting_follow_up": "会后跟进",
        "meeting_record": "背景日程",
    }
    return labels[level]


def _meeting_subtitle(level: str) -> str:
    if level == "meeting_decision":
        return "会议可能涉及方案、预算、合同或关键事项拍板。"
    if level == "meeting_follow_up":
        return "会议后存在行动项、负责人或截止时间，需要确认推进状态。"
    return "普通日程或会议背景，用于后续上下文追踪。"


def _meeting_next_step(level: str) -> str:
    if level == "meeting_decision":
        return "确认会议结论、待拍板事项、责任人和下一步时限。"
    if level == "meeting_follow_up":
        return "检查会后行动项是否有负责人和截止时间，缺失时补齐。"
    return "默认不处理，系统保留为会议上下文。"


def _meeting_next_actions(*, decisions: int, follow_ups: int, records: int) -> list[str]:
    if decisions:
        actions = [f"先处理 {decisions} 条可能需要你拍板的会议事项。"]
        if follow_ups:
            actions.append(f"再检查 {follow_ups} 条会后跟进，确认负责人和截止时间。")
        return actions
    if follow_ups:
        return [
            f"当前没有明显需要老板拍板的会议；有 {follow_ups} 条会后跟进。",
            "建议只检查负责人、截止时间和是否存在阻塞。",
        ]
    if records:
        return [f"当前只有 {records} 条背景日程，默认不需要老板处理。"]
    return ["最近 90 天没有需要展示的会议日程。"]


def _is_low_signal_event(event: WorkEvent) -> bool:
    text = _meeting_text(event)
    if any(term in text for term in DECISION_TERMS + FOLLOW_UP_TERMS):
        return False
    return any(term in text for term in LOW_SIGNAL_TERMS)


def _meeting_status(event: WorkEvent) -> str | None:
    payload = _meeting_payload(event)
    for key in ("status", "state", "meeting_status", "calendar_status"):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            if isinstance(value, dict | list):
                continue
            return str(value)
    return None


def _meeting_payload(event: WorkEvent) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    item = payload.get("item") if isinstance(payload.get("item"), dict) else payload
    return dict(item) if isinstance(item, dict) else {}


def _meeting_text(event: WorkEvent) -> str:
    return " ".join(
        [
            event.event_type or "",
            event.title or "",
            event.content_text or "",
            str(event.labels or ""),
            str(event.payload or ""),
        ]
    )
