from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import ExtractedItem
from app.services.tools.evidence import evidence_line, latest_datetime


CLOSED_STATUSES = {"closed", "done", "resolved", "completed"}


def answer_task_question(
    db: Session,
    *,
    company_id: UUID,
    limit: int = 10,
) -> str:
    tasks = _open_tasks(db, company_id=company_id, limit=limit)
    if not tasks:
        return "我暂时没有在已抽取数据里看到开放待办。"

    lines = [f"我在已抽取数据里看到 {len(tasks)} 条开放待办："]
    for task in tasks:
        owner = f" / 负责人：{task.owner}" if task.owner else ""
        due = f" / 截止：{task.due_at.isoformat()}" if task.due_at else ""
        priority = f" / 优先级：{task.priority}" if task.priority else ""
        lines.append(f"- {task.title}{owner}{due}{priority}")
    lines.append(evidence_line("已抽取开放待办", len(tasks), latest_at=latest_datetime(tasks)))
    return "\n".join(lines)


def _open_tasks(db: Session, *, company_id: UUID, limit: int) -> list[ExtractedItem]:
    return list(
        db.scalars(
            select(ExtractedItem)
            .where(ExtractedItem.company_id == company_id)
            .where(ExtractedItem.item_type == "task")
            .where(ExtractedItem.status.notin_(CLOSED_STATUSES))
            .order_by(ExtractedItem.created_at.desc())
            .limit(limit)
        ).all()
    )
