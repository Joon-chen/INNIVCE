from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.entities import ExtractedItem, WorkEvent
from app.services.tools.evidence import combined_evidence_line, evidence_ref, latest_datetime, wants_evidence_detail
from app.services.agent.policies import BotActor
from app.services.access_control import principal_from_bot_actor, work_event_access_condition
from app.services.permissions import keywords_for_domains


CLOSED_STATUSES = {"closed", "done", "resolved", "completed"}


def answer_domain_question(
    db: Session,
    *,
    company_id: UUID,
    actor: BotActor,
    question: str,
    limit: int = 8,
) -> str:
    domains = _actor_domains(actor)
    keywords = keywords_for_domains(domains)
    if not keywords:
        return "我还没有拿到你的授权业务域，所以不能按业务域读取公司数据。"

    events = _domain_events(db, company_id=company_id, actor=actor, keywords=keywords, limit=limit)
    risks = _domain_items(db, company_id=company_id, item_type="risk", keywords=keywords, limit=limit)
    tasks = _domain_items(db, company_id=company_id, item_type="task", keywords=keywords, limit=limit)

    domain_text = "、".join(domains)
    if not events and not risks and not tasks:
        return (
            f"我按你的授权业务域（{domain_text}）查了已同步数据，但暂时没有命中相关工作事件、风险或待办。"
            "这不代表飞书实时数据为空，只代表本地库里目前没有匹配记录。"
        )

    detail = wants_evidence_detail(question)
    lines = [
        f"我按你的授权业务域（{domain_text}）读取了已同步数据。",
        f"命中工作事件 {len(events)} 条、开放风险 {len(risks)} 条、开放待办 {len(tasks)} 条。",
    ]
    if detail and risks:
        lines.append("风险：")
        lines.extend(_format_item_lines(risks))
    elif risks:
        lines.append("主要风险：" + "；".join(item.title for item in risks[:3]))
    if detail and tasks:
        lines.append("待办：")
        lines.extend(_format_item_lines(tasks))
    elif tasks:
        lines.append("主要待办：" + "；".join(item.title for item in tasks[:3]))
    if detail and events:
        lines.append("最近工作事件：")
        lines.extend(_format_event_lines(events))
    elif events:
        lines.append("最近事件：" + "；".join((event.title or event.event_type) for event in events[:3]))
    if _is_risk_question(question) and not risks:
        lines.append("补充：你问的是风险，但本地暂未抽取到开放风险，我先列出相关事件供你判断。")
    if detail:
        lines.append("证据明细：")
        for item in [*risks[:3], *tasks[:3], *events[:3]]:
            lines.append(evidence_ref(item, prefix="业务域记录"))
    lines.append(
        combined_evidence_line(
            [("工作事件", len(events)), ("开放风险", len(risks)), ("开放待办", len(tasks))],
            latest_at=latest_datetime([*events, *risks, *tasks]),
        )
    )
    return "\n".join(lines)


def _actor_domains(actor: BotActor) -> list[str]:
    return [domain for domain in actor.domains if domain and domain != "all"]


def _domain_events(db: Session, *, company_id: UUID, actor: BotActor, keywords: list[str], limit: int) -> list[WorkEvent]:
    conditions = _work_event_conditions(keywords)
    if not conditions:
        return []
    principal = principal_from_bot_actor(company_id=company_id, actor=actor)
    query = (
        select(WorkEvent)
        .where(work_event_access_condition(WorkEvent, principal))
        .where(or_(*conditions))
        .order_by(WorkEvent.occurred_at.desc())
        .limit(limit)
    )
    return list(db.scalars(query).all())


def _domain_items(
    db: Session,
    *,
    company_id: UUID,
    item_type: str,
    keywords: list[str],
    limit: int,
) -> list[ExtractedItem]:
    conditions = _extracted_item_conditions(keywords)
    if not conditions:
        return []
    return list(
        db.scalars(
            select(ExtractedItem)
            .where(ExtractedItem.company_id == company_id)
            .where(ExtractedItem.item_type == item_type)
            .where(ExtractedItem.status.notin_(CLOSED_STATUSES))
            .where(or_(*conditions))
            .order_by(ExtractedItem.created_at.desc())
            .limit(limit)
        ).all()
    )


def _work_event_conditions(keywords: list[str]):
    conditions = []
    for keyword in keywords:
        pattern = f"%{keyword}%"
        conditions.extend(
            [
                WorkEvent.title.ilike(pattern),
                WorkEvent.content_text.ilike(pattern),
                WorkEvent.event_type.ilike(pattern),
            ]
        )
    return conditions


def _extracted_item_conditions(keywords: list[str]):
    conditions = []
    for keyword in keywords:
        pattern = f"%{keyword}%"
        conditions.extend(
            [
                ExtractedItem.title.ilike(pattern),
                ExtractedItem.description.ilike(pattern),
                ExtractedItem.owner.ilike(pattern),
            ]
        )
    return conditions


def _format_item_lines(items: list[ExtractedItem]) -> list[str]:
    lines: list[str] = []
    for item in items:
        owner = f" / 负责人：{item.owner}" if item.owner else ""
        due = f" / 截止：{item.due_at.isoformat()}" if item.due_at else ""
        priority = f" / 优先级：{item.priority}" if item.priority else ""
        description = _short(item.description)
        line = f"- {item.title}{owner}{due}{priority}"
        if description:
            line += f"\n  摘要：{description}"
        lines.append(line)
    return lines


def _format_event_lines(events: list[WorkEvent]) -> list[str]:
    lines: list[str] = []
    for event in events:
        occurred = event.occurred_at.strftime("%Y-%m-%d %H:%M") if event.occurred_at else "-"
        summary = _short(event.content_text)
        line = f"- {occurred} {event.title or event.event_type}"
        if summary:
            line += f"\n  摘要：{summary}"
        lines.append(line)
    return lines


def _is_risk_question(question: str) -> bool:
    return any(term in question for term in ["风险", "隐患", "问题", "异常", "逾期"])


def _short(text: str | None, max_length: int = 120) -> str:
    value = " ".join(str(text or "").split())
    if not value:
        return ""
    return value if len(value) <= max_length else f"{value[:max_length]}..."
