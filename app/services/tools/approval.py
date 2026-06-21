import json
import re
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.entities import WorkEvent
from app.services.tools.evidence import evidence_ref, evidence_line, latest_datetime, wants_evidence_detail


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


def answer_approval_question(
    db: Session,
    *,
    company_id: UUID,
    question: str,
    limit: int = 8,
) -> str:
    events = _approval_events(db, company_id=company_id, limit=max(limit * 4, 30))
    if not events:
        return "我还没有读到已同步入库的审批记录。你可以先让系统自动发现并同步审批资源，或直接问“待我审批”。"

    detail = wants_evidence_detail(question)
    if _is_pending_question(question):
        return _pending_approval_answer(events, limit=limit, detail=detail)
    if _is_advice_question(question):
        return _approval_advice_answer(events, limit=limit, detail=detail)
    return _approval_summary_answer(events, limit=limit, detail=detail)


def _approval_events(db: Session, *, company_id: UUID, limit: int) -> list[WorkEvent]:
    return list(
        db.scalars(
            select(WorkEvent)
            .where(WorkEvent.company_id == company_id)
            .where(
                or_(
                    WorkEvent.event_type.ilike("%approval%"),
                    WorkEvent.title.ilike("%审批%"),
                    WorkEvent.content_text.ilike("%审批%"),
                    WorkEvent.business_domain == "approval",
                )
            )
            .order_by(WorkEvent.occurred_at.desc())
            .limit(limit)
        ).all()
    )


def _pending_approval_answer(events: list[WorkEvent], *, limit: int, detail: bool) -> str:
    pending = [event for event in events if _approval_status(event) in PENDING_APPROVAL_STATUSES]
    if not pending:
        completed = [event for event in events if _approval_status(event) in COMPLETED_APPROVAL_STATUSES]
        lines = ["我在已同步审批记录里没有看到明确的未完成审批。"]
        if completed:
            lines.append("最近完成的审批记录：")
            lines.extend(_format_event_lines(completed[: min(3, limit)]))
        lines.append("说明：这里基于本地已同步记录，不等同于飞书实时“待你本人审批”列表。")
        return "\n".join(lines)

    lines = [
        f"我在已同步审批记录里看到 {len(pending)} 条未完成审批实例：",
        "数据源：PostgreSQL WorkEvent（审批同步）。",
        "说明：这里是本地审批实例状态，不保证全都是待你本人审批；精确待办仍以飞书审批任务为准。",
    ]
    lines.extend(_format_event_lines(pending[:limit] if detail else pending[:3]))
    if detail:
        lines.extend(evidence_ref(event, prefix="审批记录") for event in pending[:limit])
    lines.append(evidence_line("已同步审批记录", len(events), latest_at=latest_datetime(events)))
    return "\n".join(lines)


def _approval_advice_answer(events: list[WorkEvent], *, limit: int, detail: bool) -> str:
    candidates = [event for event in events if _approval_status(event) in PENDING_APPROVAL_STATUSES] or events
    lines = ["我先按已同步审批记录给出只读建议：", "数据源：PostgreSQL WorkEvent（审批同步）。"]
    for event in candidates[: limit if detail else 3]:
        amount = _approval_amount(event)
        status = _approval_status(event) or "unknown"
        title = event.title or event.event_type
        if amount is not None and amount >= 30000:
            advice = "金额较高，建议先确认预算归属、用途凭证和项目/客户对应关系。"
        elif amount is not None:
            advice = "金额不高，如事由和凭证完整，可以按常规流程处理。"
        else:
            advice = "本地记录里金额/表单信息不足，建议打开审批明细核实后再处理。"
        lines.append(f"- [{status}] {title}：{advice}")
    lines.append("我不会在这里直接提交审批动作；如需通过或拒绝，仍要走二次确认命令。")
    if detail:
        lines.extend(evidence_ref(event, prefix="审批记录") for event in candidates[:limit])
    lines.append(evidence_line("已同步审批记录", len(events), latest_at=latest_datetime(events)))
    return "\n".join(lines)


