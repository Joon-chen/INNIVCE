from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.entities import ExtractedItem, WorkEvent
from app.services.tools.evidence import evidence_line, latest_datetime
from app.services.agent.policies import BotActor


def answer_personal_tasks(
    db: Session,
    *,
    company_id: UUID,
    actor: BotActor,
    limit: int = 10,
) -> str:
    boundary = personal_task_access_boundary(actor)
    if not boundary["has_strong_identity"]:
        return "我还没有拿到你的 Open ID 或邮箱，所以暂时无法安全地只筛选“本人相关”事项。"

    identifiers = boundary["identity_values"]
    tasks = _personal_task_items(db, company_id=company_id, identifiers=identifiers, limit=limit)
    if tasks:
        lines = [f"我按你的身份线索找到 {len(tasks)} 条本人相关开放待办："]
        for task in tasks:
            owner = f" / 负责人：{task.owner}" if task.owner else ""
            due = f" / 截止：{task.due_at.isoformat()}" if task.due_at else ""
            lines.append(f"- {task.title}{owner}{due}")
        lines.append(evidence_line("本人相关开放待办", len(tasks), latest_at=latest_datetime(tasks)))
        return "\n".join(lines)

    events = _personal_events(db, company_id=company_id, identifiers=identifiers, limit=limit)
    if events:
        lines = ["我没有找到正式抽取出的本人待办，但这些工作事件和你有关："]
        for event in events:
            lines.append(f"- {event.title or event.event_type}: {_short(event.content_text)}")
        lines.append(evidence_line("本人相关工作事件", len(events), latest_at=latest_datetime(events)))
        return "\n".join(lines)

    return "我暂时没有找到明确和你本人相关的开放待办。"


def personal_task_access_boundary(actor: BotActor) -> dict[str, object]:
    identity_values = _actor_identifiers(actor)
    strong_keys = [key for key, value in _actor_identity_pairs(actor) if key in {"open_id", "email"} and value]
    return {
        "data_access_scope": "personal",
        "personal_owner_open_id": actor.open_id,
        "identity_keys": [key for key, value in _actor_identity_pairs(actor) if value],
        "identity_values": identity_values,
        "has_strong_identity": bool(strong_keys),
        "strong_identity_keys": strong_keys,
        "cross_user_data_allowed": False,
        "boundary_reason": "personal_tasks filters by the current actor identity only",
    }


def _personal_task_items(
    db: Session,
    *,
    company_id: UUID,
    identifiers: list[str],
    limit: int,
) -> list[ExtractedItem]:
    conditions = []
    for identifier in identifiers:
        pattern = f"%{identifier}%"
        conditions.extend(
            [
                ExtractedItem.owner.ilike(pattern),
                ExtractedItem.title.ilike(pattern),
                ExtractedItem.description.ilike(pattern),
            ]
        )
    return list(
        db.scalars(
            select(ExtractedItem)
            .where(ExtractedItem.company_id == company_id)
            .where(ExtractedItem.item_type == "task")
            .where(ExtractedItem.status.notin_({"closed", "done", "resolved", "completed"}))
            .where(or_(*conditions))
            .order_by(ExtractedItem.created_at.desc())
            .limit(limit)
        ).all()
    )


def _personal_events(
    db: Session,
    *,
    company_id: UUID,
    identifiers: list[str],
    limit: int,
) -> list[WorkEvent]:
    conditions = []
    for identifier in identifiers:
        pattern = f"%{identifier}%"
        conditions.extend(
            [
                WorkEvent.title.ilike(pattern),
                WorkEvent.content_text.ilike(pattern),
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


def _actor_identifiers(actor: BotActor) -> list[str]:
    seen: set[str] = set()
    identifiers: list[str] = []
    for value in [actor.display_name, actor.open_id, actor.email]:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        identifiers.append(text)
    return identifiers


def _actor_identity_pairs(actor: BotActor) -> list[tuple[str, str]]:
    return [
        ("display_name", str(actor.display_name or "").strip()),
        ("open_id", str(actor.open_id or "").strip()),
        ("email", str(actor.email or "").strip()),
    ]


def _short(text: str | None, max_length: int = 100) -> str:
    value = " ".join(str(text or "").split())
    if not value:
        return ""
    return value if len(value) <= max_length else f"{value[:max_length]}..."
