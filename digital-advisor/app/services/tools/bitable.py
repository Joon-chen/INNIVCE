from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.entities import WorkEvent
from app.services.tools.evidence import evidence_line, evidence_ref, latest_datetime, wants_evidence_detail


BITABLE_TERMS = ["bitable", "base", "多维表格", "数据表", "表格"]


def answer_bitable_question(
    db: Session,
    *,
    company_id: UUID,
    question: str,
    limit: int = 8,
) -> str:
    events = _bitable_events(db, company_id=company_id, question=question, limit=limit)
    if not events:
        return "我还没有读到已同步入库的多维表格数据。你可以先同步飞书 Base/多维表格资源，再问表格里的客户、项目或经营数据。"

    detail = wants_evidence_detail(question)
    lines = [
        f"我按已同步多维表格记录看到 {len(events)} 条相关数据：",
        "数据源：PostgreSQL WorkEvent（多维表格主数据索引）。",
    ]
    for event in events[: limit if detail else 4]:
        occurred = event.occurred_at.strftime("%Y-%m-%d %H:%M") if event.occurred_at else "-"
        summary = _short(event.content_text)
        line = f"- {occurred} {event.title or event.event_type}"
        key_fields = _format_key_fields(event)
        if key_fields:
            line += f"\n  关键字段：{key_fields}"
        if summary and not (key_fields and summary.startswith("{")):
            line += f"\n  摘要：{summary}"
        lines.append(line)
    if detail:
        lines.extend(evidence_ref(event, prefix="多维表格") for event in events[:limit])
    lines.append(evidence_line("已同步多维表格记录", len(events), latest_at=latest_datetime(events)))
    return "\n".join(lines)


def _bitable_events(db: Session, *, company_id: UUID, question: str, limit: int) -> list[WorkEvent]:
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
                WorkEvent.event_type == "feishu.bitable.master_data_index",
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
    dynamic = [term for term in ["客户", "项目", "订单", "合同", "回款", "库存"] if term in question]
    return [*BITABLE_TERMS, *dynamic]


def _short(text: str | None, max_length: int = 120) -> str:
    value = " ".join(str(text or "").split())
    if not value:
        return ""
    return value if len(value) <= max_length else f"{value[:max_length]}..."


def _format_key_fields(event: WorkEvent, *, limit: int = 5) -> str:
    payload = getattr(event, "payload", None)
    payload = payload if isinstance(payload, dict) else {}
    key_fields = payload.get("key_fields")
    if not isinstance(key_fields, dict):
        return ""

    parts: list[str] = []
    for key, value in key_fields.items():
        if value in (None, "", [], {}):
            continue
        formatted_value = _short_value(value)
        if not formatted_value:
            continue
        parts.append(f"{key}={formatted_value}")
        if len(parts) >= limit:
            break
    return "；".join(parts)


def _short_value(value: Any, max_length: int = 80) -> str:
    text = " ".join(str(value).split())
    if text.lower() in {"null", "none", "nan"}:
        return ""
    if text.startswith(("{", "[{", "[\"")):
        return ""
    return text if len(text) <= max_length else f"{text[:max_length]}..."
