from datetime import datetime
from typing import Any


DETAIL_TERMS = ["展开依据", "展开证据", "来源", "证据", "依据是什么", "具体是哪条", "明细", "详情"]


def wants_evidence_detail(question: str) -> bool:
    return any(term in question for term in DETAIL_TERMS)


def evidence_line(label: str, count: int, *, latest_at: datetime | None = None) -> str:
    time_text = f"，最新时间 {latest_at.strftime('%Y-%m-%d %H:%M')}" if latest_at else ""
    expand_text = "；回复“展开依据”可查看明细"
    return f"依据：{label} {count} 条{time_text}{expand_text}。"


def combined_evidence_line(parts: list[tuple[str, int]], *, latest_at: datetime | None = None) -> str:
    non_empty = [(label, count) for label, count in parts if count > 0]
    if not non_empty:
        return "依据：当前范围内没有命中可引用记录。"
    label = "、".join(f"{name} {count} 条" for name, count in non_empty)
    time_text = f"，最新时间 {latest_at.strftime('%Y-%m-%d %H:%M')}" if latest_at else ""
    return f"依据：{label}{time_text}；回复“展开依据”可查看明细。"


def latest_datetime(items: list[Any], attrs: tuple[str, ...] = ("occurred_at", "updated_at", "created_at")) -> datetime | None:
    values: list[datetime] = []
    for item in items:
        for attr in attrs:
            value = getattr(item, attr, None)
            if isinstance(value, datetime):
                values.append(value)
                break
    return max(values) if values else None


def evidence_ref(item: Any, *, prefix: str = "记录") -> str:
    item_id = str(getattr(item, "id", "") or "")
    short_id = item_id[:8] if item_id else "-"
    title = getattr(item, "title", None) or getattr(item, "subject", None) or getattr(item, "event_type", None) or prefix
    occurred = latest_datetime([item])
    time_text = f" / {occurred.strftime('%Y-%m-%d %H:%M')}" if occurred else ""
    return f"- {prefix} {short_id}{time_text}：{title}"
