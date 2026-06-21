from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.entities import WorkEvent
from app.services.tools.evidence import evidence_line, latest_datetime, wants_evidence_detail, evidence_ref


CALENDAR_TERMS = ["calendar", "meeting", "日程", "会议", "开会", "议程", "安排"]


def answer_calendar_question(
    db: Session,
    *,
    company_id: UUID,
    question: str,
    limit: int = 8,
) -> str:
    events = _calendar_events(db, company_id=company_id, question=question, limit=limit)
    if not events:
        return "我还没有读到已同步入库的日程或会议记录。你可以先同步飞书日历/会议，再问今天或本周安排。"

    detail = wants_evidence_detail(question)
    lines = [f"我按已同步日程/会议记录看到 {len(events)} 条相关安排："]
    for event in events[: limit if detail else 4]:
        occurred = event.occurred_at.strftime("%Y-%m-%d %H:%M") if event.occurred_at else "-"
        summary = _short(event.content_text)
        line = f"- {occurred} {event.title or event.event_type}"
        if summary:
            line += f"\n  摘要：{summary}"
        lines.append(line)
    if detail:
        lines.extend(evidence_ref(event, prefix="日程/会议") for event in events[:limit])
    lines.append(evidence_line("已同步日程/会议记录", len(events), latest_at=latest_datetime(events)))
    return "\n".join(lines)


def _calendar_events(db: Session, *, company_id: UUID, question: str, limit: int) -> list[WorkEvent]:
    terms = _keywords(question)
    conditions = []
    for term in terms:
        pattern = f"%{term}%"
        conditions.extend(
            [
                WorkEvent.event_type.ilike(pattern),
                WorkEvent.title.ilike(pattern),
                WorkEvent.content_text.ilike(pattern),
                WorkEvent.business_domain.ilike(pattern),
            ]
        )
    return list(
        db.scalars(
            select(WorkEvent)
            .where(WorkEvent.company_id == company_id)
            .where(or_(*conditions))
            .order_by(WorkEvent.occurred_at.desc())
            .limit(limit)
        ).all()
    )


def _keywords(question: str) -> list[str]:
    dynamic = [term for term in ["今天", "今日", "明天", "本周", "客户", "项目", "评审"] if term in question]
    return [*CALENDAR_TERMS, *dynamic]


def _short(text: str | None, max_length: int = 120) -> str:
    value = " ".join(str(text or "").split())
    if not value:
        return ""
    return value if len(value) <= max_length else f"{value[:max_length]}..."

