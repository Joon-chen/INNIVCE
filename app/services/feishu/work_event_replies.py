from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import ExtractedItem, FeishuAppConfig, WorkEvent
from app.services.feishu import approval_resources


PENDING_APPROVAL_STATUSES = {"pending", "todo", "doing", "in_progress", "running"}
COMPLETED_APPROVAL_STATUSES = {
    "approved",
    "rejected",
    "cancelled",
    "canceled",
    "done",
    "completed",
    "processed",
    "terminated",
    "deleted",
}


def approval_events_by_status(
    db: Session,
    app_config: FeishuAppConfig,
    *,
    statuses: set[str],
    limit: int,
) -> list[WorkEvent]:
    events = list(
        db.scalars(
            select(WorkEvent)
            .where(WorkEvent.company_id == app_config.company_id)
            .where(WorkEvent.event_type.ilike("%approvals%"))
            .order_by(WorkEvent.occurred_at.desc())
            .limit(max(limit * 5, 20))
        ).all()
    )
    matched = [event for event in events if approval_instance_status(event) in statuses]
    return matched[:limit]


def recent_approval_events(db: Session, app_config: FeishuAppConfig, *, limit: int) -> list[WorkEvent]:
    return list(
        db.scalars(
            select(WorkEvent)
            .where(WorkEvent.company_id == app_config.company_id)
            .where(WorkEvent.event_type.ilike("%approvals%"))
            .order_by(WorkEvent.occurred_at.desc())
            .limit(limit)
        ).all()
    )


def approval_instance_status(event: WorkEvent | dict[str, Any]) -> str | None:
    return approval_resources.approval_instance_status(event)


def format_approval_event_lines(events: list[WorkEvent]) -> list[str]:
    lines: list[str] = []
    for event in events:
        occurred = event.occurred_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M")
        status = approval_instance_status(event)
        summary = " ".join((event.content_text or "").split())[:160]
        status_text = f" [{status}]" if status else ""
        line = f"- {occurred}{status_text} {event.title or '(无标题)'}"
        if summary:
            line += f"\n  摘要：{summary}"
        lines.append(line)
    return lines


def format_task_time(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return str(value)
    if timestamp > 10_000_000_000:
        timestamp //= 1000
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%d %H:%M")


def safe_command_error(detail: Any) -> str:
    if isinstance(detail, dict):
        body = detail.get("body")
        if isinstance(body, dict):
            message = body.get("msg") or body.get("message")
            if message:
                return str(message)[:300]
        message = detail.get("message") or detail.get("error")
        if message:
            return str(message)[:300]
    return str(detail)[:300]


def recent_mail_reply(db: Session, app_config: FeishuAppConfig, *, limit: int = 5) -> str:
    events = list(
        db.scalars(
            select(WorkEvent)
            .where(WorkEvent.company_id == app_config.company_id)
            .where(
                WorkEvent.source.in_(["imap", "gmail", "graph"])
                | WorkEvent.event_type.ilike("%mail%")
                | WorkEvent.labels.contains(["mail"])
            )
            .order_by(WorkEvent.occurred_at.desc())
            .limit(limit)
        ).all()
    )
    if not events:
        return "还没有同步到邮件。"

    lines = ["最近一封邮件：" if limit == 1 else "最近邮件："]
    for event in events:
        occurred = event.occurred_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M")
        summary = " ".join((event.content_text or "").split())[:240]
        line = f"- {occurred} {event.title or '(无标题)'}"
        if summary:
            line += f"\n  摘要：{summary}"
        lines.append(line)
    return "\n".join(lines)


def open_items_reply(db: Session, app_config: FeishuAppConfig) -> str:
    items = list(
        db.scalars(
            select(ExtractedItem)
            .where(ExtractedItem.company_id == app_config.company_id)
            .where(ExtractedItem.status == "open")
            .order_by(ExtractedItem.created_at.desc())
            .limit(8)
        ).all()
    )
    if not items:
        return "当前没有已抽取的开放待办。你可以先发送“同步邮箱”。"

    lines = ["开放待办："]
    for item in items:
        prefix = {"task": "任务", "risk": "风险", "decision": "决策"}.get(item.item_type, item.item_type)
        lines.append(f"- [{prefix}] {item.title}")
    return "\n".join(lines)
