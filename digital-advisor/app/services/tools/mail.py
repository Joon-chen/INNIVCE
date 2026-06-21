from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.entities import WorkEvent
from app.services.tools.evidence import evidence_line, evidence_ref, latest_datetime, wants_evidence_detail


MAIL_TERMS = ["mail", "email", "邮件", "邮箱", "收件箱", "来信"]


def answer_mail_question(
    db: Session,
    *,
    company_id: UUID,
    question: str,
    limit: int = 8,
) -> str:
    events = _mail_events(db, company_id=company_id, question=question, limit=limit)
    if not events:
        return "我还没有读到已同步入库的邮件记录。你可以先同步邮箱，再问最近邮件或待处理邮件。"

    detail = wants_evidence_detail(question)
    lines = [f"我按已同步邮件记录看到 {len(events)} 条相关邮件："]
    for event in events[: limit if detail else 4]:
        occurred = event.occurred_at.strftime("%Y-%m-%d %H:%M") if event.occurred_at else "-"
        summary = _short(event.content_text)
        line = f"- {occurred} {event.title or event.event_type}"
        if summary:
            line += f"\n  摘要：{summary}"
        lines.append(line)
    if detail:
        lines.extend(evidence_ref(event, prefix="邮件") for event in events[:limit])
    lines.append("说明：邮件通常包含个人或敏感内容，当前只向老板/所有者权限开放。")
    lines.append(evidence_line("已同步邮件记录", len(events), latest_at=latest_datetime(events)))
    return "\n".join(lines)


def _mail_events(db: Session, *, company_id: UUID, question: str, limit: int) -> list[WorkEvent]:
    terms = _keywords(question)
    conditions = []
    for term in terms:
        pattern = f"%{term}%"
        conditions.extend(
            [
                WorkEvent.event_type.ilike(pattern),
                WorkEvent.title.ilike(pattern),
                WorkEvent.content_text.ilike(pattern),
                WorkEvent.source_type.ilike(pattern),
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
    dynamic = [term for term in ["最近", "今天", "客户", "合同", "付款", "项目"] if term in question]
    return [*MAIL_TERMS, *dynamic]


def _short(text: str | None, max_length: int = 120) -> str:
    value = " ".join(str(text or "").split())
    if not value:
        return ""
    return value if len(value) <= max_length else f"{value[:max_length]}..."