def _approval_summary_answer(events: list[WorkEvent], *, limit: int, detail: bool) -> str:
    by_status: dict[str, int] = {}
    for event in events:
        status = _approval_status(event) or "unknown"
        by_status[status] = by_status.get(status, 0) + 1
    lines = [
        f"我在已同步审批记录里看到 {len(events)} 条审批相关事件。",
        "数据源：PostgreSQL WorkEvent（审批同步）。",
        "状态分布：" + "、".join(f"{status} {count} 条" for status, count in by_status.items()),
        "最近记录：",
    ]
    lines.extend(_format_event_lines(events[: limit if detail else 3]))
    if detail:
        lines.extend(evidence_ref(event, prefix="审批记录") for event in events[:limit])
    lines.append(evidence_line("已同步审批记录", len(events), latest_at=latest_datetime(events)))
    return "\n".join(lines)


def _format_event_lines(events: list[WorkEvent]) -> list[str]:
    lines: list[str] = []
    for event in events:
        occurred = event.occurred_at.strftime("%Y-%m-%d %H:%M") if event.occurred_at else "-"
        status = _approval_status(event)
        amount = _approval_amount(event)
        amount_text = f" / 金额：{amount:g}" if amount is not None else ""
        status_text = f" [{status}]" if status else ""
        summary = _short(event.content_text)
        line = f"- {occurred}{status_text} {event.title or event.event_type}{amount_text}"
        if summary:
            line += f"\n  摘要：{summary}"
        lines.append(line)
    return lines


def _approval_status(event: WorkEvent) -> str | None:
    item = _approval_item(event)
    for key in ("status", "instance_status", "approval_status", "task_status"):
        value = item.get(key)
        if value not in (None, "", [], {}):
            return str(value).strip().lower()
    return None


def _approval_amount(event: WorkEvent) -> float | None:
    item = _approval_item(event)
    form_fields = _approval_form_fields(item)
    candidates = [
        item.get("amount"),
        item.get("申请金额"),
        item.get("付款金额"),
        item.get("报销金额"),
        item.get("合同金额"),
        form_fields.get("申请金额"),
        form_fields.get("付款金额"),
        form_fields.get("报销金额"),
        form_fields.get("合同金额"),
        form_fields.get("费用金额"),
        event.content_text,
        event.title,
    ]
    for candidate in candidates:
        amount = _parse_amount(candidate)
        if amount is not None:
            return amount
    return None


def _approval_item(event: WorkEvent) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    item = payload.get("item") if isinstance(payload.get("item"), dict) else payload
    return item if isinstance(item, dict) else {}


def _approval_form_fields(item: dict[str, Any]) -> dict[str, Any]:
    raw_form = item.get("form")
    if raw_form in (None, "", [], {}):
        return {}
    if isinstance(raw_form, str):
        try:
            raw_form = json.loads(raw_form)
        except json.JSONDecodeError:
            return {}
    if not isinstance(raw_form, list):
        return {}

    fields: dict[str, Any] = {}
    for field in raw_form:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        value = field.get("value")
        if value in (None, "", [], {}):
            continue
        fields[name] = value
    return fields


def _parse_amount(value: Any) -> float | None:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    match = re.search(r"(?:金额|付款|报销|合同|费用|￥|¥)?\s*([0-9][0-9,]*(?:\.[0-9]+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _is_pending_question(question: str) -> bool:
    return any(term in question for term in ["待审批", "未审批", "待我", "待你", "未完成", "待处理", "未处理"])


def _is_advice_question(question: str) -> bool:
    return any(term in question for term in ["建议", "该不该", "是否通过", "能不能通过", "同意还是拒绝", "审批建议"])


def _short(text: str | None, max_length: int = 120) -> str:
    value = " ".join(str(text or "").split())
    if not value:
        return ""
    return value if len(value) <= max_length else f"{value[:max_length]}..."
