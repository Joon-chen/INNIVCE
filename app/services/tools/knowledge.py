from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.entities import MemoryFact, WorkEvent
from app.services.data.resource_sources import EXTERNAL_MAIL_ACCOUNT
from app.services.tools.evidence import combined_evidence_line, evidence_ref, latest_datetime, wants_evidence_detail


PUBLIC_FACT_TYPES = {"people_or_policy", "policy", "process", "knowledge", "faq", "organization"}
BLOCKED_FACT_TYPES = {"compensation", "finance", "customer_or_order"}
BLOCKED_TERMS = ["薪资", "工资", "奖金", "现金流", "客户订单", "回款", "老板邮箱", "私密"]
SENSITIVE_LEVELS = {"sensitive", "confidential", "secret", "private"}
KNOWLEDGE_EVENT_TERMS = ["wiki", "doc", "docx", "document", "knowledge", "知识", "制度", "流程", "规范", "模板"]


def answer_public_knowledge_question(
    db: Session,
    *,
    company_id: UUID,
    question: str,
    limit: int = 6,
) -> str:
    keywords = _question_keywords(question)
    facts = _knowledge_facts(db, company_id=company_id, keywords=keywords, limit=limit)
    events = _knowledge_events(db, company_id=company_id, keywords=keywords, limit=limit)
    detail = wants_evidence_detail(question)

    if not facts and not events:
        return (
            "我没有在已同步的公开知识、流程制度或文档内容里找到可靠答案。"
            "我不会用审批、财务、客户、邮件等敏感数据来补这个问题。"
        )

    lines = [
        "我只按公开知识/流程制度范围回答：",
        f"命中知识事实 {len(facts)} 条、文档事件 {len(events)} 条。",
    ]
    if facts:
        lines.append("知识事实：")
        for fact in facts[: limit if detail else 3]:
            lines.append(f"- [{fact.fact_type}] {fact.subject}: {_short(fact.content)}")
    if events:
        lines.append("相关文档：")
        for event in events[: limit if detail else 3]:
            lines.append(f"- {event.title or event.event_type}: {_short(event.content_text)}")
    if detail:
        lines.append("证据明细：")
        for item in [*facts[:limit], *events[:limit]]:
            lines.append(evidence_ref(item, prefix="公开知识"))
    lines.append("说明：这里不包含老板私有数据、其他群聊、邮件正文或未授权业务数据。")
    lines.append(
        combined_evidence_line(
            [("知识事实", len(facts)), ("文档事件", len(events))],
            latest_at=latest_datetime([*facts, *events]),
        )
    )
    return "\n".join(lines)


def _knowledge_facts(db: Session, *, company_id: UUID, keywords: list[str], limit: int) -> list[MemoryFact]:
    conditions = []
    for keyword in keywords[:8]:
        pattern = f"%{keyword}%"
        conditions.extend(
            [
                MemoryFact.subject.ilike(pattern),
                MemoryFact.content.ilike(pattern),
                MemoryFact.fact_type.ilike(pattern),
            ]
        )
    query = (
        select(MemoryFact)
        .where(MemoryFact.company_id == company_id)
        .where(MemoryFact.fact_type.notin_(BLOCKED_FACT_TYPES))
        .order_by(MemoryFact.updated_at.desc())
        .limit(limit * 3)
    )
    if conditions:
        query = query.where(or_(*conditions))
    rows = list(db.scalars(query).all())
    return [fact for fact in rows if _is_public_fact(fact)][:limit]


def _knowledge_events(db: Session, *, company_id: UUID, keywords: list[str], limit: int) -> list[WorkEvent]:
    conditions = []
    for keyword in [*keywords[:8], *KNOWLEDGE_EVENT_TERMS]:
        pattern = f"%{keyword}%"
        conditions.extend(
            [
                WorkEvent.event_type.ilike(pattern),
                WorkEvent.title.ilike(pattern),
                WorkEvent.content_text.ilike(pattern),
            ]
        )
    query = (
        select(WorkEvent)
        .where(WorkEvent.company_id == company_id)
        .where(WorkEvent.data_classification == "company")
        .where(WorkEvent.business_domain.in_(["knowledge", "doc", "general"]))
        .where(WorkEvent.source_type != EXTERNAL_MAIL_ACCOUNT)
        .where(or_(*conditions))
        .order_by(WorkEvent.occurred_at.desc())
        .limit(limit * 3)
    )
    rows = list(db.scalars(query).all())
    return [event for event in rows if _is_public_knowledge_event(event)][:limit]


def _is_public_fact(fact: MemoryFact) -> bool:
    if str(fact.fact_type or "").lower() in BLOCKED_FACT_TYPES:
        return False
    if str(fact.fact_type or "").lower() not in PUBLIC_FACT_TYPES:
        return False
    return not _contains_blocked_terms(f"{fact.subject} {fact.content}")


def _is_public_knowledge_event(event: WorkEvent) -> bool:
    if str(event.sensitivity or "normal").lower() in SENSITIVE_LEVELS:
        return False
    text = f"{event.event_type or ''} {event.title or ''} {event.content_text or ''}"
    if _contains_blocked_terms(text):
        return False
    lowered = text.lower()
    return any(term.lower() in lowered for term in KNOWLEDGE_EVENT_TERMS)


def _contains_blocked_terms(text: str) -> bool:
    return any(term in text for term in BLOCKED_TERMS)


def _question_keywords(question: str) -> list[str]:
    text = (
        question.replace("，", " ")
        .replace("。", " ")
        .replace("？", " ")
        .replace("?", " ")
        .replace("/", " ")
    )
    stop_words = {"我", "你", "的", "了", "吗", "呢", "怎么", "如何", "一下", "帮我", "看看", "流程", "制度"}
    tokens = [item.strip() for item in text.split() if item.strip()]
    keywords = [token for token in tokens if token not in stop_words and len(token) >= 2]
    if keywords:
        return keywords[:8]
    return [term for term in ["流程", "制度", "规范", "模板", "知识库"] if term in question] or ["流程", "制度", "知识"]


def _short(text: str | None, max_length: int = 140) -> str:
    value = " ".join(str(text or "").split())
    if not value:
        return ""
    return value if len(value) <= max_length else f"{value[:max_length]}..."
