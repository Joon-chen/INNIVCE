from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import MemoryFact, WorkEvent


def generate_memory_facts_for_recent_events(db: Session, *, company_id=None, limit: int = 100) -> list[MemoryFact]:
    query = select(WorkEvent).order_by(WorkEvent.occurred_at.desc()).limit(limit)
    if company_id:
        query = query.where(WorkEvent.company_id == company_id)
    created: list[MemoryFact] = []
    for event in db.scalars(query).all():
        if db.scalar(select(MemoryFact.id).where(MemoryFact.source_work_event_id == event.id)):
            continue
        fact = _event_to_memory_fact(event)
        if not fact:
            continue
        db.add(fact)
        created.append(fact)
    db.flush()
    return created


def _event_to_memory_fact(event: WorkEvent) -> MemoryFact | None:
    text = f"{event.title or ''}\n{event.content_text or ''}"
    if not text.strip():
        return None
    fact_type = _classify_fact_type(text, event)
    if not fact_type:
        return None
    subject = event.title or event.event_type
    return MemoryFact(
        company_id=event.company_id,
        fact_type=fact_type,
        subject=subject[:300],
        content=_compact_text(text, 1200),
        confidence="medium",
        source_work_event_id=event.id,
        payload={"source": event.source, "event_type": event.event_type},
    )


def _classify_fact_type(text: str, event: WorkEvent) -> str | None:
    low = text.lower()
    if event.event_type.startswith("feishu.mail") and any(word in text for word in ["工资", "薪资", "奖金"]):
        return "compensation"
    if any(word in text for word in ["销售订单", "订单执行", "客户", "回款"]):
        return "customer_or_order"
    if any(word in text for word in ["财务资金", "资金情况", "现金流"]):
        return "finance"
    if any(word in text for word in ["录用", "入职", "离职", "人事", "制度"]):
        return "people_or_policy"
    if any(word in text for word in ["决定", "确认", "审批", "同意", "拍板"]):
        return "decision"
    if any(word in low for word in ["risk", "todo", "deadline"]) or any(word in text for word in ["风险", "待办", "延期", "阻塞"]):
        return "work_signal"
    return None


def _compact_text(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"
